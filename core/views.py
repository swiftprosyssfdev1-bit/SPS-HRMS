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
    existing weekly-off convention — see attendance/utils.py's WKD calc).
    A day counts as Absent only if there's no attendance punch AND no
    approved leave for that employee on that day.

    Note: this app has no Holiday model, so company holidays beyond
    Sunday can't be auto-excluded here — only join date and Sunday are
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

    else:
        month_start = today.replace(day=1)
        context['month_attendance'] = AttendanceRecord.objects.filter(
            user=user, date__gte=month_start, date__lte=today, in_time__isnull=False
        ).count()
        context['my_pending_leaves'] = LeaveRequest.objects.filter(
            user=user, status__in=['PENDING_MANAGER', 'PENDING_HR']
        ).count()

        if user.role == 'PROJECT_MANAGER':
            # Live count of projects this PM is currently assigned to
            # manage — recomputed on every dashboard load, so it always
            # reflects the current state, no caching involved.
            from projects.models import Project
            context['pm_project_count'] = Project.objects.filter(
                manager=user, status='APPROVED'
            ).count()
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
            # manage — recomputed on every dashboard load, so it always
            # reflects the current state, no caching involved.
            from projects.models import Project
            context['pm_project_count'] = Project.objects.filter(
                manager=user, status='APPROVED'
            ).count()
        elif user.role == 'MANAGER' and not is_qa_manager:
            context['team_size'] = get_manager_team(user).count()
        # GENERAL_MANAGER, CTO, QA MANAGER: no Team Members / Pending Leave card — see
        # dashboard.html, which shows only "Days Present This Month" for them.

    # Leave Analytics for Pie Chart - Team Aggregate for Managers Only
    context['show_leave_analytics'] = False
    if user.is_manager():
        from leaves.models import LeaveBalance
        import json
        
        if user.role == 'GENERAL_MANAGER':
            team = get_gm_scope(user)
        else:
            team = get_manager_team(user)
        
        team_ids = list(team.values_list('id', flat=True))
        
        leave_analytics = []
        try:
            bals = LeaveBalance.objects.filter(user_id__in=team_ids)
            total_cl_used = sum(12 - b.cl_balance for b in bals)
            total_cl_remaining = sum(b.cl_balance for b in bals)
            
            total_el_used = sum(15 - b.el_balance for b in bals)
            total_el_remaining = sum(b.el_balance for b in bals)
            
            total_sl_used = sum(8 - b.sick_balance for b in bals)
            total_sl_remaining = sum(b.sick_balance for b in bals)
            
            if bals.exists():
                leave_analytics = [
                    {'category': 'Casual Leave', 'used': float(total_cl_used), 'remaining': float(total_cl_remaining)},
                    {'category': 'Earned Leave', 'used': float(total_el_used), 'remaining': float(total_el_remaining)},
                    {'category': 'Sick Leave',   'used': float(total_sl_used), 'remaining': float(total_sl_remaining)},
                ]
                context['leave_analytics_json'] = json.dumps(leave_analytics)
                context['show_leave_analytics'] = True
                context['team_analytics_count'] = bals.count()
        except Exception as e:
            pass

    return render(request, 'core/dashboard.html', context)
