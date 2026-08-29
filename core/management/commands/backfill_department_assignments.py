from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Department, User


class Command(BaseCommand):
    """One-off backfill for existing employees.

    core/signals.py::auto_assign_to_department_project only fires when a
    user is CREATED or their department CHANGES. If a Department's
    default_project is set (or changed) AFTER its employees already
    existed in that department, those employees never got a
    ProjectAssignment row — so they're invisible on the Hourly Tracker
    team dashboard and Excel report, even though the daily-entry project
    dropdown works fine for them.

    Run once after setting/changing a Department.default_project:
        python manage.py backfill_department_assignments
    """

    help = "Create missing ProjectAssignment rows for employees whose department has a default_project."

    def handle(self, *args, **options):
        from projects.models import ProjectAssignment

        departments = Department.objects.exclude(default_project__isnull=True)
        if not departments.exists():
            self.stdout.write("No departments have a default_project set. Nothing to do.")
            return

        created_count = 0
        for dept in departments:
            employees = User.objects.filter(department=dept)
            for emp in employees:
                _, created = ProjectAssignment.objects.get_or_create(
                    project=dept.default_project,
                    user=emp,
                    is_current=True,
                    defaults={
                        'role_on_project': emp.get_role_display() if hasattr(emp, 'get_role_display') else '',
                        'start_date': timezone.localdate(),
                    },
                )
                if created:
                    created_count += 1
                    self.stdout.write(f"  + {emp.username} -> {dept.default_project.name} ({dept.name})")

        self.stdout.write(self.style.SUCCESS(f"Done. Created {created_count} ProjectAssignment row(s)."))
