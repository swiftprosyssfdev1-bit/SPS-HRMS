from django.db.models import Q
from django.utils import timezone


def not_expired_leaves(qs, today):
    """Excludes permission/leave requests whose date (or end date, for
    multi-day leave) has already passed. Used by BOTH the approvals list
    and the notification-badge count so they always agree — otherwise a
    request can inflate the badge while being hidden from the list itself,
    making it impossible to ever clear."""
    return qs.filter(
        Q(request_type='PERMISSION', permission_date__gte=today) |
        Q(request_type='LEAVE', end_date__gte=today) |
        Q(request_type='WFH', end_date__gte=today)
    )


def approved_on_leave_today(active_branch=None):
    """Every APPROVED Leave/Permission request that is actively in effect
    TODAY, scoped to a branch. Delegates to approved_on_leave_for_date.
    Deliberately excludes WFH — a WFH day is worked time, not an absence,
    so it must never count toward "on leave" / LOP / headcount-absent
    figures. Use approved_wfh_for_date for WFH-specific views."""
    today = timezone.localdate()
    return approved_on_leave_for_date(today, active_branch)


def approved_on_leave_for_date(target_date, active_branch=None):
    """Same as approved_on_leave_today, but for any given date — used by
    the COO's per-branch, per-day manpower view so the COO can check any
    day, not just today. Only LEAVE and PERMISSION requests count here;
    WFH is intentionally excluded (see approved_on_leave_today)."""
    from leaves.models import LeaveRequest

    now_time = timezone.localtime().time()
    is_today = target_date == timezone.localdate()

    qs = LeaveRequest.objects.filter(
        status='APPROVED', request_type__in=['PERMISSION', 'LEAVE']
    ).select_related(
        'user', 'user__profile', 'user__department', 'user__branch',
        'reviewed_by_manager', 'reviewed_by_hr',
    )
    if active_branch:
        qs = qs.filter(user__branch=active_branch)

    results = []
    for r in qs:
        if r.request_type == 'PERMISSION':
            if not r.permission_date or r.permission_date != target_date:
                continue
            if is_today and r.to_time and now_time > r.to_time:
                continue
        else:  # LEAVE
            if not r.start_date or not r.end_date:
                continue
            if not (r.start_date <= target_date <= r.end_date):
                continue
        results.append(r)
    return results


def approved_wfh_for_date(target_date, active_branch=None):
    """Every APPROVED WFH request actively in effect on target_date,
    scoped to a branch. Kept separate from approved_on_leave_for_date so
    WFH never gets counted as an absence anywhere (attendance marking,
    LOP calculation, or the COO's present/absent/on-leave branch stats)."""
    from leaves.models import LeaveRequest

    qs = LeaveRequest.objects.filter(
        status='APPROVED', request_type='WFH'
    ).select_related('user', 'user__profile', 'user__department', 'user__branch')
    if active_branch:
        qs = qs.filter(user__branch=active_branch)

    return [
        r for r in qs
        if r.start_date and r.end_date and r.start_date <= target_date <= r.end_date
    ]


def approved_wfh_today(active_branch=None):
    return approved_wfh_for_date(timezone.localdate(), active_branch)
