from django import forms
from core.validators import (
    name_validator, phone_validator,
    bank_account_validator, ifsc_validator, pan_validator, aadhar_validator,
    COUNTRY_CODES,
)
from employees.models import EmployeeProfile, EmployeeDocument, ResignationRequest, BankDetail
from core.models import User
from core.models import Branch , Department 
from projects.models import Project
from core.utils import user_can_switch_branch


NAME_ATTRS = {
    'pattern': "[A-Za-z0-9' -]*", 'title': "Letters, numbers, spaces, hyphens and apostrophes only.",
    'maxlength': 150, 'oninput': "this.value = this.value.replace(/[^A-Za-z0-9' -]/g, '')",
}
PHONE_ATTRS = {
    'inputmode': 'numeric', 'pattern': '[0-9]{10}', 'title': "Enter exactly 10 digits.",
    'maxlength': 10, 'oninput': "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10)",
}
COUNTRY_CODE_ATTRS = {'class': 'form-control', 'style': 'max-width:150px; flex-shrink:0;'}
ID_ATTRS = {
    'pattern': '[A-Za-z0-9-]*', 'title': "Letters, numbers and hyphens only.",
    'maxlength': 20, 'oninput': "this.value = this.value.replace(/[^A-Za-z0-9-]/g, '')",
}
BANK_ATTRS = {
    'inputmode': 'numeric', 'pattern': '[0-9]{9,18}', 'title': "9 to 18 digits only.",
    'maxlength': 18, 'oninput': "this.value = this.value.replace(/[^0-9]/g, '')",
}
IFSC_ATTRS = {
    'pattern': '[A-Za-z]{4}0[A-Za-z0-9]{6}', 'title': "Format: 4 letters + 0 + 6 alphanumeric (e.g. HDFC0001234).",
    'maxlength': 11, 'style': 'text-transform:uppercase;',
    'oninput': "this.value = this.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase()",
}
PAN_ATTRS = {
    'pattern': '[A-Za-z]{5}[0-9]{4}[A-Za-z]{1}', 'title': "Format: 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F).",
    'maxlength': 10, 'style': 'text-transform:uppercase;',
    'oninput': "this.value = this.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase()",
}
# Aadhar is displayed grouped as "XXXX XXXX XXXX" but stored as a plain
# 12-digit string — the form strips spaces on clean before validation/save.
AADHAR_ATTRS = {
    'inputmode': 'numeric', 'title': "12 digits, shown as 4-4-4.",
    'maxlength': 14,  # 12 digits + 2 inserted spaces
    'oninput': (
        "this.value = this.value.replace(/[^0-9]/g, '').slice(0,12)"
        ".replace(/(.{4})(?=.)/g, '$1 ')"
    ),
}
NUMERIC_ATTRS = {
    'inputmode': 'numeric',
    'oninput': "this.value = this.value.replace(/[^0-9]/g, '')",
}


