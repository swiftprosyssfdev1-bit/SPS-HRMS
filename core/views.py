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
from core.utils import user_can_switch_branch, get_manager_team, get_gm_scope, dashboard_all_branches
from core.models import Branch, User, ActivityLog
from django.core.paginator import Paginator


def _month_to_date_absent_days(user_ids, branch, month_start, end_date, on_leave_pairs=None):
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

    # Callers that already computed the (user_id, day) leave/permission
    # pairs can pass them in to skip the per-day query loop.
    if on_leave_pairs is None:
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


def _pct(part, whole):
    """Percentage rounded to 1 decimal; 0 when there is nothing to divide by."""
    return round(part * 100.0 / whole, 1) if whole else 0


def _absence_pairs(user_ids, month_start, end_date):
    """One query -> ({(user_id, day)} on approved LEAVE, {(user_id, day)}
    on approved PERMISSION) inside [month_start, end_date]. WFH is never
    counted. Same rule as leaves.utils.approved_on_leave_for_date: a
    permission whose end time has already passed today no longer counts
    as \"currently away\"."""
    from leaves.models import LeaveRequest
    from django.db.models import Q

    leave_pairs, perm_pairs = set(), set()
    if not user_ids:
        return leave_pairs, perm_pairs

    today = timezone.localdate()
    now_time = timezone.localtime().time()
    qs = LeaveRequest.objects.filter(status='APPROVED', user_id__in=user_ids).filter(
        Q(request_type='LEAVE', start_date__lte=end_date, end_date__gte=month_start) |
        Q(request_type='PERMISSION', permission_date__gte=month_start, permission_date__lte=end_date)
    )
    for r in qs:
        if r.request_type == 'PERMISSION':
            d = r.permission_date
            if not d:
                continue
            if d == today and r.to_time and now_time > r.to_time:
                continue
            perm_pairs.add((r.user_id, d))
        else:
            if not r.start_date or not r.end_date:
                continue
            day = max(r.start_date, month_start)
            last = min(r.end_date, end_date)
            while day <= last:
                leave_pairs.add((r.user_id, day))
                day += timedelta(days=1)
    return leave_pairs, perm_pairs


def _attendance_snapshot(user_ids, branch, month_start, today):
    """Today + month-to-date attendance numbers (with percentages) for a
    set of employees. Present counts anyone who punched in; Half Day is
    the subset of those who punched out with 3:50 <= net hours < 7:40
    (see attendance.models); Leave and Permission are approved requests
    only. Absent keeps the existing rule: not present and not on
    approved leave/permission (join-date aware, Sundays excluded for the
    month figure)."""
    from attendance.models import FULL_DAY_HOURS, HALF_DAY_MIN_HOURS

    user_ids = set(user_ids)
    total = len(user_ids)
    zero = {'present': 0, 'half': 0, 'absent': 0, 'leave': 0, 'permission': 0,
            'present_pct': 0, 'half_pct': 0, 'absent_pct': 0, 'leave_pct': 0, 'permission_pct': 0}
    if not total:
        return {'total': 0, 'today': dict(zero), 'month': dict(zero)}

    records = AttendanceRecord.objects.filter(
        user_id__in=user_ids, date__gte=month_start, date__lte=today, in_time__isnull=False
    )
    half_qs = records.filter(
        out_time__isnull=False, total_hours__gte=HALF_DAY_MIN_HOURS, total_hours__lt=FULL_DAY_HOURS
    )
    present_today = records.filter(date=today).count()
    present_month = records.count()
    half_today = half_qs.filter(date=today).count()
    half_month = half_qs.count()

    leave_pairs, perm_pairs = _absence_pairs(user_ids, month_start, today)
    leave_today = sum(1 for _, d in leave_pairs if d == today)
    perm_today = sum(1 for _, d in perm_pairs if d == today)
    leave_month, perm_month = len(leave_pairs), len(perm_pairs)

    absent_today = max(0, total - present_today - leave_today - perm_today)
    absent_month = _month_to_date_absent_days(
        user_ids, branch, month_start, today, on_leave_pairs=leave_pairs | perm_pairs
    )

    def build(present, half, absent, leave, perm, whole):
        return {
            'present': present, 'half': half, 'absent': absent, 'leave': leave, 'permission': perm,
            'present_pct': _pct(present, whole), 'half_pct': _pct(half, whole),
            'absent_pct': _pct(absent, whole), 'leave_pct': _pct(leave, whole),
            'permission_pct': _pct(perm, whole),
        }

    # Today: percentage of headcount. Month: percentage of employee-days
    # (present + absent + on leave) since those are person-days, not people.
    month_whole = present_month + absent_month + leave_month
    return {
        'total': total,
        'today': build(present_today, half_today, absent_today, leave_today, perm_today, total),
        'month': build(present_month, half_month, absent_month, leave_month, perm_month, month_whole),
    }


