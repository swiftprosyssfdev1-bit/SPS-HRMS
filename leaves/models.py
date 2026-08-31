from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model


class LeaveBalance(models.Model):
    """Combined leave pool — SwiftProsys policy is 12 days/year usable for
    any reason (no separate CL/EL/Sick split). Was previously 3 separate
    fields (cl_balance/el_balance/sick_balance); collapsed into one.

    PF-applicable employees (EmployeeProfile.is_pf_applicable) don't get
    the 12 upfront — their CL accrues 1 day/month, credited only once
    that month has started (see credit_monthly_cl command). Unused CL
    carries forward — it's never reset to 0 at month/year end. Non-PF
    employees are unaffected and keep the full 12 available from day one."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_balance')
    balance = models.DecimalField(max_digits=4, decimal_places=1, default=12)
    # First-of-month date of the last month this user's PF-accrual was
    # credited for. Null means never credited (e.g. non-PF employees, or
    # a PF employee who hasn't had the command run for them yet).
    last_credited_month = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Leave balance - {self.user}"


class LeaveRequest(models.Model):
    """Covers both short 'Permission' requests (a few hours on a given day)
    and full-day 'Leave' requests. Both follow the same approval pipeline;
    only the fields captured on submission differ."""

    REQUEST_TYPES = [('PERMISSION', 'Permission'), ('LEAVE', 'Leave'), ('WFH', 'Work From Home')]
    LEAVE_TYPES = [('CL', 'Casual Leave'), ('EL', 'Earned Leave'), ('SICK', 'Sick Leave')]

    STATUS_CHOICES = [
            ('PENDING_MANAGER', 'Pending Manager Approval'),
            ('PENDING_HR', 'Pending HR Approval'),
            ('PENDING_COO', 'Pending COO Approval'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
            ('HR_REJECTED_PENDING_MANAGER', 'HR Declined - Awaiting Manager'),
        ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    request_type = models.CharField(max_length=10, choices=REQUEST_TYPES, default='PERMISSION')

    # ---- Leave-only fields ----
    leave_type = models.CharField(max_length=10, choices=LEAVE_TYPES, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # ---- Permission-only fields ----
    permission_date = models.DateField(null=True, blank=True)
    from_time = models.TimeField(null=True, blank=True)
    to_time = models.TimeField(null=True, blank=True)
    num_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    reason = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_MANAGER')
    applied_at = models.DateTimeField(auto_now_add=True)
    
    current_approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_pending_action')
    sla_deadline = models.DateTimeField(null=True, blank=True)
    is_escalated = models.BooleanField(default=False)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.IntegerField(default=0)

    reviewed_by_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_manager_reviewed'
    )
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)

    reviewed_by_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_hr_reviewed'
    )
    hr_reviewed_at = models.DateTimeField(null=True, blank=True)

    # Only set when an HR user submits their own request — which OTHER HR
    # colleague should review it. Never the requester themselves.
    target_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_targeted_as_hr'
    )

    class Meta:
        ordering = ['-applied_at']

    @property
    def num_days(self):
        if self.request_type in ('LEAVE', 'WFH') and self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return None

    def get_monthly_leave_stats(self):
        if self.request_type != 'LEAVE' or not self.start_date:
            return 0, 0
        qs = LeaveRequest.objects.filter(
            user=self.user,
            request_type='LEAVE',
            start_date__year=self.start_date.year,
            start_date__month=self.start_date.month
        ).exclude(status__in=['REJECTED', 'HR_REJECTED_PENDING_MANAGER'])
        
        count = 0
        total_days = 0
        for r in qs:
            d = r.num_days
            if d:
                count += 1
                total_days += d
        return count, total_days

    @property
    def is_long_leave(self):
        """True for any LEAVE request exceeding 3 days, OR multiple leaves totaling >3 days in a month.
        WFH/Permission requests never trigger this — only actual Leave."""
        if self.request_type != 'LEAVE':
            return False

        days = self.num_days
        if days and days > 3:
            return True
            
        if self.id:  # only check if saved
            count, total_days = self.get_monthly_leave_stats()
            if count >= 2 and total_days > 3:
                return True
                
        return False

    @property
    def long_leave_warning(self):
        if not self.is_long_leave:
            return ''
            
        name = self.user.get_full_name() or self.user.username
        days = self.num_days
        
        if days and days > 3:
            return (
                f"Employee {name} has applied for {days} days of leave, "
                f"which exceeds the 3-day limit. Please review this request before approval."
            )
            
        count, total_days = self.get_monthly_leave_stats()
        return (
            f"Employee {name} has applied for leave {count} times this month, "
            f"totaling {total_days} days. Please review this pattern before approval."
        )

    @property
    def requires_manager_finalization(self):
        """True only when an actual manager approval happened before this
        reached HR — i.e. a regular employee's request that passed through
        the manager stage. Manager's-own and HR's-own requests skip the
        manager stage entirely, so this stays False for them."""
        return self.reviewed_by_manager_id is not None
    
    def get_manager(self):
        """The manager who should review this. A Project Manager assignment
        takes priority: if this employee is currently on a project led by a
        Project Manager, their leave/permission requests go straight to that
        PM. Otherwise falls back to the department manager or direct manager
        link, same as before. Manager-level requests always skip straight to
        HR (see initial_status), so this only matters for Employees.

        Employees in the Software department always route to the CTO
        (Software's departmental head), regardless of project assignment —
        the CTO is explicitly in charge of that department end-to-end."""
        if self.user.is_software_dept():
            User = get_user_model()
            cto = User.objects.filter(role='CTO').first()
            if cto and cto.id != self.user_id:
                return cto

        from projects.models import ProjectAssignment

        assignment = ProjectAssignment.objects.filter(
            user=self.user, is_current=True,
            project__status__in=['PENDING', 'APPROVED', 'COMPLETED'],
        ).select_related('project__manager').order_by('-project__start_date').first()

        if assignment and assignment.project.manager and assignment.project.manager_id != self.user_id:
            return assignment.project.manager

        dept = self.user.department
        candidate = None
        if dept and dept.manager_id and dept.manager_id != self.user_id:
            candidate = dept.manager
        elif self.user.manager_id and self.user.manager.is_manager():
            candidate = self.user.manager
        elif dept:
            # Department.manager was never set in Admin for this department
            # — fall back to any Manager-role user who shares the same
            # department + branch (the same convention the Directory and
            # old Department Staff page use) instead of leaving this
            # employee's requests with no manager to review at all.
            User = get_user_model()
            candidate = User.objects.filter(
                department=dept, branch=self.user.branch, role='MANAGER'
            ).exclude(id=self.user_id).first()

        if candidate:
            return candidate

        # Fallback to Branch Manager if the employee has no other managers
        if self.user.branch_id and self.user.branch.manager_id and self.user.branch.manager_id != self.user_id:
            return self.user.branch.manager

        return None
    
    def initial_status(self):
        if self.user.role == 'COO':
            return 'APPROVED'  # nobody above the COO to review
        if self.user.role == 'CTO':
            return 'PENDING_COO'  # CTO answers directly to the COO
        if self.user.role in ('GENERAL_MANAGER', 'MANAGER', 'PROJECT_MANAGER', 'HR', 'SENIOR_HR', 'ADMIN'):
            return 'PENDING_COO'
        # Regular employee: route to their manager if one can actually be
        # resolved (Software dept -> CTO, department head, direct manager
        # link, or current PM project assignment). If none exists, fall
        # back straight to HR instead of leaving the request stuck at
        # PENDING_MANAGER where nobody can act on it.
        if not self.get_manager():
            return 'PENDING_HR'
        return 'PENDING_MANAGER'


    @property
    def pending_approver_label(self):
        """Human-readable label for PENDING_MANAGER status — shows the
        actual approver role so the employee isn't confused by a generic
        'Pending Manager' when their request is actually waiting on the CTO."""
        if self.status != 'PENDING_MANAGER':
            return self.get_status_display()
        if self.user.is_software_dept():
            return 'Pending CTO'
        manager = self.get_manager()
        if manager:
            role = manager.get_role_display()
            if manager.role == 'MANAGER' and manager.department and manager.department.is_qa:
                role = 'Quality Analyst Manager'
            return f'Pending {role}'
        return 'Pending Manager'

    @property
    def month_leave_summary(self):
        """Returns this employee's LEAVE (not WFH/Permission) records for
        the same calendar month as this request (excluding this request
        itself), so the approver can see their leave count."""
        ref_date = self.start_date or self.permission_date
        if not ref_date:
            return []
        qs = LeaveRequest.objects.filter(
            user=self.user,
            request_type='LEAVE',          # Leave only — no WFH, no Permission
            status__in=['APPROVED', 'PENDING_MANAGER', 'PENDING_HR', 'PENDING_COO'],
        ).exclude(pk=self.pk)

        month_qs = []
        for lr in qs:
            if lr.start_date and lr.start_date.year == ref_date.year and lr.start_date.month == ref_date.month:
                month_qs.append(lr)
        return month_qs

    @property
    def month_leave_days(self):
        """Total number of leave days from month_leave_summary."""
        return sum(lr.num_days or 0 for lr in self.month_leave_summary)

    def get_request_type_display_full(self):
                return self.get_request_type_display()
        
    def __str__(self):
                return f"{self.user} - {self.get_request_type_display()} ({self.status})"

class LeaveNotification(models.Model):
    """A simple inbox entry telling a user about a decision made on a
    permission/leave request — used to satisfy the various 'notify the
    employee' / 'notify the manager' requirements in the approval flow."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_notifications')
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.user}: {self.message}"



class LeaveEscalationHistory(models.Model):
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='escalations')
    original_approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='escalations_from')
    current_approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='escalations_to')
    escalation_level = models.IntegerField(default=1)
    escalation_reason = models.CharField(max_length=255)
    submitted_timestamp = models.DateTimeField()
    sla_deadline = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
