from django.urls import path
from payroll import views

app_name = 'payroll'

urlpatterns = [
    path('me/', views.my_payslips, name='my_payslips'),
    path('download/<int:payslip_id>/', views.download_payslip, name='download_payslip'),
    path('runs/', views.payroll_runs, name='runs'),
    path('runs/run-all/', views.run_payroll_for_all, name='run_payroll_for_all'),
    path('employee/<int:user_id>/', views.employee_payroll_history, name='employee_payroll_history'),
    path('employee/<int:user_id>/credit/', views.credit_salary, name='credit_salary'),
    path('structures/', views.salary_structures, name='salary_structures'),
    path('structures/<int:user_id>/edit/', views.edit_salary_structure, name='edit_salary_structure'),
    path('payslip/<int:payslip_id>/deductions/', views.edit_payslip_deductions, name='edit_payslip_deductions'),
    path('allowances/', views.allowance_list, name='allowance_list'),
    path('allowances/<int:cycle_id>/<int:user_id>/edit/', views.edit_allowance, name='edit_allowance'),
    path('allowances/<int:cycle_id>/export/', views.download_allowance_excel, name='download_allowance_excel'),
    path('allowance-requests/', views.my_allowance_requests, name='my_allowance_requests'),
    path('allowance-requests/approvals/', views.allowance_request_approvals, name='allowance_request_approvals'),
    path('allowance-requests/<int:request_id>/<str:decision>/', views.review_allowance_request, name='review_allowance_request'),
]
