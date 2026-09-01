from decimal import Decimal

from django.db.models import Q
from django.utils import timezone


def cl_cycle_start(d):
    """The start date (the 26th) of the payroll cycle that contains date
    `d`. Cycles run 26th-of-month -> 25th-of-next-month, matching payroll
    — NOT calendar months. e.g. Sep 29 and Oct 2 are both in the same
    cycle (starts Sep 26); Sep 24 is in the PREVIOUS cycle (starts Aug
    26)."""
    if d.day >= 26:
        year, month = d.year, d.month
    else:
        month = d.month - 1
        year = d.year
        if month == 0:
            month = 12
            year -= 1
    return d.replace(year=year, month=month, day=26)


def cl_cycle_bounds(d):
    """(cycle_start, cycle_end) for the 26th-25th payroll cycle that
    contains date `d` — cycle_start is the 26th (see cl_cycle_start),
    cycle_end is the 25th of the following month."""
    cycle_start = cl_cycle_start(d)
    if cycle_start.month == 12:
        year, month = cycle_start.year + 1, 1
    else:
        year, month = cycle_start.year, cycle_start.month + 1
    cycle_end = cycle_start.replace(year=year, month=month, day=25)
    return cycle_start, cycle_end


# Roles that get 2 leave days/cycle instead of the base 1. Per-org policy:
# regular employees, HR and Senior HR get 1 day/cycle; Manager, General
# Manager, Project Manager, COO and CTO get 2. There is no separate
# CL/EL/Sick split for PF-applicable employees — whichever "Leave
# Category" is picked on the form draws from the same single per-cycle
# allowance.
PF_TWO_DAY_ROLES = ('MANAGER', 'GENERAL_MANAGER', 'PROJECT_MANAGER', 'COO', 'CTO')


def pf_leave_quota_for_user(user):
    """Per-cycle leave-day quota for a PF-applicable employee."""
    return 2 if user.role in PF_TWO_DAY_ROLES else 1


def pf_leave_days_remaining_in_cycle(user, cycle_start, cycle_end, quota, exclude_id=None):
    """How much of this cycle's per-cycle PF leave quota is still
    unclaimed, given every approved/pending LEAVE request (any leave_type
    — CL/EL/Sick aren't tracked separately for PF employees) that
    overlaps the cycle. This is a hard per-cycle usage cap, independent
    of any banked LeaveBalance. Returns a Decimal between 0 and `quota`.
    `exclude_id` lets a request being re-evaluated (e.g. on edit) exclude
    itself from the "already consumed" count."""
    from leaves.models import LeaveRequest

    qs = LeaveRequest.objects.filter(
        user=user, request_type='LEAVE',
        status__in=['APPROVED', 'PENDING_MANAGER', 'PENDING_HR', 'PENDING_COO', 'HR_REJECTED_PENDING_MANAGER'],
    )
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)

    consumed = Decimal('0')
    for r in qs:
        if not r.start_date or not r.end_date:
            continue
        overlap_start = max(r.start_date, cycle_start)
        overlap_end = min(r.end_date, cycle_end)
        if overlap_start <= overlap_end:
            # Use the request's own recorded cl_days (the covered
            # portion) where we have it (the normal case — set at
            # submission time). Legacy rows saved before cl_days existed
            # fall back to their full day count for that overlap, since
            # we can't recover the original split retroactively.
            if r.cl_days:
                consumed += r.cl_days
            else:
                consumed += Decimal((overlap_end - overlap_start).days + 1)

    return max(Decimal('0'), Decimal(quota) - consumed)


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
