from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.http import JsonResponse
from datetime import datetime
from core.utils import get_manager_team
from leaves.utils import not_expired_leaves, approved_on_leave_today

from core.models import User
from leaves.models import LeaveRequest, LeaveBalance, LeaveNotification
from projects.models import Project, ProjectAssignment


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


def _apply_balance_deduction(leave):
    """Only full-day Leave requests draw down a balance. Permission
    requests are hour-based and don't touch the leave balances."""
    if leave.request_type != 'LEAVE':
        return
    balance, _ = LeaveBalance.objects.get_or_create(user=leave.user)
    if leave.leave_type == 'CL':
        balance.cl_balance = max(0, balance.cl_balance - leave.num_days)
    elif leave.leave_type == 'EL':
        balance.el_balance = max(0, balance.el_balance - leave.num_days)
    elif leave.leave_type == 'SICK':
        balance.sick_balance = max(0, balance.sick_balance - leave.num_days)
    balance.save()


@login_required
def my_leaves(request):
    LeaveNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    balance, _ = LeaveBalance.objects.get_or_create(user=request.user)

    other_hrs = None
    if request.user.role in ('HR', 'SENIOR_HR'):
        other_hrs = User.objects.filter(role__in=['HR', 'SENIOR_HR']).exclude(id=request.user.id)

    if request.method == 'POST':
        request_type = request.POST.get('request_type', 'PERMISSION')
        if request_type not in ('PERMISSION', 'LEAVE', 'WFH'):
            request_type = 'PERMISSION'

        leave = LeaveRequest(
            user=request.user,
            request_type=request_type,
            reason=request.POST.get('reason', ''),
        )

        if request_type == 'PERMISSION':
            leave.permission_date = request.POST.get('permission_date') or None
            leave.from_time = request.POST.get('from_time') or None
            leave.to_time = request.POST.get('to_time') or None
            leave.num_hours = request.POST.get('num_hours') or None
            if not leave.permission_date:
                messages.error(request, "Please choose a date for the permission request.")
                return redirect('leaves:my_leaves')
        elif request_type == 'LEAVE':
            leave.leave_type = request.POST.get('leave_type')
            leave.start_date = request.POST.get('start_date') or None
            leave.end_date = request.POST.get('end_date') or None
            if not leave.start_date or not leave.end_date:
                messages.error(request, "Please choose both a start and end date for the leave request.")
                return redirect('leaves:my_leaves')
        else:  # WFH — same date range as Leave, but no leave_type and no
            # balance deduction (see _apply_balance_deduction).
            leave.start_date = request.POST.get('start_date') or None
            leave.end_date = request.POST.get('end_date') or None
            if not leave.start_date or not leave.end_date:
                messages.error(request, "Please choose both a start and end date for the WFH request.")
                return redirect('leaves:my_leaves')

      
        leave.status = leave.initial_status()
        
        # Set escalation fields for PENDING_MANAGER
        if leave.status == 'PENDING_MANAGER':
            leave.current_approver = leave.get_manager()
            from django.utils import timezone
            leave.sla_deadline = timezone.now() + timezone.timedelta(hours=24)
            
        leave.save()
        messages.success(request, f"{leave.get_request_type_display()} request submitted.")
        return redirect('leaves:my_leaves')

    requests_qs = LeaveRequest.objects.filter(user=request.user)
    return render(request, 'leaves/my_leaves.html', {
        'balance': balance, 
        'requests': requests_qs, 
        'other_hrs': other_hrs,
        'leave_types': LeaveRequest.LEAVE_TYPES,
    })


def _team_managed_by(manager):
    return get_manager_team(manager)

