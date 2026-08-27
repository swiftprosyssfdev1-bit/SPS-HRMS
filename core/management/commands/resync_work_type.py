"""
DailyProduction.work_type is stored per-row at the moment the entry is
created — it is NOT re-derived from the employee's department after the
fact. So if a department's is_qa flag gets corrected (e.g. via Admin)
AFTER some entries already exist, those existing rows keep showing the
old, now-wrong Work Type forever until fixed manually.

This command finds every DailyProduction row (for a given date, default
today) whose work_type disagrees with the employee's CURRENT department,
and by default just reports them. Pass --fix to actually correct them:
  - work_type is switched to match the employee's department
  - target_day is re-synced from the project's daily_target/qa_daily_target
  - recalculate() is called to refresh achieved/actual_target/efficiency

If fixing would collide with an existing row for the same
employee/date/project/work_type (i.e. the employee already has a
correctly-typed row for that project today), the mismatched row is left
untouched and flagged for manual review instead of silently merging or
deleting data.

Usage:
    python manage.py resync_work_type                  # report only, today
    python manage.py resync_work_type --fix             # fix, today
    python manage.py resync_work_type --date 2026-08-21 --fix
"""
from datetime import date as date_cls

from django.core.management.base import BaseCommand, CommandError

from production_tracker.models import DailyProduction


class Command(BaseCommand):
    help = "Find/fix DailyProduction rows whose work_type no longer matches the employee's department."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='YYYY-MM-DD, defaults to today')
        parser.add_argument('--fix', action='store_true', help='Actually correct the mismatched rows')

    def handle(self, *args, **options):
        if options['date']:
            try:
                target_date = date_cls.fromisoformat(options['date'])
            except ValueError:
                raise CommandError('--date must be YYYY-MM-DD')
        else:
            target_date = date_cls.today()

        records = DailyProduction.objects.filter(date=target_date).select_related(
            'employee__department', 'project'
        )

        mismatches = []
        for r in records:
            dept = r.employee.department
            correct_type = 'QA' if (dept and dept.is_qa) else 'PRODUCTION'
            if r.work_type != correct_type:
                mismatches.append((r, correct_type))

        if not mismatches:
            self.stdout.write(f"No work_type mismatches found for {target_date}.")
            return

        self.stdout.write(self.style.WARNING(f"{len(mismatches)} mismatched row(s) for {target_date}:"))
        fixed, skipped = 0, 0
        for r, correct_type in mismatches:
            label = f"  {r.employee.employee_id or r.employee.username} on \"{r.project}\": stored={r.work_type} -> should be {correct_type}"

            if not options['fix']:
                self.stdout.write(label)
                continue

            # Don't clobber an existing correctly-typed row for the same
            # employee/date/project — leave it for manual review instead.
            conflict = DailyProduction.objects.filter(
                employee=r.employee, date=r.date, project=r.project, work_type=correct_type
            ).exclude(pk=r.pk).exists()
            if conflict:
                self.stdout.write(self.style.ERROR(label + "  [SKIPPED: a correctly-typed row already exists — fix manually]"))
                skipped += 1
                continue

            r.work_type = correct_type
            r.sync_target_from_project()
            r.save(update_fields=['work_type', 'target_day', 'updated_at'])
            r.recalculate()
            self.stdout.write(self.style.SUCCESS(label + "  [FIXED]"))
            fixed += 1

        if options['fix']:
            self.stdout.write(f"\nDone. Fixed {fixed}, skipped {skipped} (needs manual review).")
        else:
            self.stdout.write("\nRun again with --fix to apply corrections.")