class EmployeeSelfEditForm(forms.ModelForm):
    """Fields an employee can edit on their own profile — basic info plus
    the extended identity fields (Aadhar, PAN, emergency contact, etc.)
    they fill in themselves. Bank details are handled by a separate
    BankDetailForm on the same page. Status, enrollment ID, employee ID
    and a few other administrative fields stay HR/Admin/Manager-only —
    see EmployeeIdentityForm."""
    first_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    email = forms.EmailField(required=False, widget=forms.EmailInput())
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    aadhar_no = forms.CharField(max_length=14, required=False, validators=[aadhar_validator], widget=forms.TextInput(attrs=AADHAR_ATTRS))
    pan_no = forms.CharField(max_length=10, required=False, validators=[pan_validator], widget=forms.TextInput(attrs=PAN_ATTRS))
    emergency_contact_name = forms.CharField(max_length=100, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    emergency_contact_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    emergency_contact_phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    class Meta:
        model = EmployeeProfile
        fields = [
            'address', 'profile_photo',
            'gender', 'marital_status', 'date_of_birth',
            'qualification', 'previous_experience', 'current_company_experience',
            'blood_group', 'uan_pf_number', 'esi_number',
            'aadhar_no', 'pan_no',
            'emergency_contact_name', 'emergency_contact_country_code', 'emergency_contact_phone',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'previous_experience': forms.TextInput(attrs={'placeholder': 'e.g. 2 years at XYZ Corp'}),
            'current_company_experience': forms.TextInput(attrs={'placeholder': 'e.g. 1.5 years'}),
        }

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop('user_instance', None)
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault('class', 'form-control')
        if self.user_instance:
            self.fields['first_name'].initial = self.user_instance.first_name
            self.fields['last_name'].initial = self.user_instance.last_name
            self.fields['email'].initial = self.user_instance.email
            self.fields['phone'].initial = self.user_instance.phone
            self.fields['phone_country_code'].initial = self.user_instance.phone_country_code
        # Show Aadhar pre-formatted with spaces even though it's stored raw.
        if self.instance and self.instance.pk and self.instance.aadhar_no:
            digits = self.instance.aadhar_no
            self.initial['aadhar_no'] = ' '.join(digits[i:i + 4] for i in range(0, len(digits), 4))

    def clean_aadhar_no(self):
        return self.cleaned_data.get('aadhar_no', '').replace(' ', '')

    def clean_pan_no(self):
        return self.cleaned_data.get('pan_no', '').upper()

    def save(self, commit=True):
        profile = super().save(commit=commit)
        if self.user_instance:
            self.user_instance.first_name = self.cleaned_data.get('first_name', '')
            self.user_instance.last_name = self.cleaned_data.get('last_name', '')
            self.user_instance.email = self.cleaned_data.get('email', '')
            self.user_instance.phone = self.cleaned_data.get('phone', '')
            self.user_instance.phone_country_code = self.cleaned_data.get('phone_country_code') or '+91'
            if commit:
                self.user_instance.save()
        return profile


class ProjectSelect(forms.Select):
    """Tags each <option> with data-branch=<branch id> so the JS can
    show only projects belonging to whichever Branch is currently selected."""
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            proj = Project.objects.filter(pk=value.value if hasattr(value, 'value') else value).select_related('manager__branch').first()
            if proj and proj.manager_id and proj.manager.branch_id:
                option['attrs']['data-branch'] = str(proj.manager.branch_id)
        return option


class DepartmentSelect(forms.Select):
    """Tags each <option> with data-has-project="1"/"0" depending on
    whether that Department already has a default_project set. A
    department WITH one (e.g. a single-project team) auto-assigns new
    employees to it via core.signals.auto_assign_to_department_project,
    so no manual pick is needed. A department WITHOUT one (e.g.
    Production, which spans several projects at once) has no way to
    know which project to auto-assign, so the JS shows the manual
    Project field for those. This checks the actual default_project
    value rather than guessing from the department's name, for the
    same reason Department.is_qa exists instead of name-matching."""
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            dept = Department.objects.filter(
                pk=value.value if hasattr(value, 'value') else value
            ).only('default_project_id').first()
            if dept:
                option['attrs']['data-has-project'] = '1' if dept.default_project_id else '0'
        return option


class HREmployeeEditForm(forms.ModelForm):
    username = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    new_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep unchanged'}), help_text="Enter a new password to change it, or leave blank.")
    first_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    date_joined_company = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    branch = forms.ModelChoiceField(queryset=Branch.objects.all().order_by('code'), required=False)
    accessible_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.all().order_by('code'), required=False,
        widget=forms.CheckboxSelectMultiple, label="Branch Access",
        help_text="Select every branch this HR user can view and manage.",
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.all().order_by('name'),
        required=False, widget=forms.Select(attrs={'class': 'form-control'}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by('name'),
        required=False, widget=ProjectSelect(attrs={'class': 'form-control'}),
    )
    manager = forms.ModelChoiceField(
        queryset=User.objects.filter(role__in=['GENERAL_MANAGER', 'PROJECT_MANAGER', 'MANAGER', 'CTO']),
        required=False, label="Reporting Manager",
        help_text=(
            "Who this person directly reports to — e.g. pick a General "
            "Manager to make them that GM's Assistant / Team Member, "
            "independent of Department or Project. Leave blank to fall "
            "back to the selected Department's own manager, if any."
        ),
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'department', 'manager', 'branch', 'date_joined_company', 'phone_country_code', 'phone']
        widgets = {'date_joined_company': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)
        
        if acting_user and acting_user.role != 'ADMIN':
            if 'username' in self.fields:
                del self.fields['username']
            if 'new_password' in self.fields:
                del self.fields['new_password']
                
        for name, field in self.fields.items():
            if name != 'accessible_branches':
                field.widget.attrs.setdefault('class', 'form-control')
        if self.instance and self.instance.pk:
            self.fields['accessible_branches'].initial = self.instance.accessible_branches.all()
            manager_qs = self.fields['manager'].queryset.exclude(id=self.instance.pk)
            if self.instance.branch_id:
                manager_qs = manager_qs.filter(branch_id=self.instance.branch_id)
            self.fields['manager'].queryset = manager_qs

    def clean(self):
        cleaned = super().clean()
        role = self.instance.role if self.instance and self.instance.pk else None
        # Same branch-scoping requirement as onboarding: clearing branch
        # here on a GM/CTO/Manager/PM/Employee would silently drop them
        # out of every branch-based leave analytics pie chart.
        if role in (User.ROLE_EMPLOYEE, User.ROLE_MANAGER, User.ROLE_PROJECT_MANAGER,
                    User.ROLE_GENERAL_MANAGER, User.ROLE_CTO):
            if not cleaned.get('branch'):
                self.add_error('branch', "This role must have a branch assigned.")
        return cleaned


class EmployeeIdentityForm(forms.ModelForm):
    designation = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    gender = forms.ChoiceField(choices=EmployeeProfile.GENDER_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))
    marital_status = forms.ChoiceField(choices=EmployeeProfile.MARITAL_STATUS_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    qualification = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    aadhar_no = forms.CharField(max_length=14, required=False, validators=[aadhar_validator], widget=forms.TextInput(attrs=AADHAR_ATTRS))
    pan_no = forms.CharField(max_length=10, required=False, validators=[pan_validator], widget=forms.TextInput(attrs=PAN_ATTRS))
    uan_pf_number = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=NUMERIC_ATTRS))
    esi_number = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=NUMERIC_ATTRS))
    emergency_contact_name = forms.CharField(max_length=100, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    emergency_contact_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    emergency_contact_phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    reference1_contact = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    reference2_contact = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    old_joining_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    is_pf_applicable = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    id_card_received = forms.BooleanField(required=False, widget=forms.CheckboxInput())

    class Meta:
        model = EmployeeProfile
        fields = [
            'profile_photo',
            'designation', 'gender', 'marital_status', 'date_of_birth',
            'qualification', 'previous_experience', 'current_company_experience',
            'employment_status', 'shift', 'work_type', 'is_pf_applicable',
            'id_card_received', 'blood_group', 'uan_pf_number', 'esi_number',
            'aadhar_no', 'pan_no',
            'emergency_contact_name', 'emergency_contact_country_code', 'emergency_contact_phone',
            'referred_by_name', 'referred_by_enrollment_id',
            'reference1_name', 'reference1_type', 'reference1_contact',
            'reference2_name', 'reference2_relation', 'reference2_contact',
            'old_joining_date',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'old_joining_date': forms.DateInput(attrs={'type': 'date'}),
            'previous_experience': forms.TextInput(attrs={'placeholder': 'e.g. 2 years at XYZ Corp'}),
            'current_company_experience': forms.TextInput(attrs={'placeholder': 'e.g. 1.5 years'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault('class', 'form-control')
        # Show Aadhar pre-formatted with spaces even though it's stored raw.
        if self.instance and self.instance.pk and self.instance.aadhar_no:
            digits = self.instance.aadhar_no
            self.initial['aadhar_no'] = ' '.join(digits[i:i + 4] for i in range(0, len(digits), 4))

    def clean_aadhar_no(self):
        return self.cleaned_data.get('aadhar_no', '').replace(' ', '')

    def clean_pan_no(self):
        return self.cleaned_data.get('pan_no', '').upper()


class BankDetailForm(forms.ModelForm):
    account_number = forms.CharField(max_length=18, required=False, validators=[bank_account_validator], widget=forms.TextInput(attrs=BANK_ATTRS))
    ifsc_code = forms.CharField(max_length=11, required=False, validators=[ifsc_validator], widget=forms.TextInput(attrs=IFSC_ATTRS))
    account_holder_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    bank_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    branch_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = BankDetail
        fields = ['account_holder_name', 'account_number', 'ifsc_code', 'bank_name', 'branch_name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_ifsc_code(self):
        return self.cleaned_data.get('ifsc_code', '').upper()

class IssueLetterForm(forms.Form):
    """HR picks a letter type, the employee(s) it's for, and the file to
    issue. One upload here can go out to any number of employees at once
    — each gets their own copy of the letter on their Letters page."""
    doc_type = forms.ChoiceField(
        choices=[c for c in EmployeeDocument.DOC_TYPES if c[0] in EmployeeDocument.HR_DOC_TYPES],
        label="Letter Type",
    )
    employees = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Issue To",
    )
    file = forms.FileField(label="Letter File")

    def __init__(self, *args, employee_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employees'].queryset = employee_queryset if employee_queryset is not None else User.objects.none()
        self.fields['doc_type'].widget.attrs.setdefault('class', 'form-control')
        self.fields['file'].widget.attrs.setdefault('class', 'form-control')


class SelfDocumentForm(forms.ModelForm):
    """Everyone uploads their own personal documents here."""
    doc_type = forms.ChoiceField(
        choices=[c for c in EmployeeDocument.DOC_TYPES if c[0] in EmployeeDocument.SELF_DOC_TYPES],
        label="Document Type",
    )

    class Meta:
        model = EmployeeDocument
        fields = ['doc_type', 'file']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class RoleChangeForm(forms.ModelForm):
    is_senior_hr = forms.BooleanField(
        required=False, label="Senior HR",
        help_text="Elevates this HR user to Senior HR — used when an HR is promoted to a senior position."
    )

    class Meta:
        model = User
        fields = ['role']

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)
        self.fields['role'].widget.attrs.setdefault('class', 'form-control')
        self.fields['is_senior_hr'].widget.attrs.setdefault('class', 'form-check-input')

        if self.instance and self.instance.pk:
            self.fields['is_senior_hr'].initial = self.instance.is_senior_hr

        if acting_user and acting_user.role == 'ADMIN':
            # Admin sees every role, including COO.
            self.fields['role'].choices = User.ROLE_CHOICES

        if acting_user and acting_user.role in ('HR', 'SENIOR_HR'):
            self.fields['role'].choices = [
                choice for choice in User.ROLE_CHOICES 
                if choice[0] not in (User.ROLE_ADMIN, User.ROLE_SENIOR_HR)
            ]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_senior_hr = self.cleaned_data.get('is_senior_hr', False) if user.role == 'HR' else False
        if commit:
            user.save()
        return user


class ResignationRequestForm(forms.ModelForm):
    class Meta:
        model = ResignationRequest
        fields = ['reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Tell HR why you are resigning...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
  
class OnboardHRForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, help_text="Temporary password for this account")
    first_name = forms.CharField(max_length=150, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    role = forms.ChoiceField(
        choices=User.ROLE_CHOICES,
        required=True,
    )

    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), required=False,
        widget=DepartmentSelect(attrs={'class': 'form-control'})
    )
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by('name'),
        required=False,
        widget=ProjectSelect(attrs={'class': 'form-control'}),
    )

    accessible_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.all().order_by('code'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Branch Access",
        help_text="Select every branch this user should be able to view and manage.",
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email',
                  'role', 'department', 'branch', 'date_joined_company', 'phone_country_code', 'phone', 'password']
        widgets = {
            'date_joined_company': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)

        if acting_user and acting_user.role in ('HR', 'SENIOR_HR'):
            # An HR actor can onboard any role except Admin or Senior HR
            # (GM (HR)) — Admin account creation stays restricted to
            # Admin actors only, and Senior HR is a peer/superior rank
            # that HR/Senior HR actors shouldn't be able to create for
            # each other; only an Admin can create a new GM (HR).
            self.fields['role'].choices = [
                c for c in User.ROLE_CHOICES
                if c[0] not in (User.ROLE_ADMIN, User.ROLE_SENIOR_HR)
            ]

            allowed_branches = acting_user.accessible_branches.all()
            if not allowed_branches.exists() and acting_user.branch_id:
                allowed_branches = Branch.objects.filter(id=acting_user.branch_id)

            self.fields['branch'].queryset = allowed_branches.order_by('code')
            self.fields['department'].queryset = Department.objects.all().order_by('name')
            self.fields['project'].queryset = Project.objects.all().order_by('name')

            # An HR actor can only grant branch access to branches she
            # herself can access — she can't hand out access she doesn't
            # have. Admin is not involved in this decision at all.
            if acting_user.can_access_all_branches:
                self.fields['accessible_branches'].queryset = Branch.objects.all().order_by('code')
            else:
                self.fields['accessible_branches'].queryset = allowed_branches.order_by('code')
        else:
            self.fields['branch'].queryset = Branch.objects.all().order_by('code')
            self.fields['department'].queryset = Department.objects.all().order_by('name')
            if 'project' in self.fields:
                self.fields['project'].queryset = Project.objects.all().order_by('name')
            self.fields['accessible_branches'].queryset = Branch.objects.all().order_by('code')

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        if role in (User.ROLE_EMPLOYEE, User.ROLE_MANAGER, User.ROLE_PROJECT_MANAGER,
                    User.ROLE_GENERAL_MANAGER, User.ROLE_CTO):
            # These roles are all branch-scoped: HR/COO dashboards bucket
            # their leave analytics (and other per-branch reporting) by
            # user.branch, so a NULL branch here silently drops them out
            # of every branch pie chart even though the user exists.
            if not cleaned.get('branch'):
                self.add_error('branch', "Select a branch for this employee.")
        elif role in (User.ROLE_HR, User.ROLE_SENIOR_HR, User.ROLE_ADMIN, User.ROLE_COO):
            if not cleaned.get('accessible_branches'):
                self.add_error('accessible_branches', "Select at least one branch this user can access.")
        return cleaned
from django.forms import modelformset_factory
from .models import ProductionAppraisal

class ProductionAppraisalForm(forms.ModelForm):
    class Meta:
        model = ProductionAppraisal
        fields = ['hour_slot', 'target', 'actual']
        widgets = {
            'hour_slot': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

ProductionAppraisalFormSet = modelformset_factory(
    ProductionAppraisal, form=ProductionAppraisalForm,
    extra=0, can_delete=False
)


# ---------------------------------------------------------------------------
# Training Feedback forms
# ---------------------------------------------------------------------------
from employees.models import TrainingFeedback


class TrainingFeedbackForm(forms.ModelForm):
    """Used by the Project Manager to submit training feedback for an
    onboarding employee. Rating and written feedback are required; the
    work sample attachment is optional evidence of what the employee
    actually produced during training."""

    class Meta:
        model = TrainingFeedback
        fields = ['rating', 'feedback_text', 'work_sample']
        widgets = {
            'rating': forms.Select(attrs={'class': 'form-control'}),
            'feedback_text': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5,
                'placeholder': 'Describe the employee\'s performance, attitude, and readiness...',
            }),
            'work_sample': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'rating': 'Overall Rating',
            'feedback_text': 'Feedback',
            'work_sample': 'Work Sample (optional)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rating'].required = True
        self.fields['work_sample'].required = False
        self.fields['feedback_text'].required = True


class TrainingDurationForm(forms.ModelForm):
    """HR-only form to adjust the training duration for an onboarding
    employee's training record."""

    class Meta:
        model = TrainingFeedback
        fields = ['training_duration_days']
        widgets = {
            'training_duration_days': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 365,
                'style': 'max-width:120px;',
            }),
        }
        labels = {
            'training_duration_days': 'Training Duration (days)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['training_duration_days'].required = True


class NoticePeriodDurationForm(forms.ModelForm):
    """HR-only form to adjust the notice period duration for an exiting
    employee."""

    class Meta:
        model = ResignationRequest
        fields = ['notice_period_days']
        widgets = {
            'notice_period_days': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
                'max': 365,
                'style': 'width:80px; min-width:80px;',
            }),
        }
        labels = {
            'notice_period_days': 'Notice Period (days)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['notice_period_days'].required = True


class TrainingReviewForm(forms.ModelForm):
    """HR-only form to add review notes and mark the training feedback as
    reviewed."""

    class Meta:
        model = TrainingFeedback
        fields = ['hr_notes']
        widgets = {
            'hr_notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Optional HR remarks or action notes...',
            }),
        }
        labels = {
            'hr_notes': 'HR Notes (optional)',
        }
