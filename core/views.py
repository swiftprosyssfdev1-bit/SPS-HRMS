from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta

from core.forms import StyledAuthenticationForm
from attendance.models import AttendanceRecord
from leaves.models import LeaveRequest
from employees.models import EmployeeProfile
from django.contrib import messages
from core.utils import user_can_switch_branch, get_manager_team, get_gm_scope
from core.models import Branch, User


def _month_to_date_absent_days(user_ids, branch, month_start, end_date):
    """Join-date-aware, Sunday-excluded month-to-date Absent count.

    For each employee, only counts days from max(month_start, their own
    date_joined_company) through end_date, skipping Sundays (the app's
    existing weekly-off convention â€” see attendance/utils.py's WKD calc).
    A day counts as Absent only if there's no attendance punch AND no
    approved leave for that employee on that day.

    Note: this app has no Holiday model, so company holidays beyond
    Sunday can't be auto-excluded here â€” only join date and Sunday are
    accounted for.
    """
    from leaves.utils import approved_on_leave_for_date

    if not user_ids:
        return 0

    joined_map = dict(
        User.objects.filter(id__in=user_ids).values_list('id', 'date_joined_company')
    )

    present_pairs = set(
        AttendanceRecord.objects.filter(
            date__gte=month_start, date__lte=end_date,
            in_time__isnull=False, user_id__in=user_ids
        ).values_list('user_id', 'date')
    )

    on_leave_pairs = set()
    day = month_start
    while day <= end_date:
        for r in approved_on_leave_for_date(day, branch):
            if r.user_id in user_ids:
                on_leave_pairs.add((r.user_id, day))
        day += timedelta(days=1)

    absent_count = 0
    for uid in user_ids:
        effective_start = month_start
        joined = joined_map.get(uid)
        if joined and joined > effective_start:
            effective_start = joined
        if effective_start > end_date:
            continue  # not yet joined within this range at all

        day = effective_start
        while day <= end_date:
            if day.weekday() != 6:  # skip Sunday
                if (uid, day) not in present_pairs and (uid, day) not in on_leave_pairs:
                    absent_count += 1
            day += timedelta(days=1)

    return absent_count


def _leave_category_breakdown(user_ids, month_start, end_date):
    """Total approved LEAVE days taken in [month_start, end_date] by the
    given users, bucketed by which approval chain each employee falls
    under: Software dept -> CTO, QA dept -> QA Manager, everyone else
    (Production / default) -> GM. Powers the leave-analytics pie chart
    on the HR/GM(HR)/COO dashboards."""
    totals = {
        'GM': {'total': 0, 'types': {'CL': 0, 'EL': 0, 'SICK': 0}},
        'QA Manager': {'total': 0, 'types': {'CL': 0, 'EL': 0, 'SICK': 0}},
        'CTO': {'total': 0, 'types': {'CL': 0, 'EL': 0, 'SICK': 0}}
    }
    if not user_ids:
        return [{'category': k, 'days': float(v['total']), 'cl': 0.0, 'el': 0.0, 'sick': 0.0} for k, v in totals.items()]

    requests = LeaveRequest.objects.filter(
        user_id__in=user_ids, request_type='LEAVE', status='APPROVED',
        start_date__gte=month_start, start_date__lte=end_date,
    ).select_related('user', 'user__department')

    for req in requests:
        days = req.num_days or 0
        l_type = req.leave_type if req.leave_type in ('CL', 'EL', 'SICK') else 'OTHER'
        
        category = None
        if req.user.is_software_dept():
            category = 'CTO'
        elif req.user.department and req.user.department.is_qa:
            category = 'QA Manager'
        else:
            category = 'GM'
            
        totals[category]['total'] += days
        if l_type not in totals[category]['types']:
            totals[category]['types'][l_type] = 0
        totals[category]['types'][l_type] += days

    return [
        {
            'category': k, 
            'days': float(v['total']),
            'cl': float(v['types'].get('CL', 0)),
            'el': float(v['types'].get('EL', 0)),
            'sick': float(v['types'].get('SICK', 0))
        }
        for k, v in totals.items()
    ]