@login_required
def leave_approvals(request):
    user = request.user
    today = timezone.localdate()
    context = {'hr_rejected_pending': None, 'on_leave_today': None, 'team_leave_records': None}

    if user.role in ('HR', 'SENIOR_HR'):
        active_branch = getattr(request, 'active_branch', None)

        # HR's actionable queue: Employee requests that have been approved
        # by their manager and forwarded to HR for final approval.
        requests_qs = LeaveRequest.objects.filter(
            status='PENDING_HR'
        ).exclude(user=user).filter(
            Q(target_hr__isnull=True) | Q(target_hr=user)
        ).select_related('user', 'user__profile', 'user__department', 'reviewed_by_manager')
        if active_branch:
            requests_qs = requests_qs.filter(user__branch=active_branch)
        context['requests'] = requests_qs

        # Unified, view-only record of every finalized leave/permission
        # request for employees in this branch — approved or rejected,
        # regardless of date, and regardless of whether it was decided by
        # a department Manager, a Project Manager, or HR itself. HR has no
        # approve/reject authority over Manager/PM-decided requests, but
        # needs full visibility for attendance and payroll tracking.
        team_leave_qs = LeaveRequest.objects.filter(
            status__in=['APPROVED', 'REJECTED']
        ).exclude(user=user).select_related(
            'user', 'user__department', 'reviewed_by_manager', 'reviewed_by_hr'
        )
        if active_branch:
            team_leave_qs = team_leave_qs.filter(user__branch=active_branch)
        context['team_leave_records'] = team_leave_qs.order_by('-applied_at')[:200]

    elif user.role == 'ADMIN':
        active_branch = getattr(request, 'active_branch', None)
        requests_qs = LeaveRequest.objects.select_related(
            'user', 'user__profile', 'user__department', 'reviewed_by_manager', 'reviewed_by_hr', 'target_hr'
        ).all()
        if active_branch:
            requests_qs = requests_qs.filter(user__branch=active_branch)
        context['requests'] = requests_qs
        context['on_leave_today'] = approved_on_leave_today(active_branch)

    elif user.is_cto():
        # The CTO is the Software department's approval authority —
        # same role a department Manager plays elsewhere.
        from django.db.models import Q
        context['requests'] = LeaveRequest.objects.filter(
            Q(current_approver=user) | Q(user__department__name__icontains='software', current_approver__isnull=True),
            status='PENDING_MANAGER'
        ).select_related('user', 'user__profile', 'user__department')

    elif user.role == 'GENERAL_MANAGER':
        # A GM's actionable queue is exclusively their own direct team
        # (production employees / managers who report to them) — never
        # branch-wide, and never another GM's people. PM leave requests
        # always route straight to the COO (see LeaveRequest.initial_status),
        # so a PM never appears in this actionable list either — the GM
        # only gets read-only visibility into those, added below.
        from core.utils import get_manager_team, get_gm_pms
        from django.db.models import Q
        team = get_manager_team(user).exclude(role='PROJECT_MANAGER')
        requests_qs = LeaveRequest.objects.filter(
            Q(current_approver=user) | Q(user__in=team, current_approver__isnull=True),
            status='PENDING_MANAGER'
        ).select_related('user', 'user__profile', 'user__department')
        context['requests'] = requests_qs

        # View-only: status of leave requests raised by the PMs who report
        # to this GM. The GM cannot approve/reject these — only the COO can
        # — but should be able to see whether they were Approved or Rejected.
        pm_ids = get_gm_pms(user).values_list('id', flat=True)
        context['pm_leave_requests'] = LeaveRequest.objects.filter(
            user_id__in=list(pm_ids)
        ).select_related('user', 'user__department').order_by('-applied_at')[:100]

    elif user.is_manager():
        # A Manager's (including a Project Manager's) queue is exclusively
        # their own team — same branch. A Manager's own leave requests
        # never appear here since those route straight to HR.
        team = _team_managed_by(user)
        from django.db.models import Q
        requests_qs = LeaveRequest.objects.filter(
            Q(current_approver=user) | Q(user__in=team, current_approver__isnull=True),
            status='PENDING_MANAGER'
        ).select_related('user', 'user__profile', 'user__department')
        context['requests'] = requests_qs

    elif Project.objects.filter(lead=user).exists() or ProjectAssignment.objects.filter(
        user=user, is_current=True, role_on_project__iexact='Team Leader'
    ).exists():
        # A Project Lead or a Team Leader has no approve/reject authority,
        # but should still be able to see how their team's leave/permission
        # requests are progressing (pending, approved, or rejected — and by
        # whom). A Team Leader's "team" is everyone currently assigned to
        # the same project(s) where they hold the Team Leader role.
        led_project_ids = set(Project.objects.filter(lead=user).values_list('id', flat=True))
        tl_project_ids = set(ProjectAssignment.objects.filter(
            user=user, is_current=True, role_on_project__iexact='Team Leader'
        ).values_list('project_id', flat=True))
        team_ids = ProjectAssignment.objects.filter(
            project_id__in=led_project_ids | tl_project_ids, is_current=True
        ).values_list('user_id', flat=True)
        context['pl_team_records'] = LeaveRequest.objects.filter(
            user_id__in=team_ids
        ).exclude(user=user).select_related(
            'user', 'user__department', 'reviewed_by_manager', 'reviewed_by_hr'
        ).order_by('-applied_at')[:200]

    else:
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    return render(request, 'leaves/approvals.html', context)

@login_required
def review_leave(request, leave_id, decision):
    user = request.user
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    label = leave.get_request_type_display()

    if user.role == 'ADMIN':
        messages.error(request, "Admin has view-only access and cannot approve or reject requests.")
        return redirect('leaves:approvals')

    # ---- Stage: Manager review (Employee's request) — FINAL decision.
    # Only the employee's own Department Manager (same branch) can act
    # here. HR can no longer stand in — a leave request must be
    # approved/rejected by that specific manager. ----
    if leave.status == 'PENDING_MANAGER':
        approving_manager = leave.get_manager()
        if not ((user.is_manager() or user.is_cto()) and approving_manager and approving_manager.id == user.id):
            messages.error(request, "You cannot review this request.")
            return redirect('leaves:approvals')

        leave.reviewed_by_manager = user
        leave.manager_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'PENDING_HR'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by your manager and forwarded to HR.")
            messages.success(request, f"{label} approved for {leave.user} and forwarded to HR.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by your manager.")
            messages.success(request, f"{label} rejected for {leave.user}.")
        return redirect('leaves:approvals')

    # ---- Stage: HR review (Manager's own request, or an HR-self request
    # targeted at this HR colleague) — FINAL decision. ----
    if user.role in ('HR', 'SENIOR_HR') and leave.status == 'PENDING_HR':
        if leave.user_id == user.id:
            messages.error(request, "You cannot approve or reject your own request.")
            return redirect('leaves:approvals')
        if leave.target_hr_id and leave.target_hr_id != user.id:
            messages.error(request, "This request was routed to a different HR colleague.")
            return redirect('leaves:approvals')

        leave.reviewed_by_hr = user
        leave.hr_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'APPROVED'
            leave.save()
            _apply_balance_deduction(leave)
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by HR.")
            messages.success(request, f"{label} approved for {leave.user}.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by HR.")
            messages.success(request, f"{label} rejected for {leave.user}.")
        return redirect('leaves:approvals')

    messages.error(request, "You cannot review this request.")
    return redirect('core:dashboard')

