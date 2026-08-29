from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
from payroll.models import SalaryStructure, Payslip, Allowance, AllowanceRequest

# Numeric-only handler: allows digits and a single decimal point as the
# user types, stripping anything else (letters, symbols, extra dots).
DECIMAL_ONINPUT = (
    "this.value = this.value.replace(/[^0-9.]/g, '')"
    ".replace(/(\\..*)\\./g, '$1')"
)


class SalaryStructureForm(forms.ModelForm):
    class Meta:
        model = SalaryStructure
        fields = ['basic', 'hra', 'conveyance', 'other_allowances', 'bonus', 'pf_applicable', 'pf_percent']
        widgets = {
            'basic': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'hra': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'conveyance': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'other_allowances': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'bonus': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'pf_percent': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'pf_applicable':
                field.widget.attrs.setdefault('class', 'form-control')
        self.fields['basic'].validators.append(MinValueValidator(0))
        self.fields['hra'].validators.append(MinValueValidator(0))
        self.fields['conveyance'].validators.append(MinValueValidator(0))
        self.fields['other_allowances'].validators.append(MinValueValidator(0))
        self.fields['bonus'].validators.append(MinValueValidator(0))
        self.fields['pf_percent'].validators.append(MinValueValidator(0))
        self.fields['pf_percent'].validators.append(MaxValueValidator(100))

class PayslipDeductionsForm(forms.ModelForm):
    """Lets HR fill in the per-cycle deduction figures (TDS, P.Tax/LWF, and
    any bonus recovery) that aren't derivable from attendance/salary
    structure and vary payslip to payslip."""
    class Meta:
        model = Payslip
        fields = ['tds', 'p_tax_lwf', 'bonus_deduction']
        widgets = {
            'tds': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'p_tax_lwf': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'bonus_deduction': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
        }


class AllowanceForm(forms.ModelForm):
    """HR fills in an employee's allowance line-item for a given cycle
    (cycle/user/created_by are set explicitly in the view, not via the form)."""
    class Meta:
        model = Allowance
        fields = [
            'project', 'attendance_bonus', 'travel_allowance', 'internet_allowance',
            'incentive', 'night_shift_allowance', 'account_no', 'ifsc',
            'cleared_from_bank', 'cleared_date', 'ref_no', 'remarks',
        ]
        widgets = {
            'project': forms.TextInput(attrs={'class': 'form-control'}),
            'attendance_bonus': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'travel_allowance': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'internet_allowance': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'incentive': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'night_shift_allowance': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'account_no': forms.TextInput(attrs={'class': 'form-control'}),
            'ifsc': forms.TextInput(attrs={'class': 'form-control'}),
            'cleared_from_bank': forms.TextInput(attrs={'class': 'form-control'}),
            'cleared_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'ref_no': forms.TextInput(attrs={'class': 'form-control'}),
            'remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ['attendance_bonus', 'travel_allowance', 'internet_allowance',
                     'incentive', 'night_shift_allowance']:
            self.fields[name].validators.append(MinValueValidator(0))


class AllowanceRequestForm(forms.ModelForm):
    """Employee-facing form to request an allowance for a given month,
    with mandatory proof (PDF/JPG/PNG)."""
    class Meta:
        model = AllowanceRequest
        fields = ['allowance_type', 'amount', 'month', 'year', 'reason', 'proof']
        widgets = {
            'allowance_type': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT}),
            'month': forms.Select(attrs={'class': 'form-control'}),
            'year': forms.Select(attrs={'class': 'form-control'}),
            'reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brief reason (optional)'}),
            'proof': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['amount'].validators.append(MinValueValidator(0.01))
        self.fields['proof'].required = True

    def clean_proof(self):
        proof = self.cleaned_data.get('proof')
        if proof:
            ext = proof.name.rsplit('.', 1)[-1].lower()
            if ext not in ('pdf', 'jpg', 'jpeg', 'png'):
                raise forms.ValidationError("Only PDF, JPG or PNG files are allowed.")
        return proof
