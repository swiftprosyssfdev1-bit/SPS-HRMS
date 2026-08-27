from django.urls import path

from production_tracker import views

app_name = 'production_tracker'

urlpatterns = [
    path('my/', views.daily_entry, name='daily_entry'),
    path('my/history/', views.my_tracker, name='my_tracker'),
    path('employee-history/<int:user_id>/', views.employee_tracker_history, name='employee_tracker_history'),
    path('dashboard/', views.tracker_dashboard, name='dashboard'),
    path('dashboard/<int:user_id>/set-target/', views.set_target, name='set_target'),
    path('dashboard/project/<int:project_id>/set-target/', views.set_project_target, name='set_project_target'),
    path('export/excel/', views.export_tracker_excel, name='export_tracker_excel'),
]
