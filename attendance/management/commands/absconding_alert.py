from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from attendance.models import AttendanceRecord, AbsenceAlert
from leaves.models import LeaveRequest
from attendance.models import OvertimeRequest
from django.contrib.auth import get_user_model
from projects.models import ProjectAssignment
from django.core.mail import send_mail
from django.conf import settings

User = get_user_model()

class Command(BaseCommand):
    help = 'Check for unauthorized absence of > 3 consecutive days and create alerts'

    def handle(self, *args, **options):
        # We check the last 10 days to find 3 consecutive missing days
        today = timezone.localdate()
        threshold_days = 3
        count = 0
        
        employees = User.objects.filter(is_active=True, role='EMPLOYEE')
        
        for emp in employees:
            consecutive_absence = 0
            last_attendance_date = None
            
            # Check last 10 days backwards
            for i in range(1, 11):
                check_date = today - timedelta(days=i)
                
                # Skip weekends if required (assuming Sat=5, Sun=6)
                if check_date.weekday() in [5, 6]:
                    continue
                
                has_attendance = AttendanceRecord.objects.filter(user=emp, date=check_date).exists()
                if has_attendance:
                    if not last_attendance_date:
                        last_attendance_date = check_date
                    break # Stop counting backwards once we hit an attendance record
                
                # Check for approved leaves or WFH/Permission
                has_leave = LeaveRequest.objects.filter(
                    user=emp,
                    start_date__lte=check_date,
                    end_date__gte=check_date,
                    status='APPROVED'
                ).exists()
                
                if has_leave:
                    break # Leave counts as authorized
                
                has_permission = LeaveRequest.objects.filter(
                    user=emp,
                    request_type__in=['PERMISSION', 'WFH'],
                    permission_date=check_date,
                    status='APPROVED'
                ).exists()
                
                if has_permission:
                    break
                    
                has_ot = OvertimeRequest.objects.filter(
                    employee=emp,
                    date=check_date,
                    status='APPROVED'
                ).exists()
                
                if has_ot:
                    break
                    
                consecutive_absence += 1
                
            if consecutive_absence > threshold_days:
                # Need to create an alert if one doesn't exist recently
                existing_alert = AbsenceAlert.objects.filter(
                    employee=emp, 
                    resolved=False
                ).exists()
                
                if not existing_alert:
                    # Find project and manager
                    assignment = ProjectAssignment.objects.filter(
                        user=emp, is_current=True,
                        project__status__in=['PENDING', 'APPROVED', 'COMPLETED'],
                    ).select_related('project').order_by('-project__start_date').first()
                    
                    project = assignment.project if assignment else None
                    manager = project.manager if project else emp.manager
                    
                    alert = AbsenceAlert.objects.create(
                        employee=emp,
                        project=project,
                        reporting_manager=manager,
                        last_attendance_date=last_attendance_date,
                        consecutive_days=consecutive_absence
                    )
                    
                    # Send Email Alert
                    recipients = []
                    if manager and manager.email:
                        recipients.append(manager.email)
                    if project and project.gm and project.gm.email:
                        recipients.append(project.gm.email)
                        
                    # Also HR emails if any
                    hr_users = User.objects.filter(role__in=['HR', 'SENIOR_HR'], is_active=True)
                    for hr in hr_users:
                        if hr.email:
                            recipients.append(hr.email)
                            
                    # CC the absconding employee so they are alerted directly
                    if emp.email:
                        recipients.append(emp.email)
                            
                    recipients = list(set(recipients))
                    if recipients:
                        subject = f"Absence Alert: {emp.get_full_name()} ({emp.employee_id})"
                        body = f"Employee {emp.get_full_name()} has {consecutive_absence} consecutive days of unauthorized absence.\n"
                        body += f"Last attendance date: {last_attendance_date}\n"
                        body += f"Project: {project.name if project else 'N/A'}\n"
                        body += f"Reporting Manager: {manager.get_full_name() if manager else 'N/A'}\n"
                        
                        try:
                            send_mail(
                                subject,
                                body,
                                settings.DEFAULT_FROM_EMAIL,
                                recipients,
                                fail_silently=True
                            )
                            alert.email_sent = True
                            alert.save()
                        except Exception as e:
                            pass
                    
                    count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully created {count} absence alerts.'))
