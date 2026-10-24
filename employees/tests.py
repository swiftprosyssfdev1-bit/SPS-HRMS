from django.test import TestCase, Client, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from core.models import Branch, Department
from core.utils import get_active_branch
from core.permissions import can_access_user_branch
from employees.models import EmployeeProfile

User = get_user_model()


class BranchAccessAndProfileSecurityTests(TestCase):
    def setUp(self):
        # Retrieve or create Branches seeded by migration
        self.branch_chennai, _ = Branch.objects.get_or_create(code='B01', defaults={'name': 'Chennai'})
        self.branch_kanch, _ = Branch.objects.get_or_create(code='B03', defaults={'name': 'Kancheepuram'})

        # Clean any existing users with test usernames
        User.objects.filter(username__in=['admin_test', 'gm_hr_test', 'hr_chennai_test', 'hr_kanch_test', 'emp_chennai_test', 'emp_kanch_test']).delete()

        # Create Users
        # 1. Admin
        self.admin = User.objects.create_user(
            username='admin_test', password='password123',
            role=User.ROLE_ADMIN, employee_id='TEST_ADMIN'
        )

        # 2. Senior HR (GM HR)
        self.senior_hr = User.objects.create_user(
            username='gm_hr_test', password='password123',
            role=User.ROLE_SENIOR_HR, branch=self.branch_chennai, employee_id='TEST_GMHR'
        )

        # 3. Chennai Normal HR
        self.hr_chennai = User.objects.create_user(
            username='hr_chennai_test', password='password123',
            role=User.ROLE_HR, branch=self.branch_chennai, employee_id='TEST_HRC'
        )

        # 4. Kancheepuram Normal HR
        self.hr_kanch = User.objects.create_user(
            username='hr_kanch_test', password='password123',
            role=User.ROLE_HR, branch=self.branch_kanch, employee_id='TEST_HRK'
        )

        # 5. Chennai Employee
        self.emp_chennai = User.objects.create_user(
            username='emp_chennai_test', password='password123',
            role=User.ROLE_EMPLOYEE, branch=self.branch_chennai, employee_id='TEST_EMPC'
        )
        EmployeeProfile.objects.get_or_create(user=self.emp_chennai, defaults={'status': 'ACTIVE', 'designation': 'Data Entry'})

        # 6. Kancheepuram Employee
        self.emp_kanch = User.objects.create_user(
            username='emp_kanch_test', password='password123',
            role=User.ROLE_EMPLOYEE, branch=self.branch_kanch, employee_id='TEST_EMPK'
        )
        EmployeeProfile.objects.get_or_create(user=self.emp_kanch, defaults={'status': 'ACTIVE', 'designation': 'Operator'})

    def test_1_normal_hr_directory_branch_filtering(self):
        """Normal HR (Yoga - Kancheepuram) only sees Kancheepuram branch staff in directory."""
        client = Client()
        client.force_login(self.hr_kanch)

        response = client.get(reverse('employees:directory'))
        self.assertEqual(response.status_code, 200)

        # Kancheepuram employees must be in context
        employees_in_directory = list(response.context['employees'])
        emp_ids = [e.id for e in employees_in_directory]

        self.assertIn(self.emp_kanch.id, emp_ids)
        self.assertIn(self.hr_kanch.id, emp_ids)

        # Cross-branch employees (Chennai HR, Chennai Employee, global Admin) must NOT be in directory
        self.assertNotIn(self.hr_chennai.id, emp_ids)
        self.assertNotIn(self.emp_chennai.id, emp_ids)
        self.assertNotIn(self.admin.id, emp_ids)

    def test_2_normal_hr_cannot_view_other_branch_employee_via_url(self):
        """Normal HR cannot view profile of employee in another branch by URL manipulation."""
        client = Client()
        client.force_login(self.hr_kanch)

        # Attempt to access Chennai employee's detail
        response = client.get(reverse('employees:employee_detail', args=[self.emp_chennai.id]))
        # Should be redirected to directory with error message
        self.assertRedirects(response, reverse('employees:directory'))

        # Can view own branch employee
        response_own = client.get(reverse('employees:employee_detail', args=[self.emp_kanch.id]))
        self.assertEqual(response_own.status_code, 200)

    def test_3_normal_hr_cannot_edit_other_branch_employee_via_url(self):
        """Normal HR cannot edit profile of employee in another branch by URL manipulation."""
        client = Client()
        client.force_login(self.hr_kanch)

        # Attempt to access edit page of Chennai employee
        response = client.get(reverse('employees:edit_employee_profile', args=[self.emp_chennai.id]))
        self.assertRedirects(response, reverse('employees:directory'))

        # Attempt to POST update to Chennai employee
        post_response = client.post(
            reverse('employees:edit_employee_profile', args=[self.emp_chennai.id]),
            {'first_name': 'HackedName'}
        )
        self.assertRedirects(post_response, reverse('employees:directory'))
        self.emp_chennai.refresh_from_db()
        self.assertNotEqual(self.emp_chennai.first_name, 'HackedName')

    def test_4_unauthorized_active_branch_session_tampering(self):
        """Normal HR cannot tamper with session to switch to unauthorized branch."""
        factory = RequestFactory()
        req = factory.get('/')
        req.user = self.hr_kanch
        # Try to forge active_branch_id in session to Chennai
        req.session = {'active_branch_id': str(self.branch_chennai.id)}

        active_branch = get_active_branch(req)
        # Must resolve to user's authorized home branch, NOT Chennai
        self.assertEqual(active_branch.id, self.branch_kanch.id)

    def test_5_admin_and_senior_hr_cross_branch_access(self):
        """Admin and Senior HR retain authorized cross-branch access."""
        self.assertTrue(can_access_user_branch(self.admin, self.emp_chennai))
        self.assertTrue(can_access_user_branch(self.admin, self.emp_kanch))
        self.assertTrue(can_access_user_branch(self.senior_hr, self.emp_chennai))
        self.assertTrue(can_access_user_branch(self.senior_hr, self.emp_kanch))

        # Admin and Senior HR can view both employees
        client = Client()
        client.force_login(self.senior_hr)
        resp_chennai = client.get(reverse('employees:employee_detail', args=[self.emp_chennai.id]))
        self.assertEqual(resp_chennai.status_code, 200)
        resp_kanch = client.get(reverse('employees:employee_detail', args=[self.emp_kanch.id]))
        self.assertEqual(resp_kanch.status_code, 200)

    def test_6_employee_cannot_edit_own_profile(self):
        """Plain employees get a read-only profile: GET/POST to the self-edit
        page and document upload/delete are all blocked and nothing is saved."""
        client = Client()
        client.force_login(self.emp_kanch)
        original_first = self.emp_kanch.first_name

        # Profile page itself is still viewable
        self.assertEqual(client.get(reverse('employees:my_profile')).status_code, 200)

        # Edit page and edit POST are blocked
        resp = client.get(reverse('employees:edit_my_profile'))
        self.assertRedirects(resp, reverse('employees:my_profile'))
        resp = client.post(reverse('employees:edit_my_profile'), {
            'first_name': 'Hacked', 'last_name': 'Name',
            'role': User.ROLE_ADMIN, 'branch': self.branch_chennai.id,
        })
        self.assertRedirects(resp, reverse('employees:my_profile'))
        resp = client.post(reverse('employees:upload_own_document'), {})
        self.assertRedirects(resp, reverse('employees:my_profile'))

        self.emp_kanch.refresh_from_db()
        self.assertEqual(self.emp_kanch.first_name, original_first)
        self.assertEqual(self.emp_kanch.role, User.ROLE_EMPLOYEE)
        self.assertEqual(self.emp_kanch.branch, self.branch_kanch)