def _sum_snapshots(snaps):
    """Combine several _attendance_snapshot results (one per branch) into
    one company-wide snapshot whose numbers are exactly the sum of the
    branch rows, with percentages recomputed on the combined totals."""
    keys = ('present', 'half', 'absent', 'leave', 'permission')
    total = sum(sn['total'] for sn in snaps)
    out = {'total': total}
    for period in ('today', 'month'):
        agg = {k: sum(sn[period][k] for sn in snaps) for k in keys}
        whole = total if period == 'today' else agg['present'] + agg['absent'] + agg['leave']
        for k in keys:
            agg[k + '_pct'] = _pct(agg[k], whole)
        out[period] = agg
    return out


def _build_approver_lookup():
    """Reverse-map every employee to the manager(s)/GM(s)/CTO(s) whose
    team they're actually in, reusing the same rich team-resolution
    logic (get_gm_scope / get_manager_team) that already powers each
    manager's own 'Team Leave Analytics' widget — Project assignments,
    department managers, and direct reports. This means the HR/COO
    leave-analytics chart resolves names automatically as Projects and
    Departments are set up normally; it does NOT require hand-setting
    every employee's Reporting Manager field.

    An employee can legitimately be on more than one GM's team at once
    (e.g. assigned to two different projects run by two different GMs),
    so each lookup maps to a LIST of approvers, not a single one."""
    gm_lookup = {}
    for gm in User.objects.filter(role=User.ROLE_GENERAL_MANAGER):
        for member in get_gm_scope(gm):
            gm_lookup.setdefault(member.id, []).append(gm)

    cto_lookup = {}
    for cto in User.objects.filter(role=User.ROLE_CTO):
        for member in get_manager_team(cto):
            cto_lookup.setdefault(member.id, []).append(cto)

    manager_lookup = {}
    for mgr in User.objects.filter(role__in=[User.ROLE_MANAGER, User.ROLE_PROJECT_MANAGER]):
        for member in get_manager_team(mgr):
            manager_lookup.setdefault(member.id, []).append(mgr)

    return gm_lookup, cto_lookup, manager_lookup


def _resolve_approvers(user, gm_lookup, cto_lookup, manager_lookup):
    """Find every person actually approving this user's leave — usually
    one, but can be several if the employee is on multiple GMs' teams
    at once (shared across projects). Prefers the live team-membership
    lookups (Project/Department driven, no manual setup needed) and
    only falls back to explicit fields (department.manager / user.manager
    / branch.manager) if the person genuinely isn't on any manager's
    resolved team yet. Always returns a list (possibly empty)."""
    if user.is_software_dept():
        approvers = cto_lookup.get(user.id, [])
    elif user.department and user.department.is_qa:
        approvers = manager_lookup.get(user.id) or gm_lookup.get(user.id) or []
    else:
        approvers = gm_lookup.get(user.id) or manager_lookup.get(user.id) or []

    if approvers:
        # de-dupe while preserving order, in case a manager shows up via
        # more than one path (e.g. both department.manager and a project)
        seen = set()
        unique = []
        for a in approvers:
            if a.id not in seen:
                seen.add(a.id)
                unique.append(a)
        return unique

    # Fallback chain for anyone Projects/Departments don't yet link up.
    dept = user.department
    if dept and dept.manager_id:
        return [dept.manager]
    if user.manager_id and user.manager.is_manager():
        return [user.manager]
    if user.branch_id and user.branch.manager_id:
        return [user.branch.manager]
    return []


