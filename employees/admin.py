from django.contrib import admin
from employees.models import EmployeeProfile, EmployeeDocument, ResignationRequest, TrainingFeedback

admin.site.register(EmployeeProfile)
admin.site.register(EmployeeDocument)
admin.site.register(ResignationRequest)


@admin.register(TrainingFeedback)
class TrainingFeedbackAdmin(admin.ModelAdmin):
    list_display = ['employee', 'training_start_date', 'training_duration_days', 'training_end_date', 'status', 'submitted_by', 'reviewed_by']
    list_filter = ['status', 'rating']
    search_fields = ['employee__username', 'employee__first_name', 'employee__last_name']
    readonly_fields = ['training_end_date', 'days_remaining', 'created_at']
