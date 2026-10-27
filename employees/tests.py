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


# ---------------------------------------------------------------------------
# Excel import: update EXISTING employees only (Employee ID + Enrollment ID)
# ---------------------------------------------------------------------------
import datetime as _dt
import io as _io

from django.core.files.uploadedfile import SimpleUploadedFile


class ExcelImportExistingOnlyTests(TestCase):
    HEADERS = ['Enrollment No', 'Emp  ID\nNumber', 'Name', 'D O J', 'Designation', 'Address']

    def setUp(self):
        self.branch, _ = Branch.objects.get_or_create(code='B01', defaults={'name': 'Chennai'})
        self.hr = User.objects.create_user(
            username='imp_hr', password='password123', role=User.ROLE_HR,
            branch=self.branch, employee_id='IMPHR01',
        )
        self.emp_a = self._make_employee('imp_a', 'IMPT001', 'IMPE001', 'Ravi', 'Kumar', 'Operator', 'Old Address A')
        self.emp_b = self._make_employee('imp_b', 'IMPT002', 'IMPE002', 'Priya', 'Devi', 'Checker', 'Old Address B')
        self.client.login(username='imp_hr', password='password123')

    def _make_employee(self, username, emp_id, enr_id, first, last, designation, address):
        u = User.objects.create_user(
            username=username, password='password123', role=User.ROLE_EMPLOYEE,
            branch=self.branch, employee_id=emp_id, first_name=first, last_name=last,
        )
        u.date_joined_company = _dt.date(2023, 1, 2)
        u.save()
        profile, _ = EmployeeProfile.objects.get_or_create(user=u)
        profile.enrollment_id = enr_id
        profile.designation = designation
        profile.address = address
        profile.status = 'ACTIVE'
        profile.save()
        return u

    def _xlsx(self, rows):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(self.HEADERS)
        for r in rows:
            ws.append([r.get('enr'), r.get('emp'), r.get('name', 'Ravi Kumar'),
                       r.get('doj', _dt.date(2023, 1, 2)), r.get('designation'), r.get('address')])
        buf = _io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile('import.xlsx', buf.getvalue())

    def _post(self, rows, action='import'):
        return self.client.post(reverse('employees:import_employees'), {
            'branch': self.branch.id, 'action': action, 'file': self._xlsx(rows),
        })

    def _result(self, response, n=0):
        return response.context['results'][n]

    def _fresh(self, user):
        return EmployeeProfile.objects.get(user=user)

    # 1 -------------------------------------------------------------------
    def test_both_ids_match_and_values_differ_updates_employee(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Senior Operator'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'Updated')
        self.assertEqual(self._fresh(self.emp_a).designation, 'Senior Operator')
        # the other employee is untouched
        self.assertEqual(self._fresh(self.emp_b).designation, 'Checker')

    # 2 -------------------------------------------------------------------
    def test_identical_values_report_no_changes(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Operator'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'No Changes')
        self.assertTrue(r.unchanged)
        self.assertFalse(r.imported)
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')

    # 3 -------------------------------------------------------------------
    def test_employee_id_mismatch_is_error(self):
        resp = self._post([{'emp': 'IMPT999', 'enr': 'IMPE001', 'designation': 'Hacked'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'Error')
        self.assertIn('Employee ID mismatch', ' '.join(r.errors))
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')

    # 4 -------------------------------------------------------------------
    def test_enrollment_id_mismatch_is_error(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE999', 'designation': 'Hacked'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'Error')
        self.assertIn('Enrollment ID mismatch', ' '.join(r.errors))
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')

    # 5 -------------------------------------------------------------------
    def test_ids_of_two_different_employees_is_error(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE002', 'designation': 'Hacked'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'Error')
        self.assertIn('Conflicting IDs', ' '.join(r.errors))
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')
        self.assertEqual(self._fresh(self.emp_b).designation, 'Checker')

    # 6 -------------------------------------------------------------------
    def test_unknown_employee_is_error_and_nothing_is_created(self):
        users_before = User.objects.count()
        profiles_before = EmployeeProfile.objects.count()
        resp = self._post([{'emp': 'NOPE001', 'enr': 'NOPE002', 'name': 'Ghost Person'}])
        r = self._result(resp)
        self.assertEqual(r.status, 'Error')
        self.assertIn('Employee not found. Only existing employees can be updated.', r.errors)
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(EmployeeProfile.objects.count(), profiles_before)
        self.assertFalse(User.objects.filter(employee_id='NOPE001').exists())

    # 7 -------------------------------------------------------------------
    def test_blank_identifier_is_error(self):
        resp = self._post([
            {'emp': '', 'enr': 'IMPE001', 'designation': 'Hacked'},
            {'emp': 'IMPT001', 'enr': '', 'designation': 'Hacked'},
            {'emp': '', 'enr': '', 'designation': 'Hacked'},
        ])
        results = resp.context['results']
        self.assertEqual([r.status for r in results], ['Error', 'Error', 'Error'])
        self.assertIn('Employee ID is missing', ' '.join(results[0].errors))
        self.assertIn('Enrollment ID is missing', ' '.join(results[1].errors))
        self.assertIn('both missing', ' '.join(results[2].errors))
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')

    # 8 -------------------------------------------------------------------
    def test_blank_optional_cells_keep_existing_values(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Senior Operator', 'address': None}])
        self.assertEqual(self._result(resp).status, 'Updated')
        profile = self._fresh(self.emp_a)
        self.assertEqual(profile.designation, 'Senior Operator')
        self.assertEqual(profile.address, 'Old Address A')          # blank cell did not erase it

    # extra safety ------------------------------------------------------------
    def test_any_error_row_blocks_the_whole_file(self):
        resp = self._post([
            {'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Senior Operator'},   # valid
            {'emp': 'IMPT002', 'enr': 'IMPE999', 'designation': 'Senior Checker'},    # error
        ])
        self.assertEqual([r.status for r in resp.context['results']], ['Will Update', 'Error'])
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')
        self.assertEqual(self._fresh(self.emp_b).designation, 'Checker')

    def test_validate_only_never_writes(self):
        resp = self._post([{'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Senior Operator'}], action='validate')
        self.assertEqual(self._result(resp).status, 'Will Update')
        self.assertEqual(self._fresh(self.emp_a).designation, 'Operator')

    def test_update_keeps_login_role_branch_and_ids(self):
        self._post([{'emp': 'IMPT001', 'enr': 'IMPE001', 'designation': 'Senior Operator'}])
        self.emp_a.refresh_from_db()
        self.assertEqual(self.emp_a.username, 'imp_a')
        self.assertTrue(self.emp_a.check_password('password123'))
        self.assertEqual(self.emp_a.role, User.ROLE_EMPLOYEE)
        self.assertEqual(self.emp_a.branch_id, self.branch.id)
        self.assertEqual(self.emp_a.employee_id, 'IMPT001')
        self.assertEqual(self._fresh(self.emp_a).enrollment_id, 'IMPE001')
