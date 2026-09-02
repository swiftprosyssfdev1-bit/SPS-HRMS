"""
Diagnostic / repair command for the "GM leave not showing in HR/COO
dashboard" bug: onboarding previously allowed EMPLOYEE / MANAGER /
PROJECT_MANAGER / GENERAL_MANAGER / CTO accounts to be saved with
branch = NULL. Since the HR and COO "Leave Analytics by Approval Chain"
pie charts are built per-branch (see core/views.py:
_leave_category_by_branch), any such user is silently invisible on
every dashboard chart even though their account and leave requests
are otherwise completely normal.

Usage:
    # Just list the affected users (safe, read-only, run this first)
    python manage.py fix_missing_branch

    # Assign every affected user to a specific branch by its code
    python manage.py fix_missing_branch --set-branch HQ

    # Only fix one user (by username or employee_id), instead of all of them
    python manage.py fix_missing_branch --set-branch HQ --user gm3
"""
from django.core.management.base import BaseCommand, CommandError
from core.models import User, Branch

BRANCH_SCOPED_ROLES = (
    User.ROLE_EMPLOYEE,
    User.ROLE_MANAGER,
    User.ROLE_PROJECT_MANAGER,
    User.ROLE_GENERAL_MANAGER,
    User.ROLE_CTO,
)


class Command(BaseCommand):
    help = "Find (and optionally fix) branch-scoped users whose branch is NULL."

    def add_arguments(self, parser):
        parser.add_argument(
            '--set-branch', metavar='BRANCH_CODE', default=None,
            help="Branch code to assign to every affected user (e.g. HQ). "
                 "Without this flag the command only lists the problem, it "
                 "changes nothing.",
        )
        parser.add_argument(
            '--user', metavar='USERNAME_OR_EMP_ID', default=None,
            help="Only act on this one user instead of every affected user.",
        )

    def handle(self, *args, **options):
        affected = User.objects.filter(
            role__in=BRANCH_SCOPED_ROLES, branch__isnull=True
        ).order_by('role', 'username')

        user_filter = options.get('user')
        if user_filter:
            affected = affected.filter(
                models_q_username_or_empid(user_filter)
            )

        if not affected.exists():
            self.stdout.write(self.style.SUCCESS(
                "No branch-scoped users with a missing branch found."
            ))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {affected.count()} user(s) with a branch-scoped role and no branch:"
        ))
        for u in affected:
            self.stdout.write(
                f"  [{u.role}] {u.username}"
                f" (id={u.id}, employee_id={u.employee_id or '—'},"
                f" name={u.get_full_name() or '—'})"
            )

        set_branch_code = options.get('set_branch')
        if not set_branch_code:
            self.stdout.write(
                "\nRun again with --set-branch <CODE> to assign a branch to "
                "the user(s) above. Nothing was changed."
            )
            return

        try:
            branch = Branch.objects.get(code=set_branch_code)
        except Branch.DoesNotExist:
            valid = ', '.join(Branch.objects.values_list('code', flat=True))
            raise CommandError(
                f"No branch with code '{set_branch_code}'. Valid codes: {valid}"
            )

        updated = affected.update(branch=branch)
        self.stdout.write(self.style.SUCCESS(
            f"\nAssigned branch '{branch.code}' to {updated} user(s). "
            "Their leave will now appear correctly in the HR/COO dashboards "
            "for future months. Past months are computed live from "
            "LeaveRequest data, so this fix also applies retroactively — "
            "no backfill script needed."
        ))


def models_q_username_or_empid(value):
    from django.db.models import Q
    return Q(username=value) | Q(employee_id=value)
