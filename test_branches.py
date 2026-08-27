import django, sys, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prostaff.settings')
django.setup()
from core.models import User, Branch
from core.context_processors import branch_context
from django.test import RequestFactory

user = User.objects.filter(role='SENIOR_HR').first()
print(f'User: {user.username}, Role: {user.role}')
req = RequestFactory().get('/')
req.user = user
req.session = {}
ctx = branch_context(req)
print('all_branches:', ctx.get('all_branches'))
print('can_switch_branch:', ctx.get('can_switch_branch'))
