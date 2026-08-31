from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from employees.models import EmployeeProfile
from leaves.models import LeaveBalance


class Command(BaseCommand):
    """Monthly CL accrual for PF-applicable employees.

    Policy: PF employees don't get the full 12 days upfront — 1 CL is
    credited per month, only once that month has actually started. If an
    employee doesn't use it, it stays in their balance (no reset, no cap).
    Non-PF employees are untouched — they keep the flat 12-upfront model.

    Safe to run more than once in the same month, or after a gap of
    several months (it catches up on every month owed since
    last_credited_month, capped at the employee's join month).
    """

    help = "Credit this month's CL to every PF-applicable active employee (idempotent, catches up missed months)."

    def handle(self, *args, **options):
        today = timezone.localdate()
        current_month = today.replace(day=1)

        profiles = EmployeeProfile.objects.filter(
            status='ACTIVE', is_pf_applicable=True
        ).select_related('user')

        credited = 0
        for profile in profiles:
            user = profile.user
            join_month = user.date_joined_company.replace(day=1) if user.date_joined_company else current_month
            if join_month > current_month:
                continue  # hasn't started yet

            balance, _ = LeaveBalance.objects.get_or_create(
                user=user, defaults={'balance': 0, 'last_credited_month': None}
            )

            next_due = self._add_month(balance.last_credited_month) if balance.last_credited_month else join_month
            if next_due < join_month:
                next_due = join_month

            months_owed = 0
            cursor = next_due
            while cursor <= current_month:
                months_owed += 1
                cursor = self._add_month(cursor)

            if months_owed > 0:
                balance.balance = balance.balance + Decimal(months_owed)
                balance.last_credited_month = current_month
                balance.save()
                credited += 1

        self.stdout.write(self.style.SUCCESS(
            f'Credited monthly CL for {credited} PF-applicable employee(s), through {current_month.strftime("%B %Y")}.'
        ))

    @staticmethod
    def _add_month(d):
        if d.month == 12:
            return d.replace(year=d.year + 1, month=1)
        return d.replace(month=d.month + 1)
