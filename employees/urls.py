from django.urls import path
from employees import views

app_name = 'employees'

urlpatterns = [
    path('me/', views.my_profile, name='my_profile'),
    path('me/upload-document/', views.upload_own_document, name='upload_own_document'),
    path('me/documents/<int:doc_id>/delete/', views.delete_own_document, name='delete_own_document'),
    path('letters/issue/', views.issue_letter, name='issue_letter'),
    path('letters/mine/', views.my_letters, name='my_letters'),
    path('documents/<int:doc_id>/download/', views.download_document, name='download_document'),
    
    path('resign/', views.apply_resignation, name='apply_resignation'),
    path('resignation/', views.resignation_list, name='resignation_list'),
    path('notice-period/', views.notice_period_list, name='notice_period_list'),
    path('resignation/<int:resignation_id>/approve/', views.approve_resignation, name='approve_resignation'),
    path('resignation/<int:resignation_id>/reject/', views.reject_resignation, name='reject_resignation'),
    path('resignation/<int:resignation_id>/negotiate/', views.negotiate_resignation, name='negotiate_resignation'),
    path('resignation/<int:resignation_id>/accept-offer/', views.accept_resignation_offer, name='accept_resignation_offer'),
    path('resignation/<int:resignation_id>/quit-anyway/', views.quit_after_negotiation, name='quit_after_negotiation'),
    path('directory/', views.employee_directory, name='directory'),
    path('joiners-exits/', views.joiners_exits_list, name='joiners_exits'),
    path('wfh-directory/', views.wfh_directory, name='wfh_directory'),
    path('<int:user_id>/', views.employee_detail, name='employee_detail'),
    path('<int:user_id>/edit-profile/', views.edit_employee_profile, name='edit_employee_profile'),
    path('<int:user_id>/reset-credentials/', views.reset_employee_credentials, name='reset_employee_credentials'),
    path('<int:user_id>/update-status/', views.update_employee_status, name='update_employee_status'),
    path('<int:user_id>/update-salary/', views.update_salary_structure, name='update_salary_structure'),
    path('<int:user_id>/delete-document/<int:doc_id>/', views.delete_document, name='delete_document'),
    path('<int:user_id>/delete/', views.delete_employee, name='delete_employee'),
    path('<int:user_id>/change-role/', views.change_role, name='change_role'),
    path('my-department/', views.my_department, name='my_department'),
    path('<int:user_id>/team-member/', views.team_member_detail, name='team_member_detail'),
    path('resignation/mine/', views.my_resignation, name='my_resignation'),
    path('<int:user_id>/toggle-branch-access/', views.toggle_branch_admin_access, name='toggle_branch_admin_access'),
    path('onboard-hr/', views.onboard_hr, name='onboard_hr'),
    path('import/', views.import_employees, name='import_employees'),
    path('import/template/', views.download_import_template, name='import_template'),

    path('me/edit/', views.edit_my_profile, name='edit_my_profile'),

    path('appraisals/dashboard/', views.appraisal_dashboard, name='appraisal_dashboard'),
    path('appraisals/entry/<int:user_id>/<str:date_str>/', views.appraisal_entry, name='appraisal_entry'),
    path('appraisals/my/', views.my_appraisals, name='my_appraisals'),
    path('appraisals/export/', views.export_appraisals, name='appraisal_export'),

    # Training Feedback
    path('training-feedback/', views.training_feedback_list, name='training_feedback_list'),
    path('training-feedback/<int:tf_id>/review/', views.review_training_feedback, name='review_training_feedback'),
    path('<int:user_id>/training-feedback/submit/', views.submit_training_feedback, name='submit_training_feedback'),
    path('my-team/training/', views.my_team_training, name='my_team_training'),

]
