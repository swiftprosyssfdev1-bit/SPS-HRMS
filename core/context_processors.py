# core/context_processors.py
from django.utils import timezone
from django.db.models import Q
from leaves.models import LeaveRequest
from leaves.utils import not_expired_leaves
from core.models import User, Branch
from core.utils import user_can_switch_branch, get_manager_team
from employees.models import ResignationRequest, TrainingFeedback
from attendance.models import OvertimeRequest
from increments.models import IncrementRequest
from increments.views import due_for_annual_increment_count
from queries.models import EmployeeQuery
from payroll.models import AllowanceRequest


def notifications(request):
    if not request.user.is_authenticated:
        return {}
    user = request.user
    today = timezone.localdate()
    if user.role in ('ADMIN', 'HR', 'SENIOR_HR'):
        active_branch = getattr(request, 'active_branch', None)
        leave_qs = LeaveRequest.objects.filter(status='PENDING_HR').exclude(user=user)
        if active_branch:
            leave_qs = leave_qs.filter(user__branch=active_branch)
        leave_approval_count = leave_qs.count()

        resignation_qs = ResignationRequest.objects.filter(status='PENDING').exclude(user=user)
        overtime_hr_qs = OvertimeRequest.objects.filter(status='PENDING_HR')
        increment_reports_qs = IncrementRequest.objects.filter(status='REPORT_SUBMITTED')
        increment_coo_ready_qs = IncrementRequest.objects.exclude(
            status__in=['PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED']
        )
        increment_salary_update_qs = IncrementRequest.objects.filter(status='COO_DECIDED')
        query_qs = EmployeeQuery.objects.filter(recipient_role='HR', status='OPEN')
        allowance_hr_qs = AllowanceRequest.objects.filter(status=AllowanceRequest.STATUS_PENDING_HR).exclude(user=user)
        training_feedback_hr_qs = TrainingFeedback.objects.filter(status='SUBMITTED', employee__profile__status='ONBOARDING')

        if active_branch:
            resignation_qs = resignation_qs.filter(user__branch=active_branch)
            overtime_hr_qs = overtime_hr_qs.filter(employee__branch=active_branch)
            increment_reports_qs = increment_reports_qs.filter(user__branch=active_branch)
            increment_coo_ready_qs = increment_coo_ready_qs.filter(user__branch=active_branch)
            increment_salary_update_qs = increment_salary_update_qs.filter(user__branch=active_branch)
            query_qs = query_qs.filter(user__branch=active_branch)
            allowance_hr_qs = allowance_hr_qs.filter(user__branch=active_branch)
            training_feedback_hr_qs = training_feedback_hr_qs.filter(employee__branch=active_branch)

        allowance_hr_count = allowance_hr_qs.count()
        training_feedback_hr_count = training_feedback_hr_qs.count()
        resignation_count = resignation_qs.count()
        overtime_hr_count = overtime_hr_qs.count()
        increment_reports_count = increment_reports_qs.count()
        increment_coo_ready_count = increment_coo_ready_qs.count()
        increment_salary_update_count = increment_salary_update_qs.count()
        increment_due_count = due_for_annual_increment_count(active_branch)
        # HR's queue is shared across HR/Admin staff. A query only needs
        # attention if no one has replied yet, or the employee wrote again
        # after the last reply — not just because it's still technically OPEN
        # (which stays true until the employee marks it resolved).
        query_count = 0
        for q in query_qs.prefetch_related('messages'):
            last_message = q.messages.last()
            if last_message is None or last_message.sender_id == q.user_id:
                query_count += 1

        return {
            'leave_approval_count': leave_approval_count,
            'resignation_count': resignation_count,
            'overtime_hr_count': overtime_hr_count,
            'increment_reports_count': increment_reports_count,
            'increment_coo_ready_count': increment_coo_ready_count,
            'increment_salary_update_count': increment_salary_update_count,
            'increment_due_count': increment_due_count,
            'query_count': query_count,
            'allowance_hr_count': allowance_hr_count,
            'training_feedback_hr_count': training_feedback_hr_count,
            'notification_count': (
                leave_approval_count + resignation_count + overtime_hr_count
                + increment_reports_count + increment_salary_update_count + query_count
                + allowance_hr_count + increment_due_count + training_feedback_hr_count
            ),
        }
    elif user.role == 'COO':
        increment_coo_pending_count = IncrementRequest.objects.filter(status='PENDING_COO').count()
        leave_coo_count = LeaveRequest.objects.filter(status='PENDING_COO').count()
        return {
            'increment_coo_pending_count': increment_coo_pending_count,
            'leave_coo_count': leave_coo_count,
            'notification_count': increment_coo_pending_count + leave_coo_count,
        }
    elif user.is_manager() or user.is_cto():
        active_branch = getattr(request, 'active_branch', None)
        if user.is_cto():
            team_leave_qs = LeaveRequest.objects.filter(status='PENDING_MANAGER', user__department__name__iexact='Software')
            overtime_manager_count = 0
        elif user.role == 'GENERAL_MANAGER':
            # A GM's actionable badge counts only their own direct team's
            # requests — matching leave_approvals(). PMs never appear here
            # since PM leave routes straight to the COO (view-only for GM).
            team = get_manager_team(user).exclude(role='PROJECT_MANAGER')
            team_leave_qs = LeaveRequest.objects.filter(user__in=team, status='PENDING_MANAGER')
            overtime_manager_count = 0
        else:
            team = get_manager_team(user)
            team_leave_qs = LeaveRequest.objects.filter(user__in=team, status='PENDING_MANAGER')
            overtime_manager_count = OvertimeRequest.objects.filter(status='PENDING_MANAGER', employee__in=team).count()
        leave_approval_count = team_leave_qs.count()
        my_leave_count = LeaveRequest.objects.filter(
            user=user, status__in=['PENDING_MANAGER', 'PENDING_HR']
        ).count()
        query_qs = EmployeeQuery.objects.filter(
            recipient_role='MANAGER', recipient_user=user, status='OPEN'
        ).prefetch_related('messages')
        # Only count queries where it's actually the manager's turn to act —
        # i.e. no reply from them yet, or the employee messaged again since
        # their last reply. A query the manager already answered stays OPEN
        # (waiting on the employee to confirm/resolve) but shouldn't keep
        # showing up as something the manager still needs to do.
        query_count = 0
        for q in query_qs:
            last_message = q.messages.last()
            if last_message is None or last_message.sender_id == q.user_id:
                query_count += 1
        # GM now gets personally assigned as feedback_manager on PM
        # increment requests, same as any other manager tier.
        increment_feedback_count = IncrementRequest.objects.filter(
            status='PENDING', feedback_manager=user
        ).count()
        if user.role == 'GENERAL_MANAGER':
            # Allowance Requests is removed entirely for GM — see base.html
            # and payroll views. Keep this at 0 so it never contributes to
            # notification_count for a GM.
            allowance_pm_count = 0
            pm_team_ids = []
        else:
            from projects.models import ProjectAssignment
            pm_team_ids = ProjectAssignment.objects.filter(
                project__manager=user, is_current=True
            ).values_list('user_id', flat=True)
            allowance_pm_count = AllowanceRequest.objects.filter(
                user_id__in=pm_team_ids, status=AllowanceRequest.STATUS_PENDING_PM
            ).count()
        training_feedback_pm_count = 0
        projects_needing_setup_count = 0
        if user.role == 'PROJECT_MANAGER':
            training_feedback_pm_count = TrainingFeedback.objects.filter(
                employee_id__in=pm_team_ids, status='PENDING',
                employee__profile__status='ONBOARDING'
            ).count()
            
            from projects.models import Project
            projects_needing_setup_count = Project.objects.filter(
                manager=user, lead__isnull=True
            ).exclude(status='COMPLETED').count()

        return {
            'leave_approval_count': leave_approval_count,
            'my_leave_count': my_leave_count,
            'overtime_manager_count': overtime_manager_count,
            'query_count': query_count,
            'increment_feedback_count': increment_feedback_count,
            'allowance_pm_count': allowance_pm_count,
            'training_feedback_pm_count': training_feedback_pm_count,
            'projects_needing_setup_count': projects_needing_setup_count,
            'notification_count': leave_approval_count + my_leave_count + overtime_manager_count + query_count + increment_feedback_count + allowance_pm_count + training_feedback_pm_count + projects_needing_setup_count,
        }
    else:
        my_leave_count = LeaveRequest.objects.filter(
            user=user, status__in=['PENDING_MANAGER', 'PENDING_HR']
        ).count()
        from projects.models import Project, ProjectAssignment
        is_project_lead_or_tl = Project.objects.filter(lead=user).exists() or ProjectAssignment.objects.filter(
            user=user, is_current=True, role_on_project__iexact='Team Leader'
        ).exists()
        return {
            'my_leave_count': my_leave_count,
            'is_project_lead_or_tl': is_project_lead_or_tl,
            'notification_count': my_leave_count,
        }


def branch_context(request):
    if not request.user.is_authenticated:
        return {}
    user = request.user
    can_switch = user_can_switch_branch(user)
    
    if user.role in ('ADMIN', 'SENIOR_HR', 'COO') or getattr(user, 'can_access_all_branches', False):
        allowed_branches_qs = Branch.objects.all()
    else:
        allowed_branches_qs = user.accessible_branches.all()
        if user.branch_id:
            allowed_branches_qs = allowed_branches_qs | Branch.objects.filter(id=user.branch_id)
            
    branch_order = ['B01', 'B02', 'B03', 'B04']  # Chennai, Thindivanam, Kancheepuram, Madurai
    branches = list(allowed_branches_qs.distinct())
    branches.sort(key=lambda b: branch_order.index(b.code) if b.code in branch_order else 99)
    
    return {
        'active_branch': getattr(request, 'active_branch', None),
        'can_switch_branch': can_switch,
        'all_branches': branches if can_switch else [],
    }
