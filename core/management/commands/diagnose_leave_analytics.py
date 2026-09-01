"""
Diagnostic command for "this GM's leave / their team's leave isn't
showing on the HR/COO Leave Analytics dashboard" style bugs.

Give it a username or employee_id and it walks through exactly the same
logic core/views.py uses to build the dashboard pie charts, and prints
where (if anywhere) this user and their team currently land — or why
they're being dropped.

Usage:
    python manage.py diagnose_leave_analytics <username_or_employee_id>
"""
import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import User, Branch
from core.utils import get_gm_scope, get_manager_team
from leaves.models import LeaveRequest


class Command(BaseCommand):
    help = "Trace why a user's leave is/isn't showing in the branch-based leave analytics."

    def add_arguments(self, parser):
        parser.add_argument('user', help="username or employee_id of the GM/Manager/CTO to check")
        parser.add_argument('--month', default=None, help="YYYY-MM, defaults to current month")

    def handle(self, *args, **options):
        ident = options['user']
        user = User.objects.filter(username=ident).first() or User.objects.filter(employee_id=ident).first()
        if not user:
            raise CommandError(f"No user found with username or employee_id '{ident}'")

        today = timezone.localdate()
        if options['month']:
            y, m = (int(p) for p in options['month'].split('-'))
        else:
            y, m = today.year, today.month
        month_start = date(y, m, 1)
        month_end = date(y, m, calendar.monthrange(y, m)[1])
        eff_end = min(month_end, today)

        self.stdout.write(self.style.WARNING(f"\n=== {user.username} ({user.get_full_name() or '—'}) ==="))
        self.stdout.write(f"role            : {user.role}")
        self.stdout.write(f"branch          : {user.branch} (id={user.branch_id})")
        self.stdout.write(f"department      : {user.department} "
                           f"(is_qa={getattr(user.department, 'is_qa', None)})")
        self.stdout.write(f"is_software_dept: {user.is_software_dept()}")
        self.stdout.write(f"status (profile): {getattr(getattr(user, 'employeeprofile', None), 'status', 'no profile')}")

        # ---- Which per-branch bucket would _leave_category_by_branch put them in? ----
        self.stdout.write(self.style.WARNING("\n--- Dashboard branch-bucket eligibility ---"))
        if user.branch_id is None:
            self.stdout.write(self.style.ERROR(
                "  branch is NULL -> excluded from EVERY branch pie on HR/COO dashboards."
            ))
        elif user.role in ('HR', 'SENIOR_HR', 'ADMIN'):
            self.stdout.write(self.style.ERROR(
                f"  role={user.role} is explicitly excluded by "
                f".exclude(role__in=['HR','SENIOR_HR','ADMIN']) in _leave_category_by_branch."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  Eligible: would be counted under branch '{user.branch}' "
                f"(category={'CTO' if user.is_software_dept() else 'QA Manager' if getattr(user.department, 'is_qa', False) else 'GM'})."
            ))

        # ---- Their approved leave this month ----
        self.stdout.write(self.style.WARNING(f"\n--- Approved LEAVE requests, {month_start} to {eff_end} ---"))
        own_leaves = LeaveRequest.objects.filter(
            user=user, request_type='LEAVE', status='APPROVED',
            start_date__gte=month_start, start_date__lte=eff_end,
        )
        if not own_leaves.exists():
            self.stdout.write("  (none found for this user in this date range)")
        for lr in own_leaves:
            self.stdout.write(f"  {lr.leave_type} x {lr.num_days} days, {lr.start_date} -> {lr.end_date}, status={lr.status}")

        # Any leave requests at all (any status), in case they're stuck pending
        any_leaves = LeaveRequest.objects.filter(
            user=user, request_type='LEAVE', start_date__gte=month_start, start_date__lte=month_end,
        ).exclude(status='APPROVED')
        if any_leaves.exists():
            self.stdout.write(self.style.WARNING("  Non-approved leave requests this month (won't count until APPROVED):"))
            for lr in any_leaves:
                self.stdout.write(f"    {lr.leave_type} x {lr.num_days} days, status={lr.status}")

        # ---- Their team (for context — NOT what drives the branch pie, see note) ----
        if user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'CTO'):
            self.stdout.write(self.style.WARNING("\n--- Team scope (personal dashboard widget only) ---"))
            team = get_gm_scope(user) if user.role == 'GENERAL_MANAGER' else get_manager_team(user)
            self.stdout.write(f"  {team.count()} team member(s) (excludes {user.username} themselves, by design)")
            for t in team:
                flag = '' if t.branch_id else '  <-- NULL BRANCH, excluded from branch pies'
                self.stdout.write(f"    {t.username} [{t.role}] branch={t.branch}{flag}")
            self.stdout.write(
                "\n  NOTE: the HR/COO 'Leave Analytics by Approval Chain' pie is NOT built from "
                "this team list. It's built per-branch, from every non-HR/Admin user whose "
                "user.branch matches that branch. So a team member's leave shows up under "
                "*their own* branch's pie, not necessarily grouped with their manager."
            )

        self.stdout.write(self.style.WARNING("\n--- All branches, for reference ---"))
        for b in Branch.objects.all().order_by('code'):
            self.stdout.write(f"  {b.code}: {b.name} (id={b.id})")
