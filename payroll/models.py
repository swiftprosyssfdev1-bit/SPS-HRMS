from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils import timezone


class SalaryStructure(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='salary_structure')
    basic = models.DecimalField(max_digits=10, decimal_places=2)
    hra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    conveyance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Flat monthly bonus, added on top of earned gross, not prorated by attendance.")
    pf_applicable = models.BooleanField(default=True)
    pf_percent = models.DecimalField(max_digits=4, decimal_places=2, default=12.00)

    @property
    def gross(self):
        return self.basic + self.hra + self.conveyance + self.other_allowances

    def __str__(self):
        return f"Salary structure - {self.user}"


class PayrollRun(models.Model):
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='payroll_runs')

    class Meta:
        # DB-level guarantee that one pay cycle (e.g. 26-Jan to 25-Feb)
        # can never have two PayrollRun rows — get_or_create() alone only
        # protects against duplicates within a single request; without
        # this constraint, two near-simultaneous requests (e.g. someone
        # crediting one employee while "Run for All" is mid-flight) could
        # each pass the get_or_create() existence check before either has
        # committed, and create two separate runs for the same cycle,
        # splitting that month's payslips across them.
        constraints = [
            models.UniqueConstraint(fields=['start_date', 'end_date'], name='payroll_run_unique_cycle'),
        ]

    def __str__(self):
        return f"Payroll {self.start_date} - {self.end_date}"


class Payslip(models.Model):
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payslips')

    # Snapshot of the salary structure at the time this payslip was computed.
    basic = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    conveyance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    days_present = models.IntegerField(default=0)
    total_hours_worked = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    lop_days = models.IntegerField(default=0, verbose_name="LOP Days")

    gross_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="EPF & ESIC")
    tds = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="TDS")
    p_tax_lwf = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="P. Tax / LWF")
    bonus_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Bonus (Deduction/Recovery)")
    other_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    generated_at = models.DateTimeField(auto_now_add=True)
    pdf_file = models.FileField(upload_to='payslips/', null=True, blank=True)

    # Release workflow: HR must explicitly release a payslip before the
    # employee/manager can see or download it.
    is_released = models.BooleanField(default=False)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='payslips_released')
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('payroll_run', 'user')

    def __str__(self):
        return f"Payslip - {self.user} ({self.payroll_run})"


class AllowanceCycle(models.Model):
    """A monthly batch of allowance payments (Attendance Bonus, Travel,
    Internet, Incentive, Night Shift), separate from the SalaryStructure/
    Payslip payroll run. Exported via utils.generate_allowance_excel()."""
    year = models.IntegerField()
    month = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-month']
        unique_together = ('year', 'month')

    def __str__(self):
        return f"Allowance Cycle {self.month:02d}-{self.year}"


class Allowance(models.Model):
    """A single employee's allowance line-item within an AllowanceCycle."""
    cycle = models.ForeignKey(AllowanceCycle, on_delete=models.CASCADE, related_name='allowances')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='allowances')

    project = models.CharField(max_length=150, blank=True)
    attendance_bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    travel_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    internet_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    incentive = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    night_shift_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    account_no = models.CharField(max_length=30, blank=True)
    ifsc = models.CharField(max_length=15, blank=True)
    cleared_from_bank = models.CharField(max_length=100, blank=True)
    cleared_date = models.DateField(null=True, blank=True)
    ref_no = models.CharField(max_length=50, blank=True)
    remarks = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='allowances_created')

    class Meta:
        ordering = ['user__first_name']
        unique_together = ('cycle', 'user')

    @property
    def grand_total(self):
        return (
            self.attendance_bonus + self.travel_allowance + self.internet_allowance
            + self.incentive + self.night_shift_allowance
        )

    def __str__(self):
        return f"Allowance - {self.user} ({self.cycle})"


