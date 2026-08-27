from django.contrib import admin
from payroll.models import SalaryStructure, PayrollRun, Payslip, AllowanceCycle, Allowance

admin.site.register(SalaryStructure)
admin.site.register(PayrollRun)
admin.site.register(Payslip)
admin.site.register(AllowanceCycle)
admin.site.register(Allowance)
