from django import forms

from employees.models import EmployeeProfile
from .models import DailyProduction, ProductionEvent, ProductionGrouping, ProductionProcess


class CreatableEventField(forms.CharField):
    """A text field that looks up a ProductionEvent by name, creating it if it
    doesn't exist yet. Lets employees type any event name freely while still
    linking to a proper master-data record."""

    def to_python(self, value):
        value = super().to_python(value).strip()
        if not value:
            return None
        obj, _ = ProductionEvent.objects.get_or_create(
            name__iexact=value,
            defaults={'name': value, 'is_active': True},
        )
        return obj

    def prepare_value(self, value):
        if isinstance(value, int):
            try:
                return ProductionEvent.objects.get(pk=value).name
            except ProductionEvent.DoesNotExist:
                return ''
        if hasattr(value, 'name'):
            return value.name
        return value or ''


class DailyProductionForm(forms.ModelForm):
    event = CreatableEventField(
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'list': 'event-datalist'}),
    )

    class Meta:
        model = DailyProduction
        fields = [
            'work_type', 'shift', 'project', 'process', 'event', 'grouping',
            'target_day', 'error_count', 'reason', 'pl_comments', 'pm_comments',
        ]
        widgets = {
            'reason': forms.TextInput(),
            'pl_comments': forms.Textarea(attrs={'rows': 2}),
            'pm_comments': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['shift'].widget = forms.Select(choices=[('', '---------')] + list(EmployeeProfile.SHIFT_CHOICES))
        self.fields['process'].queryset = ProductionProcess.objects.filter(is_active=True)
        self.fields['grouping'].queryset = ProductionGrouping.objects.filter(is_active=True)
        # Both Production and QA rows must be tied to a project.
        self.fields['project'].required = True
        self.fields['process'].required = False
        self.fields['grouping'].required = False

        if user:
            from django.db.models import Q
            from projects.models import Project
            from production_tracker.views import _is_production_employee

            is_qa_employee = bool(user.department and user.department.is_qa)

            if user.branch and is_qa_employee:
                # QA employees pick their project for the day from the
                # full list of their branch's projects (not just ones
                # they already have a current assignment on) — a QA
                # employee may need to log against any project running
                # in their branch, not only the one their department
                # auto-assigns them to.
                self.fields['project'].queryset = Project.objects.filter(
                    Q(manager__branch=user.branch) |
                    Q(created_by__branch=user.branch) |
                    Q(assignments__user__branch=user.branch)
                ).exclude(status='COMPLETED').distinct().order_by('name')
            else:
                # Production employees only see projects they are
                # actually a current team member on — showing every
                # branch project here let people accidentally log hours
                # against the wrong project.
                self.fields['project'].queryset = Project.objects.filter(
                    assignments__user=user,
                    assignments__is_current=True
                ).distinct()

        for field in self.fields.values():
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (existing + ' form-control').strip()

        if user:
            from production_tracker.views import _is_project_lead_or_tl

            is_tl_pl = _is_project_lead_or_tl(user)
            is_pm_gm = user.role in ('PROJECT_MANAGER', 'GENERAL_MANAGER', 'MANAGER', 'ADMIN', 'COO')

            if user.role == 'EMPLOYEE':
                # Employees cannot edit target, either comment field, or
                # Work Type â€” Work Type is fixed by department (see
                # daily_entry view), not a free choice on the form.
                self.fields['target_day'].disabled = True
                self.fields['pl_comments'].disabled = True
                self.fields['pm_comments'].disabled = True
                self.fields['work_type'].disabled = True
            elif is_tl_pl and not is_pm_gm:
                # PL/TL can edit PL's comments only — not PM comments
                self.fields['pm_comments'].disabled = True
            elif is_pm_gm:
                # PM/GM can edit PM comments only — not PL's comments
                self.fields['pl_comments'].disabled = True
