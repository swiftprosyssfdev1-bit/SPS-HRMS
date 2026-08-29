from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from core.models import User
from increments.models import IncrementFeedback

DECIMAL_ONINPUT = (
    "this.value = this.value.replace(/[^0-9.]/g, '')"
    ".replace(/(\\..*)\\./g, '$1')"
)

MANAGER_TIER_ROLES = ['MANAGER', 'GENERAL_MANAGER', 'CTO']

REPORT_FILE_EXTENSIONS = ['doc', 'docx', 'xls', 'xlsx', 'pdf']


class IncrementRequestForm(forms.Form):
    user = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(), label="Employee / Project Manager Name",
        widget=forms.CheckboxSelectMultiple,
    )
    feedback_manager = forms.ModelChoiceField(
        queryset=User.objects.filter(Q(role='PROJECT_MANAGER') | Q(role='CTO') | Q(role='GENERAL_MANAGER')), required=False,
        label="Project Manager / CTO / General Manager (for Feedback)",
        help_text="For an Employee: pick their Project Manager or CTO. For a Project Manager: pick their General Manager."
    )
    current_basic = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, label="Current Basic Salary", required=False,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    requested_basic = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False,
        label="Employee's Expected Basic Salary",
        help_text="What the employee is asking for. Leave blank if they haven't given a number.",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    effective_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label="Notes (optional)")

    def __init__(self, *args, acting_user=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        users_qs = User.objects.filter(Q(role='EMPLOYEE') | Q(role='PROJECT_MANAGER'))
        if acting_user:
            users_qs = users_qs.exclude(id=acting_user.id)
        if branch:
            users_qs = users_qs.filter(branch=branch)
        self.fields['user'].queryset = users_qs

        manager_qs = User.objects.filter(Q(role='PROJECT_MANAGER') | Q(role='CTO') | Q(role='GENERAL_MANAGER'))
        if branch:
            manager_qs = manager_qs.filter(Q(branch=branch) | Q(role='CTO'))
        self.fields['feedback_manager'].queryset = manager_qs
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        emp_users = cleaned.get('user')
        feedback_manager = cleaned.get('feedback_manager')
        if not emp_users:
            self.add_error('user', "Select at least one employee.")
            return cleaned
        # A manual Current Basic Salary is only meaningful when requesting
        # for a single employee — for a batch, each employee's own salary
        # (looked up automatically) is used instead.
        if len(emp_users) == 1 and cleaned.get('current_basic') is None:
            self.add_error('current_basic', "This field is required.")
        if len(emp_users) == 1:
            target = list(emp_users)[0]
            if not feedback_manager:
                if target.role == 'PROJECT_MANAGER':
                    self.add_error('feedback_manager', "Select the Project Manager's General Manager to send this increment for feedback.")
                else:
                    self.add_error('feedback_manager', "Select the employee's assigned Project Manager to send this increment for feedback.")
            elif target.role == 'PROJECT_MANAGER' and feedback_manager.role != 'GENERAL_MANAGER':
                self.add_error('feedback_manager', "A Project Manager's increment must be sent to their General Manager for feedback.")
            elif target.role == 'EMPLOYEE' and feedback_manager.role not in ('PROJECT_MANAGER', 'CTO'):
                self.add_error('feedback_manager', "Select the employee's Project Manager or CTO for feedback.")
        return cleaned


class ManagerIncrementRequestForm(forms.Form):
    """HR uses this to request an increment directly for a Manager /
    General Manager / CTO. No peer-feedback step — this goes straight to
    the COO along with the uploaded performance report, since these roles
    report to the COO, not to another manager. A Project Manager's
    increment does NOT use this form — PMs go through IncrementRequestForm
    below, routed to their General Manager for feedback first."""
    user = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(), label="Name",
        widget=forms.CheckboxSelectMultiple,
    )
    current_basic = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, label="Current Basic Salary", required=False,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    increment_percent = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=0, label="Increment Percentage (%)",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    effective_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    report_file = forms.FileField(
        required=True, label="Upload Performance Report",
        help_text="Accepted formats: DOC, DOCX, XLS, XLSX, PDF.",
        validators=[FileExtensionValidator(allowed_extensions=REPORT_FILE_EXTENSIONS)],
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label="Notes for COO")

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(role__in=MANAGER_TIER_ROLES)
        if acting_user:
            qs = qs.exclude(id=acting_user.id)
        if branch:
            qs = qs.filter(branch=branch)
        self.fields['user'].queryset = qs
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        mgr_users = cleaned.get('user')
        if not mgr_users:
            self.add_error('user', "Select at least one manager.")
            return cleaned
        # A manual Current Basic Salary only applies when requesting for a
        # single manager — for a batch, each manager's own salary is used.
        if len(mgr_users) == 1 and cleaned.get('current_basic') is None:
            self.add_error('current_basic', "This field is required.")
        return cleaned


class IncrementFeedbackForm(forms.ModelForm):
    suggested_percent = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=0, required=True,
        label="Suggested Increment (%)",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT, 'class': 'form-control'})
    )
    report_file = forms.FileField(
        required=True, label="Upload Performance Report",
        help_text="Accepted formats: DOC, DOCX, XLS, XLSX, PDF.",
        validators=[FileExtensionValidator(allowed_extensions=REPORT_FILE_EXTENSIONS)],
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = IncrementFeedback
        fields = ['report_file', 'suggested_percent', 'description']
        widgets = {
            'description': forms.Textarea(attrs={
                'rows': 3, 'class': 'form-control',
                'placeholder': "Any additional comments about this employee's overall performance...",
            }),
        }