@login_required
def notifications(request):
    notes = list(
        LeaveNotification.objects.filter(user=request.user)
        .select_related('leave_request', 'leave_request__user')[:50]
    )
    LeaveNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'leaves/notifications.html', {'notes': notes})


@login_required
def check_leave_balance(request):
    """API endpoint to return available leave balance for a given type and date range."""
    if request.method == 'GET':
        leave_type = request.GET.get('leave_type', '')
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')

        if not start_date_str or not end_date_str:
            return JsonResponse({'error': 'Missing parameters'}, status=400)

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            if start_date > end_date:
                return JsonResponse({'error': 'Start date after end date'}, status=400)
            num_days = (end_date - start_date).days + 1
        except ValueError:
            return JsonResponse({'error': 'Invalid date format'}, status=400)

        balance, _ = LeaveBalance.objects.get_or_create(user=request.user)
        if leave_type == 'CL':
            available = float(balance.cl_balance)
        elif leave_type == 'EL':
            available = float(balance.el_balance)
        elif leave_type == 'SICK':
            available = float(balance.sick_balance)
        else:
            available = 0

        lop_days = max(0, num_days - available)

        from datetime import date
        current_month = date.today().replace(day=1)
        
        used_qs = LeaveRequest.objects.filter(
            user=request.user,
            request_type='LEAVE',
            leave_type=leave_type,
            status='APPROVED',
            start_date__gte=current_month
        )
        used_this_month = sum(req.num_days for req in used_qs if req.num_days) or 0

        pending_qs = LeaveRequest.objects.filter(
            user=request.user,
            request_type='LEAVE',
            leave_type=leave_type,
            status__startswith='PENDING'
        )
        pending = sum(req.num_days for req in pending_qs if req.num_days) or 0

        return JsonResponse({
            'available_balance': available,
            'used_this_month': used_this_month,
            'pending': pending,
            'requested': num_days,
            'remaining_after_request': max(0, available - num_days),
            'lop_days': lop_days,
        })
    return JsonResponse({'error': 'GET request required'}, status=405)


@login_required
def appreciation_report(request):
    if request.user.role not in ('HR', 'SENIOR_HR', 'GENERAL_MANAGER', 'COO', 'ADMIN'):
        messages.error(request, 'Permission denied.')
        return redirect('core:dashboard')
    import datetime
    from django.db.models import Count, Q
    from projects.models import Project, ProjectAssignment
    from leaves.models import LeaveRequest
    today = datetime.date.today()
    first_of_this = today.replace(day=1)
    last_month_date = first_of_this - datetime.timedelta(days=1)
    month = int(request.GET.get('month', last_month_date.month))
    year = int(request.GET.get('year', last_month_date.year))
    active_branch = getattr(request, 'active_branch', request.user.branch if request.user.role in ('HR', 'SENIOR_HR') else None)
    # Active projects
    projects = Project.objects.filter(status__in=['PENDING', 'APPROVED', 'COMPLETED'])
    if active_branch and not getattr(request.user, 'can_access_all_branches', False):
        projects = projects.filter(Q(manager__branch=active_branch) | Q(created_by__branch=active_branch)).distinct()
    perfect_projects = []
    for project in projects:
        # Get all employees assigned to this project
        team_user_ids = ProjectAssignment.objects.filter(project=project, is_current=True).values_list('user_id', flat=True)
        if not team_user_ids:
            continue
        # Check if any team member took leave in this month/year
        leaves_taken = LeaveRequest.objects.filter(
            user_id__in=team_user_ids,
            status__in=['APPROVED', 'PENDING_HR', 'PENDING_MANAGER'],
            start_date__year=year,
            start_date__month=month
        ).exists()
        if not leaves_taken:
            # No leaves taken by anyone on the team!
            project.team_size = len(team_user_ids)
            perfect_projects.append(project)
    months = [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)]
    years = range(today.year - 2, today.year + 1)
    return render(request, 'leaves/appreciation.html', {
        'perfect_projects': perfect_projects,
        'month': month,
        'year': year,
        'months': months,
        'years': years,
    })