def _leave_category_by_branch(month_start, end_date):
    """Per-branch version of _leave_category_breakdown, for GM(HR) and
    COO â€” both see across every branch, so their dashboard needs one pie
    per branch instead of a single combined one."""
    result = []
    for branch in Branch.objects.all().order_by('code'):
        user_ids = list(
            User.objects.filter(branch=branch)
            .exclude(role__in=['HR', 'SENIOR_HR', 'ADMIN'])
            .values_list('id', flat=True)
        )
        breakdown = _leave_category_breakdown(user_ids, month_start, end_date)
        result.append({
            'branch': f"{branch.name} ({branch.code})",
            'breakdown': breakdown,
            'has_data': any(item['days'] > 0 for item in breakdown),
        })
    return result


class HRLoginView(auth_views.LoginView):
    template_name = 'core/login.html'
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


@login_required
def set_active_branch(request):
    if request.method == 'POST':
        user = request.user
        can_switch = user_can_switch_branch(user)
        if not can_switch:
            messages.error(request, "You do not have permission to switch branches.")
            return redirect('core:dashboard')
        branch_id = request.POST.get('branch_id')
        if branch_id:
            if user.role in ('ADMIN', 'SENIOR_HR', 'COO') or getattr(user, 'can_access_all_branches', False):
                allowed = Branch.objects.all()
            else:
                allowed = user.accessible_branches.all()
                if user.branch_id:
                    allowed = allowed | Branch.objects.filter(id=user.branch_id)
                    
            branch = allowed.filter(id=branch_id).first()
            if branch:
                request.session['active_branch_id'] = branch_id
                messages.success(request, f"You are now viewing {branch.name} ({branch.code}).")
            else:
                messages.error(request, "You don't have access to that branch.")
        return redirect(request.POST.get('next') or 'core:dashboard')
    return redirect('core:dashboard')


