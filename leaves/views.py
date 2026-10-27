from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.http import JsonResponse
from datetime import datetime
from core.utils import get_manager_team
from leaves.utils import (
    not_expired_leaves, approved_on_leave_today,
    cl_cycle_bounds, pf_leave_quota_for_user, pf_leave_days_remaining_in_cycle,
)

from core.models import User
from core.decorators import hr_only_required
from leaves.models import LeaveRequest, LeaveBalance, LeaveNotification, LeaveCategory
from projects.models import Project, ProjectAssignment


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


def _default_leave_balance(user):
    """PF-applicable employees don't start with the 12 days upfront —
    theirs accrues 1/month via the credit_monthly_cl command, so a brand
    new LeaveBalance row for them starts at 0. Non-PF employees keep the
    old flat-12-upfront behaviour unchanged."""
    profile = getattr(user, 'profile', None)
    if profile and profile.is_pf_applicable:
        return 0
    return 12


def _is_pf_cycle_leave(leave):
    """True when this LEAVE request should use the per-cycle quota model
    instead of the old pooled 12-day balance. PF-applicable employees
    don't have separate CL/EL/Sick pools — whichever category is picked
    on the form draws from the same per-cycle allowance (see
    leaves.utils.pf_leave_quota_for_user)."""
    if leave.request_type != 'LEAVE':
        return False
    profile = getattr(leave.user, 'profile', None)
    return bool(profile and profile.is_pf_applicable)


def _generic_lop_split(user, num_days, exclude_id=None):
    """Pooled-balance-vs-pending LOP split — fallback for non-PF
    employees only. PF employees always use the per-cycle quota model
    (see _compute_cl_lop_split)."""
    balance, _ = LeaveBalance.objects.get_or_create(
        user=user, defaults={'balance': _default_leave_balance(user)}
    )
    pending_qs = LeaveRequest.objects.filter(
        user=user, request_type='LEAVE', status__startswith='PENDING'
    )
    if exclude_id:
        pending_qs = pending_qs.exclude(pk=exclude_id)
    pending = sum((r.num_days or 0) for r in pending_qs)
    effectively_available = max(Decimal('0'), balance.balance - Decimal(pending))
    lop = max(Decimal('0'), Decimal(num_days) - effectively_available)
    cl = Decimal(num_days) - lop
    return cl, lop


def _compute_cl_lop_split(leave, num_days, end_date):
    """Returns (covered_days, lop_days) for a full-day LEAVE request.
    PF-applicable employees get a fixed leave quota per 26th-25th payroll
    cycle (1 day for regular employees/HR, 2 for Manager/GM/PM/COO/CTO —
    see pf_leave_quota_for_user), shared across whatever leave category is
    picked, independent of any banked balance. Non-PF employees fall back
    to the old pooled 12-day balance. `end_date` must be an actual date
    object (a real parsed date, not the raw POST string that may still be
    sitting on leave.end_date at this point)."""
    if _is_pf_cycle_leave(leave):
        cycle_start, cycle_end = cl_cycle_bounds(end_date)
        quota = pf_leave_quota_for_user(leave.user)
        remaining = pf_leave_days_remaining_in_cycle(leave.user, cycle_start, cycle_end, quota, exclude_id=leave.pk)
        covered = min(remaining, Decimal(num_days))
        lop = Decimal(num_days) - covered
        return covered, lop
    return _generic_lop_split(leave.user, num_days, exclude_id=leave.pk)


def _apply_balance_deduction(leave):
    """Only full-day Leave requests draw down a balance. Permission
    requests are hour-based and don't touch the leave balance. For non-PF
    employees the cl_days (covered) portion is deducted from the pooled
    balance — lop_days is unpaid and was never covered by it. For PF
    employees the per-cycle quota check (see _compute_cl_lop_split) is
    the actual entitlement, so their LeaveBalance is no longer the source
    of truth — this still records cl_days there for reference, but
    nothing currently reads it back for them."""
    if leave.request_type != 'LEAVE':
        return
    balance, _ = LeaveBalance.objects.get_or_create(
        user=leave.user, defaults={'balance': _default_leave_balance(leave.user)}
    )
    amount = leave.cl_days if leave.cl_days else Decimal('0')
    balance.balance = max(Decimal('0'), balance.balance - amount)
    balance.save()


