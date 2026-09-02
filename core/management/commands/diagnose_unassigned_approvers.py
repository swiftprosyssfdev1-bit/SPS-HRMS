"""
Finds employees whose leave falls into an "(Unassigned)" bucket on the
Leave Analytics pie chart, and shows exactly which link in the approver
chain (department.manager -> user.manager -> branch.manager) is missing
for each one, so it can be fixed at the source (Department / User edit
screens) rather than in code.

Usage:
    python manage.py diagnose_unassigned_approvers
    python manage.py diagnose_unassigned_approvers --month 2026-09
"""
import calendar
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from leaves.models import LeaveRequest
from core.utils import get_manager_team, get_gm_scope


class Command(BaseCommand):
    help = "List employees whose approved leave has no resolvable approver this month."

    def add_arguments(self, parser):
        parser.add_argument('--month', default=None, help="YYYY-MM, defaults to current month")

    def handle(self, *args, **options):
        today = timezone.localdate()
        if options['month']:
            y, m = (int(p) for p in options['month'].split('-'))
        else:
            y, m = today.year, today.month
        month_start = date(y, m, 1)
        month_end = date(y, m, calendar.monthrange(y, m)[1])

        requests = LeaveRequest.objects.filter(
            request_type='LEAVE', status='APPROVED',
            start_date__gte=month_start, start_date__lte=month_end,
        ).select_related('user', 'user__department', 'user__manager', 'user__branch', 'user__branch__manager')

        seen_users = {}
        for req in requests:
            u = req.user
            if u.id in seen_users:
                continue
            seen_users[u.id] = u

        if not seen_users:
            self.stdout.write("No approved leave requests found in this date range.")
            return

        any_unassigned = False
        gm_lookup = {}
        from core.models import User
        for gm in User.objects.filter(role=User.ROLE_GENERAL_MANAGER):
            for member in get_gm_scope(gm):
                gm_lookup.setdefault(member.id, gm)
        cto_lookup = {}
        for cto in User.objects.filter(role=User.ROLE_CTO):
            for member in get_manager_team(cto):
                cto_lookup.setdefault(member.id, cto)
        manager_lookup = {}
        for mgr in User.objects.filter(role__in=[User.ROLE_MANAGER, User.ROLE_PROJECT_MANAGER]):
            for member in get_manager_team(mgr):
                manager_lookup.setdefault(member.id, mgr)

        for u in seen_users.values():
            dept = u.department
            dept_mgr = dept.manager if dept else None
            direct_mgr = u.manager
            direct_mgr_is_manager = bool(direct_mgr and direct_mgr.is_manager())
            branch_mgr = u.branch.manager if u.branch else None

            if u.is_software_dept():
                live_approver = cto_lookup.get(u.id)
            elif dept and dept.is_qa:
                live_approver = manager_lookup.get(u.id) or gm_lookup.get(u.id)
            else:
                live_approver = gm_lookup.get(u.id) or manager_lookup.get(u.id)

            resolved = live_approver or dept_mgr or (direct_mgr if direct_mgr_is_manager else None) or branch_mgr
            if resolved:
                continue  # this one resolves fine, not the problem

            any_unassigned = True
            self.stdout.write(self.style.ERROR(f"\n{u.username} ({u.get_full_name() or '—'}) -> UNASSIGNED"))
            self.stdout.write(f"  department            : {dept or '— (no department set)'}")
            live_status = str(live_approver) if live_approver else "not on any manager's resolved team"
            self.stdout.write(f"  live team lookup (Project/GM-scope): {live_status}")
            self.stdout.write(f"  department.manager    : {dept_mgr or '— NOT SET'}")
            self.stdout.write(f"  user.manager          : {direct_mgr or '— NOT SET'}"
                               + ('' if not direct_mgr else
                                  '' if direct_mgr_is_manager else '  (set, but role is not a manager-tier role)'))
            self.stdout.write(f"  branch                : {u.branch or '— no branch'}")
            self.stdout.write(f"  branch.manager        : {branch_mgr or '— NOT SET'}")
            self.stdout.write(self.style.WARNING(
                "  FIX: put this person on a GM's team via a Project (Project.gm + "
                "ProjectAssignment), or set department.manager, or user.manager, "
                "or branch.manager — any one of these resolves it."
            ))

        if not any_unassigned:
            self.stdout.write(self.style.SUCCESS(
                "Every employee with approved leave this month resolves to a named approver."
            ))
