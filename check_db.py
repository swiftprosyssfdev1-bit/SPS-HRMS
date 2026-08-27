import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prostaff.settings')
django.setup()

from projects.models import Project
from core.models import User

project = Project.objects.last()
print(f"Project: {project.name} (ID {project.id})")
assignments = project.assignments.filter(is_current=True)
print(f"Assignments count: {assignments.count()}")
for a in assignments:
    print(f" - Assignment User ID: {a.user_id}")

user_ids = project.assignments.filter(is_current=True).values_list('user_id', flat=True)
print(f"User IDs list: {list(user_ids)}")

assigned_employees = User.objects.filter(id__in=user_ids)
print(f"Assigned Employees count: {assigned_employees.count()}")
for emp in assigned_employees:
    print(f" - Emp: {emp.username}")