@login_required
def my_leaves(request):
    LeaveNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    balance, _ = LeaveBalance.objects.get_or_create(
        user=request.user, defaults={'balance': _default_leave_balance(request.user)}
    )

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
            leave.leave_type = request.POST.get('leave_type') or ''
            if not LeaveCategory.objects.filter(code=leave.leave_type, is_active=True).exists():
                messages.error(request, "Please choose a valid leave category.")
                return redirect('leaves:my_leaves')
            leave.start_date = request.POST.get('start_date') or None
            leave.end_date = request.POST.get('end_date') or None
            if not leave.start_date or not leave.end_date:
                messages.error(request, "Please choose both a start and end date for the leave request.")
                return redirect('leaves:my_leaves')

            try:
                parsed_start = datetime.strptime(leave.start_date, '%Y-%m-%d').date()
                parsed_end = datetime.strptime(leave.end_date, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                messages.error(request, "Invalid start or end date.")
                return redirect('leaves:my_leaves')

            today = timezone.localdate()
            # Rule: no backdated leave requests — start date must be
            # today or later.
            if parsed_start < today:
                messages.error(request, "Leave requests can't be backdated — the start date must be today or later.")
                return redirect('leaves:my_leaves')

            # Note: previously blocked CL requests from reaching into a
            # payroll cycle that "hadn't started yet" (credit not posted).
            # That was tied to the old accrual model (1 CL/month credited
            # into a balance). Under the per-cycle quota model there's no
            # accrual to wait for — remaining quota for any cycle, past or
            # future, is just checked live against that cycle's
            # approved/pending requests — so this restriction no longer
            # applies and has been removed.

            num_days = (parsed_end - parsed_start).days + 1
            leave.cl_days, leave.lop_days = _compute_cl_lop_split(leave, num_days, parsed_end)
        else:  # WFH — same date range as Leave, but no leave_type and no
            # balance deduction (see _apply_balance_deduction).
            leave.start_date = request.POST.get('start_date') or None
            leave.end_date = request.POST.get('end_date') or None
            if not leave.start_date or not leave.end_date:
                messages.error(request, "Please choose both a start and end date for the WFH request.")
                return redirect('leaves:my_leaves')

            try:
                parsed_start = datetime.strptime(leave.start_date, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                messages.error(request, "Invalid start date.")
                return redirect('leaves:my_leaves')

            if parsed_start < timezone.localdate():
                messages.error(request, "WFH requests can't be backdated — the start date must be today or later.")
                return redirect('leaves:my_leaves')

      
        leave.status = leave.initial_status()
        
        # Set escalation fields for PENDING_MANAGER
        if leave.status == 'PENDING_MANAGER':
            leave.current_approver = leave.get_manager()
            leave.sla_deadline = timezone.now() + timezone.timedelta(hours=24)
            
        leave.save()
        messages.success(request, f"{leave.get_request_type_display()} request submitted.")
        return redirect('leaves:my_leaves')

    requests_qs = LeaveRequest.objects.filter(user=request.user)
    return render(request, 'leaves/my_leaves.html', {
        'balance': balance, 
        'requests': requests_qs, 
        'other_hrs': other_hrs,
        'leave_types': [(c.code, c.name) for c in LeaveCategory.objects.filter(is_active=True)],
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
        context['requests'] = LeaveRequest.objects.filter(
            Q(current_approver=user) | Q(
                user__department__name__icontains='software',
                current_approver__isnull=True
            ),
            status='PENDING_MANAGER'
        ).select_related(
            'user', 'user__profile', 'user__department'
        )
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
        # current_approver reflects escalation (e.g. bumped up to a GM
        # after an SLA breach) — it must win over a fresh get_manager()
        # call, which would always resolve back to the original PM/CTO
        # and silently block the escalated approver from acting.
        approving_manager = leave.current_approver or leave.get_manager()
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

        today = timezone.localdate()
        if start_date < today:
            return JsonResponse({'error': "Leave can't be backdated — the start date must be today or later."}, status=400)

        profile = getattr(request.user, 'profile', None)
        if profile and profile.is_pf_applicable:
            # PF-applicable employees don't have separate CL/EL/Sick
            # pools — whichever leave_type was picked draws from the same
            # fixed per-cycle quota (1 day for regular employees/HR, 2
            # for Manager/GM/PM/COO/CTO), independent of any banked
            # balance. This replaces the old pooled-balance-vs-pending
            # calc below, which only applies to non-PF employees now.
            cycle_start, cycle_end = cl_cycle_bounds(end_date)
            quota = pf_leave_quota_for_user(request.user)
            remaining_before = pf_leave_days_remaining_in_cycle(request.user, cycle_start, cycle_end, quota)
            covered_in_request = min(remaining_before, Decimal(num_days))
            lop_in_request = Decimal(num_days) - covered_in_request

            return JsonResponse({
                'is_pf_cl': True,
                'cycle_start': cycle_start.strftime('%d-%m-%Y'),
                'cycle_end': cycle_end.strftime('%d-%m-%Y'),
                'cl_allowed': quota,
                'cl_used_or_pending': float(Decimal(quota) - remaining_before),
                'cl_in_request': float(covered_in_request),
                'lop_in_request': float(lop_in_request),
                'requested': num_days,
            })

        balance, _ = LeaveBalance.objects.get_or_create(
            user=request.user, defaults={'balance': _default_leave_balance(request.user)}
        )
        available = float(balance.balance)

        current_month = today.replace(day=1)

        used_qs = LeaveRequest.objects.filter(
            user=request.user,
            request_type='LEAVE',
            status='APPROVED',
            start_date__gte=current_month
        )
        used_this_month = sum(req.num_days for req in used_qs if req.num_days) or 0

        pending_qs = LeaveRequest.objects.filter(
            user=request.user,
            request_type='LEAVE',
            status__startswith='PENDING'
        )
        pending = sum(req.num_days for req in pending_qs if req.num_days) or 0

        # LOP (loss-of-pay) days also need to account for already-pending
        # requests, not just this one — otherwise it under-counts how much
        # of the requested leave would actually go unpaid once pending
        # requests are approved too.
        effectively_available = max(0, available - pending)
        lop_days = max(0, num_days - effectively_available)

        return JsonResponse({
            'is_pf_cl': False,
            'available_balance': available,
            'used_this_month': used_this_month,
            'pending': pending,
            'requested': num_days,
            # Previously ignored `pending` entirely (just available - num_days),
            # which let someone request more leave than they actually have
            # left once their already-pending requests get approved too.
            'remaining_after_request': max(0, available - pending - num_days),
            'lop_days': lop_days,
        })
    return JsonResponse({'error': 'GET request required'}, status=405)


@hr_only_required
def leave_categories(request):
    """HR manages the leave categories shown on the Apply for Leave form:
    add, rename, reorder, hide/show, or delete (only if never used)."""
    if request.method == 'POST':
        action = request.POST.get('action')
        name = (request.POST.get('name') or '').strip()

        if action == 'add':
            if not name:
                messages.error(request, "Enter a category name.")
            elif LeaveCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, f"'{name}' already exists.")
            else:
                last = LeaveCategory.objects.order_by('-sort_order').first()
                LeaveCategory.objects.create(name=name, sort_order=(last.sort_order + 1) if last else 1)
                messages.success(request, f"Leave category '{name}' added.")
        else:
            cat = get_object_or_404(LeaveCategory, id=request.POST.get('category_id'))
            if action == 'edit':
                try:
                    order = int(request.POST.get('sort_order') or cat.sort_order)
                except ValueError:
                    order = cat.sort_order
                if not name:
                    messages.error(request, "Category name can't be empty.")
                elif LeaveCategory.objects.filter(name__iexact=name).exclude(id=cat.id).exists():
                    messages.error(request, f"'{name}' already exists.")
                else:
                    cat.name, cat.sort_order = name, max(order, 0)
                    cat.save()
                    messages.success(request, f"Category updated to '{cat.name}'.")
            elif action == 'toggle':
                cat.is_active = not cat.is_active
                cat.save()
                messages.success(
                    request,
                    f"'{cat.name}' is now {'visible' if cat.is_active else 'hidden'} on the Apply form."
                )
            elif action == 'delete':
                if cat.request_count:
                    messages.error(
                        request,
                        f"'{cat.name}' has {cat.request_count} leave request(s) and can't be deleted. "
                        f"Hide it instead."
                    )
                else:
                    cat.delete()
                    messages.success(request, f"Category '{cat.name}' deleted.")
        return redirect('leaves:categories')

    return render(request, 'leaves/categories.html', {'categories': LeaveCategory.objects.all()})


@login_required
def appreciation_report(request):
    if request.user.role not in ('HR', 'SENIOR_HR', 'GENERAL_MANAGER', 'COO', 'ADMIN'):
        messages.error(request, 'Permission denied.')
        return redirect('core:dashboard')
    import calendar
    import datetime
    from django.db.models import Count, Q
    from projects.models import Project, ProjectAssignment
    from leaves.models import LeaveRequest
    from attendance.models import AttendanceRecord

    today = datetime.date.today()
    first_of_this = today.replace(day=1)
    last_month_date = first_of_this - datetime.timedelta(days=1)
    month = int(request.GET.get('month', last_month_date.month))
    year = int(request.GET.get('year', last_month_date.year))

    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month, calendar.monthrange(year, month)[1])
    # Don't hold today's/future's un-punched days against anyone if the
    # selected month is still in progress.
    if last_day > today:
        last_day = today

    active_branch = getattr(request, 'active_branch', request.user.branch if request.user.role in ('HR', 'SENIOR_HR') else None)
    # Active projects — filter by whichever branch is currently selected,
    # same as every other HR report (dashboard, leave stats, etc). Full
    # cross-branch access only matters for *whether you can switch*, not
    # for bypassing the switcher once a branch is selected.
    projects = Project.objects.filter(status__in=['PENDING', 'APPROVED', 'COMPLETED'])
    if active_branch:
        projects = projects.filter(Q(manager__branch=active_branch) | Q(created_by__branch=active_branch)).distinct()

    perfect_projects = []
    for project in projects:
        # Get all employees assigned to this project
        team_user_ids = list(ProjectAssignment.objects.filter(project=project, is_current=True).values_list('user_id', flat=True))
        if not team_user_ids:
            continue
        if first_day > last_day:
            # Selected month hasn't started yet - nothing to appreciate.
            continue

        # 1) Nobody on the team has a leave/permission/WFH request touching
        #    this month.
        leaves_taken = LeaveRequest.objects.filter(
            user_id__in=team_user_ids,
            status__in=['APPROVED', 'PENDING_HR', 'PENDING_MANAGER'],
            start_date__year=year,
            start_date__month=month
        ).exists()
        if leaves_taken:
            continue

        # 2) Everybody actually punched in on every working day (Mon-Sat)
        #    up to today. A missing LeaveRequest row only proves they didn't
        #    ask for leave - it does NOT prove they showed up and worked.
        punched_days = set(
            AttendanceRecord.objects.filter(
                user_id__in=team_user_ids,
                date__gte=first_day, date__lte=last_day,
                in_time__isnull=False,
            ).values_list('user_id', 'date')
        )
        join_dates = dict(
            User.objects.filter(id__in=team_user_ids).values_list('id', 'date_joined_company')
        )

        all_present = True
        for user_id in team_user_ids:
            start = join_dates.get(user_id) or first_day
            d = max(first_day, start)
            while d <= last_day:
                if d.weekday() != 6 and (user_id, d) not in punched_days:  # skip Sundays
                    all_present = False
                    break
                d += datetime.timedelta(days=1)
            if not all_present:
                break
        if not all_present:
            continue

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