@login_required
def dashboard(request):
    user = request.user
    context = {'user': user}

    today = timezone.localdate()
    context['today_record'] = AttendanceRecord.objects.filter(user=user, date=today).first()

    active_branch = getattr(request, 'active_branch', None)

    if user.role == 'ADMIN':
        base_qs = EmployeeProfile.objects.select_related('user')
        if active_branch:
            base_qs = base_qs.filter(user__branch=active_branch)
        context['hr_count'] = base_qs.filter(user__role='HR').count()
        context['manager_count'] = base_qs.filter(user__role='MANAGER').count()
        context['employee_count'] = base_qs.filter(user__role='EMPLOYEE').count()
    elif user.role == 'COO':
        import calendar
        from datetime import date
        from leaves.utils import approved_on_leave_today, approved_on_leave_for_date
        from projects.models import Project

        # ---- Month selector (defaults to the current month) ----
        month_param = request.GET.get('month')  # expected 'YYYY-MM'
        try:
            sel_year, sel_month = (int(p) for p in month_param.split('-'))
            month_start = date(sel_year, sel_month, 1)
        except (ValueError, AttributeError, TypeError):
            month_start = today.replace(day=1)
        month_end = date(month_start.year, month_start.month, calendar.monthrange(month_start.year, month_start.month)[1])
        # Cap at today for the current month so we don't count future days as absent.
        month_effective_end = min(month_end, today)
        days_elapsed = max(0, (month_effective_end - month_start).days + 1)

        context['selected_month'] = month_start.strftime('%Y-%m')
        # Last 12 months (including current) for the dropdown, newest first.
        month_options = []
        y, m = today.year, today.month
        for _ in range(12):
            month_options.append({'value': f'{y:04d}-{m:02d}', 'label': date(y, m, 1).strftime('%B %Y')})
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        context['month_options'] = month_options

        if active_branch:
            branches = [active_branch]
        else:
            branches = Branch.objects.all().order_by('code')
        branch_stats = []
        
        all_active_profiles = EmployeeProfile.objects.exclude(user__role__in=['ADMIN']).filter(status='ACTIVE').select_related('user', 'user__branch')
        
        for branch in branches:
            branch_profiles = [p for p in all_active_profiles if p.user.branch_id == branch.id]
            total_emp = len(branch_profiles)
            
            branch_user_ids = {p.user_id for p in branch_profiles}
            
            present = AttendanceRecord.objects.filter(
                date=today, in_time__isnull=False, user_id__in=branch_user_ids
            ).values('user_id').distinct().count()
            
            on_leave = sum(
                1 for r in approved_on_leave_today(branch) if r.user_id in branch_user_ids
            )
            
            absent = max(0, total_emp - present - on_leave)
            
            active_projects = Project.objects.filter(
                manager__branch=branch, status='APPROVED'
            ).count()

            new_joiners = EmployeeProfile.objects.exclude(user__role='ADMIN').filter(
                user__branch=branch,
                user__date_joined_company__gte=month_start,
                user__date_joined_company__lte=month_end,
            ).count()

            exited_employees = EmployeeProfile.objects.exclude(user__role='ADMIN').filter(
                user__branch=branch,
                status='EXITED',
                exit_date__gte=month_start,
                exit_date__lte=month_end,
            ).count()

            # ---- Month-to-date present / absent / on-leave for this branch ----
            if days_elapsed > 0:
                present_month = AttendanceRecord.objects.filter(
                    date__gte=month_start, date__lte=month_effective_end,
                    in_time__isnull=False, user_id__in=branch_user_ids
                ).values('user_id', 'date').distinct().count()

                on_leave_month = 0
                day = month_start
                while day <= month_effective_end:
                    on_leave_month += sum(
                        1 for r in approved_on_leave_for_date(day, branch) if r.user_id in branch_user_ids
                    )
                    day += timedelta(days=1)

                absent_month = _month_to_date_absent_days(
                    branch_user_ids, branch, month_start, month_effective_end
                )
            else:
                present_month = on_leave_month = absent_month = 0
            
            branch_stats.append({
                'branch': branch,
                'total_employees': total_emp,
                'present': present,
                'absent': absent,
                'on_leave': on_leave,
                'active_projects': active_projects,
                'new_joiners': new_joiners,
                'exited_employees': exited_employees,
                'present_month': present_month,
                'absent_month': absent_month,
                'on_leave_month': on_leave_month,
            })
            
        context['branch_stats'] = branch_stats

                # ---- Leave category breakdown (GM / QA Manager / CTO), per
        # branch — COO sees every branch, so one pie per branch. Uses
        # the same selected month as the rest of this dashboard. ----
        import json
        if active_branch:
            user_ids = list(
                User.objects.filter(branch=active_branch)
                .exclude(role__in=['HR', 'SENIOR_HR', 'ADMIN'])
                .values_list('id', flat=True)
            )
            context['leave_category_json'] = json.dumps(
                _leave_category_breakdown(user_ids, month_start, month_effective_end)
            )
            context['use_single_leave_category'] = True
        else:
            context['leave_category_by_branch'] = _leave_category_by_branch(month_start, month_effective_end)
            context['leave_category_by_branch_json'] = json.dumps(context['leave_category_by_branch'])
            context['use_single_leave_category'] = False

    elif user.role in ('HR', 'SENIOR_HR'):
        from leaves.utils import approved_on_leave_today, approved_on_leave_for_date

        base_qs = EmployeeProfile.objects.exclude(user__role__in=['HR', 'SENIOR_HR', 'ADMIN'])
        if active_branch:
            base_qs = base_qs.filter(user__branch=active_branch)

        context['total_employees'] = base_qs.count()
        active_profiles = base_qs.filter(status='ACTIVE')
        context['active_employees'] = active_profiles.count()
        context['inactive_employees'] = context['total_employees'] - context['active_employees']

        active_user_ids = set(active_profiles.values_list('user_id', flat=True))

        present_today_count = AttendanceRecord.objects.filter(
            date=today, in_time__isnull=False, user_id__in=active_user_ids
        ).values('user_id').distinct().count()

        on_leave_today_count = sum(
            1 for r in approved_on_leave_today(active_branch) if r.user_id in active_user_ids
        )

        context['present_today'] = present_today_count
        context['on_leave_today'] = on_leave_today_count
        context['absent_today'] = max(0, context['active_employees'] - present_today_count - on_leave_today_count)

        month_start = today.replace(day=1)

        # ---- Monthly present / absent / on-leave totals (month-to-date) ----
        present_month_count = AttendanceRecord.objects.filter(
            date__gte=month_start, date__lte=today, in_time__isnull=False, user_id__in=active_user_ids
        ).values('user_id', 'date').distinct().count()

        on_leave_month_count = 0
        day = month_start
        while day <= today:
            on_leave_month_count += sum(
                1 for r in approved_on_leave_for_date(day, active_branch) if r.user_id in active_user_ids
            )
            day += timedelta(days=1)

        absent_month_count = _month_to_date_absent_days(
            active_user_ids, active_branch, month_start, today
        )

        context['present_month'] = present_month_count
        context['on_leave_month'] = on_leave_month_count
        context['absent_month'] = absent_month_count

        joiners_qs = base_qs.filter(
            user__date_joined_company__gte=month_start, user__date_joined_company__lte=today
        )
        exited_qs = base_qs.filter(
            status='EXITED', exit_date__gte=month_start, exit_date__lte=today
        )
        context['new_joiners_count'] = joiners_qs.count()
        context['exited_employees_count'] = exited_qs.count()
        context['current_month'] = month_start.strftime('%Y-%m')

                # ---- Leave category breakdown (GM / QA Manager / CTO) ----
        # Plain HR only sees their own branch, so one combined pie.
        # GM (HR) = SENIOR_HR sees across every branch, so it splits
        # into one pie per branch, same as the COO dashboard.
        import json
        if user.role == 'SENIOR_HR' and not active_branch:
            context['leave_category_by_branch'] = _leave_category_by_branch(month_start, today)
            context['leave_category_by_branch_json'] = json.dumps(context['leave_category_by_branch'])
            context['use_single_leave_category'] = False
        else:
            context['leave_category_json'] = json.dumps(
                _leave_category_breakdown(active_user_ids, month_start, today)
            )
            context['use_single_leave_category'] = True

    else:
        month_start = today.replace(day=1)
        context['month_attendance'] = AttendanceRecord.objects.filter(
            user=user, date__gte=month_start, date__lte=today, in_time__isnull=False
        ).count()
        context['my_pending_leaves'] = LeaveRequest.objects.filter(
            user=user, status__in=['PENDING_MANAGER', 'PENDING_HR']
        ).count()

        is_qa_manager = user.role == 'MANAGER' and bool(user.department and user.department.is_qa)
        is_gm_layout = user.role == 'GENERAL_MANAGER' or user.role == 'CTO' or is_qa_manager
        context['is_gm_layout'] = is_gm_layout

        if user.role == 'PROJECT_MANAGER':
            # Live count of projects this PM is currently assigned to
            # manage â€” recomputed on every dashboard load, so it always
            # reflects the current state, no caching involved.
            from projects.models import Project
            context['pm_project_count'] = Project.objects.filter(
                manager=user, status='APPROVED'
            ).count()
        elif user.role == 'MANAGER' and not is_qa_manager:
            context['team_size'] = get_manager_team(user).count()
        # GENERAL_MANAGER, CTO, QA MANAGER: no Team Members / Pending Leave card â€” see
        # dashboard.html, which shows only "Days Present This Month" for them.

    # Leave Analytics for Doughnut Chart - Team Aggregate for Managers Only
    # NOTE: LeaveBalance only tracks ONE combined pool (see leaves/models.py
    # docstring) â€” there is no per-type (CL/EL/SICK) allocated/remaining
    # balance in this schema. So:
    #   - "Used" per leave type comes from approved LeaveRequest records
    #     (leave_type is a label on the request, grouped/summed here).
    #   - "Remaining" is the single combined LeaveBalance.balance total â€”
    #     it is NOT split by leave type, because that split doesn't exist
    #     in the database. Never derive one from the other.
    context['show_leave_analytics'] = False
    if user.is_manager():
        from leaves.models import LeaveBalance
        import json

        if user.role == 'GENERAL_MANAGER':
            team = get_gm_scope(user)
        else:
            team = get_manager_team(user)

        team_ids = list(team.values_list('id', flat=True))

        if team_ids:
            month_start = today.replace(day=1)

            leave_type_labels = {'CL': 'Casual Leave', 'EL': 'Earned Leave', 'SICK': 'Sick Leave'}
            used_by_type = {'CL': 0, 'EL': 0, 'SICK': 0}

            approved_requests = LeaveRequest.objects.filter(
                user_id__in=team_ids, request_type='LEAVE', status='APPROVED',
                leave_type__in=['CL', 'EL', 'SICK'],
                start_date__gte=month_start, start_date__lte=today,
            )
            for req in approved_requests:
                days = req.num_days or 0
                used_by_type[req.leave_type] += days

            total_used = sum(used_by_type.values())

            bals = LeaveBalance.objects.filter(user_id__in=team_ids)
            total_remaining = sum(b.balance for b in bals)

            if bals.exists():
                leave_analytics = [
                    {'type': t, 'label': leave_type_labels[t], 'used': float(used_by_type[t])}
                    for t in ('CL', 'EL', 'SICK')
                ]
                context['leave_analytics_json'] = json.dumps(leave_analytics)
                context['leave_total_used'] = float(total_used)
                context['leave_total_remaining'] = float(total_remaining)
                context['show_leave_analytics'] = True
                context['team_analytics_count'] = bals.count()
                context['leave_analytics_month'] = month_start.strftime('%B %Y')

    return render(request, 'core/dashboard.html', context)