def _leave_category_breakdown(user_ids, month_start, end_date):
    """Total approved LEAVE days taken in [month_start, end_date] by the
    given users, bucketed by the actual approving manager: Software dept
    -> that dept's CTO, QA dept -> that dept's QA Manager, everyone else
    (Production / default) -> their GM. Each bucket is keyed by the real
    approver (name shown), resolved automatically from Project/Department
    team membership (see _build_approver_lookup) — not just the generic
    role — so multiple GMs / CTOs / QA Managers each get their own slice
    without any manual per-employee setup.

    If an employee is on more than one GM's team at once (shared across
    projects), their leave days are credited in FULL to every GM whose
    team includes them — so each GM's slice reflects the complete
    picture of their own team's leave, rather than one GM silently
    claiming the employee exclusively. This means the pie's grand total
    can exceed the employee's actual leave days when they're shared
    across managers; that's intentional, since this chart is "leave per
    manager's team", not "total company leave days". Powers the
    leave-analytics pie chart on the HR/GM(HR)/COO dashboards."""
    if not user_ids:
        return []

    requests = LeaveRequest.objects.filter(
        user_id__in=user_ids, request_type='LEAVE', status='APPROVED',
        start_date__gte=month_start, start_date__lte=end_date,
    ).select_related('user', 'user__department', 'user__manager', 'user__branch', 'user__branch__manager')

    gm_lookup, cto_lookup, manager_lookup = _build_approver_lookup()
    buckets = {}  # key -> {'label', 'role', 'total', 'types'}

    for req in requests:
        user = req.user
        if user.is_software_dept():
            role_bucket = 'CTO'
        elif user.department and user.department.is_qa:
            role_bucket = 'QA Manager'
        else:
            role_bucket = 'GM'

        approvers = _resolve_approvers(user, gm_lookup, cto_lookup, manager_lookup)
        days = req.num_days or 0
        l_type = req.leave_type if req.leave_type in ('CL', 'EL', 'SICK') else 'OTHER'

        if approvers:
            for approver in approvers:
                name = approver.get_full_name() or approver.username
                label = f"{name} ({role_bucket})"
                key = f"user:{approver.id}:{role_bucket}"
                bucket = buckets.setdefault(key, {
                    'label': label, 'role': role_bucket, 'total': 0,
                    'types': {'CL': 0, 'EL': 0, 'SICK': 0},
                })
                # Full days credited to EVERY approver whose team this
                # employee is on (not split) — a shared employee's leave
                # shows in full under each GM, since each GM genuinely
                # has that person on their team. This means the sum
                # across all slices can exceed the employee's actual
                # total leave days when they're shared across managers;
                # that's intentional here, not a bug.
                bucket['total'] += days
                bucket['types'][l_type] = bucket['types'].get(l_type, 0) + days
        else:
            label = f"{role_bucket} (Unassigned)"
            key = f"unassigned:{role_bucket}"
            bucket = buckets.setdefault(key, {
                'label': label, 'role': role_bucket, 'total': 0,
                'types': {'CL': 0, 'EL': 0, 'SICK': 0},
            })
            bucket['total'] += days
            bucket['types'][l_type] = bucket['types'].get(l_type, 0) + days

    return [
        {
            'category': b['label'],
            'role': b['role'],
            'days': round(float(b['total']), 2),
            'cl': round(float(b['types'].get('CL', 0)), 2),
            'el': round(float(b['types'].get('EL', 0)), 2),
            'sick': round(float(b['types'].get('SICK', 0)), 2),
        }
        for b in sorted(buckets.values(), key=lambda x: -x['total'])
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
        if branch_id == 'all':
            # "All Branches" is a dashboard-only view for GM (HR) and COO; it
            # does not change the active branch used by the other pages.
            if user.role in ('SENIOR_HR', 'COO'):
                request.session['dashboard_all_branches'] = True
                messages.success(request, "You are now viewing All Branches.")
            else:
                messages.error(request, "You don't have access to that view.")
            return redirect(request.POST.get('next') or 'core:dashboard')
        if branch_id:
            request.session['dashboard_all_branches'] = False
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
    
    try:
        from core.models import MotivationalPost
        context['motivational_post'] = MotivationalPost.objects.filter(is_active=True).first()
    except Exception:
        pass

    active_branch = getattr(request, 'active_branch', None)
    context['show_all_branches'] = False

    if user.role == 'ADMIN':
        base_qs = EmployeeProfile.objects.select_related('user')
        if active_branch:
            base_qs = base_qs.filter(user__branch=active_branch)
        context['hr_count'] = base_qs.filter(user__role__in=['HR', 'SENIOR_HR']).count()
        context['manager_count'] = base_qs.filter(user__role__in=['MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'CTO']).count()
        context['employee_count'] = base_qs.filter(user__role='EMPLOYEE').count()
        
        context['month_attendance'] = AttendanceRecord.objects.filter(
            user=user, date__year=today.year, date__month=today.month, in_time__isnull=False
        ).count()
        context['my_pending_leaves'] = LeaveRequest.objects.filter(
            user=user, status='PENDING'
        ).count()
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

        # "All Branches" picked in the dropdown -> show every branch at once.
        coo_all_branches = dashboard_all_branches(request)
        coo_scope_branch = None if coo_all_branches else active_branch
        context['show_all_branches'] = coo_all_branches
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

        if coo_scope_branch:
            branches = [coo_scope_branch]
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
        if coo_scope_branch:
            user_ids = list(
                User.objects.filter(branch=coo_scope_branch)
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
        # GM (HR) can pick "All Branches" in the dropdown: every card below
        # is then company-wide (stats_branch=None) and a branch-wise
        # breakdown table is added. Plain HR always stays on their branch.
        show_all = user.role == 'SENIOR_HR' and dashboard_all_branches(request)
        stats_branch = None if show_all else active_branch
        context['show_all_branches'] = show_all

        # GM (HR) sees who added / changed what on employee records.
        if user.role == 'SENIOR_HR':
            recent_qs = ActivityLog.objects.select_related('actor', 'target', 'branch')
            if stats_branch:
                recent_qs = recent_qs.filter(branch=stats_branch)
            context['recent_changes'] = list(recent_qs[:8])

        base_qs = EmployeeProfile.objects.exclude(user__role__in=['HR', 'SENIOR_HR', 'ADMIN'])
        if stats_branch:
            base_qs = base_qs.filter(user__branch=stats_branch)

        context['total_employees'] = base_qs.count()
        active_profiles = base_qs.filter(status='ACTIVE')
        context['active_employees'] = active_profiles.count()
        context['inactive_employees'] = context['total_employees'] - context['active_employees']

        active_user_ids = set(active_profiles.values_list('user_id', flat=True))
        month_start = today.replace(day=1)

        if show_all:
            # Branch-wise rows first; the company-wide cards and the
            # "All Branches" total row are the exact sum of these rows.
            branch_rows, branch_snaps = [], []
            for branch in Branch.objects.all().order_by('code'):
                b_base = EmployeeProfile.objects.exclude(
                    user__role__in=['HR', 'SENIOR_HR', 'ADMIN']
                ).filter(user__branch=branch)
                b_active_ids = set(b_base.filter(status='ACTIVE').values_list('user_id', flat=True))
                b_snap = _attendance_snapshot(b_active_ids, branch, month_start, today)
                branch_snaps.append(b_snap)
                branch_rows.append({
                    'branch': branch,
                    'total_employees': b_base.count(),
                    'active_employees': len(b_active_ids),
                    'today': b_snap['today'],
                    'month': b_snap['month'],
                    'new_joiners': b_base.filter(
                        user__date_joined_company__gte=month_start, user__date_joined_company__lte=today
                    ).count(),
                    'exited': b_base.filter(
                        status='EXITED', exit_date__gte=month_start, exit_date__lte=today
                    ).count(),
                })
            snap = _sum_snapshots(branch_snaps)
            context['branch_rows'] = branch_rows
            context['overall_today'] = snap['today']
            context['overall_month'] = snap['month']
        else:
            snap = _attendance_snapshot(active_user_ids, stats_branch, month_start, today)
        context['present_today'] = snap['today']['present']
        context['half_today'] = snap['today']['half']
        context['absent_today'] = snap['today']['absent']
        context['on_leave_today'] = snap['today']['leave']
        context['permission_today'] = snap['today']['permission']

        context['present_month'] = snap['month']['present']
        context['half_month'] = snap['month']['half']
        context['absent_month'] = snap['month']['absent']
        context['on_leave_month'] = snap['month']['leave']
        context['permission_month'] = snap['month']['permission']

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
        # GM (HR) on "All Branches" splits into one pie per branch.
        import json
        if show_all:
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
            import calendar
            month_start = today.replace(day=1)
            month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

            leave_type_labels = {'CL': 'Casual Leave', 'EL': 'Earned Leave', 'SICK': 'Sick Leave'}
            used_by_type = {'CL': 0, 'EL': 0, 'SICK': 0}

            approved_requests = LeaveRequest.objects.filter(
                user_id__in=team_ids, request_type='LEAVE', status='APPROVED',
                leave_type__in=['CL', 'EL', 'SICK'],
                start_date__gte=month_start, start_date__lte=month_end,
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

from core.models import MotivationalPost
from core.forms import MotivationalPostForm

@login_required
def manage_motivational_post(request):
    if request.user.role not in ('HR', 'SENIOR_HR', 'ADMIN', 'COO'):
        messages.error(request, 'Access denied.')
        return redirect('core:dashboard')

    posts = MotivationalPost.objects.all()
    if request.method == 'POST':
        form = MotivationalPostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.uploaded_by = request.user
            post.save()
            messages.success(request, 'Motivational post uploaded successfully.')
            return redirect('core:manage_motivational_post')
    else:
        form = MotivationalPostForm(initial={'is_active': True})

    return render(request, 'core/manage_motivational_post.html', {'form': form, 'posts': posts})


@login_required
def recent_changes(request):
    """GM (HR) only: full history of who added / modified employee records."""
    if request.user.role != 'SENIOR_HR':
        messages.error(request, "Only GM (HR) can view Recent Changes.")
        return redirect('core:dashboard')

    qs = ActivityLog.objects.select_related('actor', 'target', 'branch')

    q = request.GET.get('q', '').strip()
    action = request.GET.get('action', '').strip()
    branch_id = request.GET.get('branch', '').strip()
    if q:
        qs = qs.filter(
            Q(actor_name__icontains=q) | Q(target_name__icontains=q) | Q(summary__icontains=q)
        )
    if action in dict(ActivityLog.ACTION_CHOICES):
        qs = qs.filter(action=action)
    if branch_id.isdigit():
        qs = qs.filter(branch_id=int(branch_id))

    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))

    params = request.GET.copy()
    params.pop('page', None)

    return render(request, 'core/recent_changes.html', {
        'page_obj': page_obj,
        'q': q,
        'selected_action': action,
        'selected_branch': branch_id,
        'action_choices': ActivityLog.ACTION_CHOICES,
        'branches': Branch.objects.all().order_by('name'),
        'querystring': params.urlencode(),
    })

