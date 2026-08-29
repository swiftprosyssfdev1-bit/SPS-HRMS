"""
One-off diagnostic command: lists every Department currently flagged
is_qa=True, and every EMPLOYEE in those departments, so you can quickly
spot a department that got wrongly tagged (e.g. by the name-based
backfill in migration 0015) and fix it in Admin.

Usage:
    python manage.py list_qa_departments
"""
from django.core.management.base import BaseCommand
from core.models import Department, User


class Command(BaseCommand):
    help = "List departments flagged is_qa=True and their employees."

    def handle(self, *args, **options):
        qa_depts = Department.objects.filter(is_qa=True).order_by('branch__name', 'name')
        if not qa_depts:
            self.stdout.write("No departments are currently flagged is_qa=True.")
            return

        for dept in qa_depts:
            self.stdout.write(self.style.WARNING(f"\n{dept} (is_qa=True)"))
            employees = User.objects.filter(department=dept, role='EMPLOYEE').order_by('employee_id')
            if not employees:
                self.stdout.write("  (no employees)")
                continue
            for emp in employees:
                self.stdout.write(f"  {emp.employee_id or emp.id}\t{emp.get_full_name() or emp.username}")