class AllowanceRequest(models.Model):
    """An employee's request to be paid an allowance, with proof attached.
    Employees in a Production department route through their Project
    Manager first; everyone else goes straight to HR. Once HR approves,
    the amount is added into the matching field of that month's Allowance
    line-item (creating the AllowanceCycle/Allowance rows if needed) so it
    shows up on the Allowances screen and, eventually, the payslip.
    """

    TYPE_ATTENDANCE = 'ATTENDANCE_BONUS'
    TYPE_TRAVEL = 'TRAVEL'
    TYPE_INTERNET = 'INTERNET'
    TYPE_INCENTIVE = 'INCENTIVE'
    TYPE_NIGHT_SHIFT = 'NIGHT_SHIFT'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [
        (TYPE_ATTENDANCE, 'Attendance Bonus'),
        (TYPE_TRAVEL, 'Travel Allowance'),
        (TYPE_INTERNET, 'Internet Allowance'),
        (TYPE_INCENTIVE, 'Incentive'),
        (TYPE_NIGHT_SHIFT, 'Night Shift Allowance'),
    ]

    # Maps a request type onto the Allowance model field it should be
    # credited to once HR approves it. TYPE_OTHER has no matching
    # Allowance field, so it is only reflected in remarks (an "other
    # earnings" style entry HR can move into the payslip manually).
    TYPE_TO_ALLOWANCE_FIELD = {
        TYPE_ATTENDANCE: 'attendance_bonus',
        TYPE_TRAVEL: 'travel_allowance',
        TYPE_INTERNET: 'internet_allowance',
        TYPE_INCENTIVE: 'incentive',
        TYPE_NIGHT_SHIFT: 'night_shift_allowance',
    }

    STATUS_PENDING_PM = 'PENDING_PM'
    STATUS_PENDING_HR = 'PENDING_HR'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDING_PM, 'Pending Project Manager Approval'),
        (STATUS_PENDING_HR, 'Pending HR Approval'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='allowance_requests')
    allowance_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_ATTENDANCE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    year = models.IntegerField()
    month = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)

    proof = models.FileField(
        upload_to='allowance_proofs/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        help_text="Upload proof as PDF, JPG or PNG.",
    )

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING_HR)
    submitted_at = models.DateTimeField(auto_now_add=True)

    reviewed_by_pm = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='allowance_requests_pm_reviewed'
    )
    pm_reviewed_at = models.DateTimeField(null=True, blank=True)
    pm_remarks = models.CharField(max_length=255, blank=True)

    reviewed_by_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='allowance_requests_hr_reviewed'
    )
    hr_reviewed_at = models.DateTimeField(null=True, blank=True)
    hr_remarks = models.CharField(max_length=255, blank=True)

    # Set once this request has been folded into an Allowance line-item.
    applied_allowance = models.ForeignKey(
        Allowance, null=True, blank=True, on_delete=models.SET_NULL, related_name='source_requests'
    )

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Allowance request - {self.user} ({self.get_allowance_type_display()}, {self.status})"

    @property
    def is_production(self):
        """True if this employee belongs to a department whose name
        contains 'Production' — those requests must be reviewed by their
        Project Manager before HR sees them."""
        dept = self.user.department
        return bool(dept and 'production' in dept.name.strip().lower())

    def get_pm(self):
        """The Project Manager who should review this request: whoever
        manages the project this employee is currently assigned to. Falls
        back to the department manager if there's no active project
        assignment with a manager set."""
        from projects.models import ProjectAssignment

        assignment = ProjectAssignment.objects.filter(
            user=self.user, is_current=True,
            project__status__in=['PENDING', 'APPROVED', 'COMPLETED'],
        ).select_related('project__manager').order_by('-project__start_date').first()

        if assignment and assignment.project.manager and assignment.project.manager_id != self.user_id:
            return assignment.project.manager

        dept = self.user.department
        if dept and dept.manager_id and dept.manager_id != self.user_id:
            return dept.manager

        return None

    def initial_status(self):
        if self.is_production and self.get_pm():
            return self.STATUS_PENDING_PM
        return self.STATUS_PENDING_HR

    def apply_to_allowance(self, hr_user):
        """Credit the approved amount into this month's Allowance row for
        the requester, creating the AllowanceCycle/Allowance rows if this
        is the first request applied for that cycle."""
        cycle, _ = AllowanceCycle.objects.get_or_create(year=self.year, month=self.month)
        allowance, _ = Allowance.objects.get_or_create(
            cycle=cycle, user=self.user, defaults={'created_by': hr_user}
        )

        field = self.TYPE_TO_ALLOWANCE_FIELD.get(self.allowance_type)
        if field:
            setattr(allowance, field, (getattr(allowance, field) or 0) + self.amount)
        else:
            note = f"+{self.amount} {self.get_allowance_type_display()} (request #{self.pk})"
            allowance.remarks = f"{allowance.remarks} {note}".strip() if allowance.remarks else note

        allowance.save()
        self.applied_allowance = allowance
        return allowance
