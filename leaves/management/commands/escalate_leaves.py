from django.core.management.base import BaseCommand
from django.utils import timezone
from leaves.models import LeaveRequest, LeaveEscalationHistory
from projects.models import ProjectAssignment
from core.models import User


class Command(BaseCommand):
    help = 'Escalate pending leave requests that have exceeded their SLA'

    def handle(self, *args, **options):
        now = timezone.now()
        pending_requests = LeaveRequest.objects.filter(
            status='PENDING_MANAGER',
            sla_deadline__lt=now
        )

        count = 0
        for req in pending_requests:
            dept = req.user.department

            # QA and Software department requests skip the GM level
            # entirely on SLA breach — they go straight to HR.
            if req.user.is_software_dept() or (dept and dept.is_qa):
                self._escalate_to_hr(req, now)
                count += 1
                continue

            # Everyone else (Production / default department) escalates
            # up the project hierarchy: PL -> PM -> GM.
            if self._escalate_production(req, now):
                count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully escalated {count} leave requests.'))

    def _escalate_production(self, req, now):
        original_approver = req.current_approver
        next_approver = None
        reason = "SLA breached"

        assignment = ProjectAssignment.objects.filter(
            user=req.user, is_current=True,
            project__status__in=['PENDING', 'APPROVED', 'COMPLETED'],
        ).select_related('project').order_by('-project__start_date').first()

        if assignment and assignment.project:
            project = assignment.project
            if req.current_approver == project.lead:
                next_approver = project.manager
                reason = "Escalated from PL to PM due to SLA breach"
            elif req.current_approver == project.manager:
                next_approver = project.gm
                reason = "Escalated from PM to GM due to SLA breach"
            elif req.current_approver == project.gm:
                # GM is highest project level, stop escalating
                return False
            else:
                next_approver = project.gm

        if not next_approver:
            # No project chain to climb (e.g. a department-manager-only
            # approver with no PM/GM above them) — fall back to a GM for
            # this employee's branch so a Production request never gets
            # stuck with nobody able to act on it.
            next_approver = User.objects.filter(
                role='GENERAL_MANAGER', branch=req.user.branch
            ).exclude(id=req.user_id).first() or User.objects.filter(
                role='GENERAL_MANAGER'
            ).exclude(id=req.user_id).first()
            reason = "Escalated to GM due to SLA breach (no project chain)"

        if not next_approver:
            return False

        LeaveEscalationHistory.objects.create(
            leave_request=req,
            original_approver=original_approver,
            current_approver=next_approver,
            escalation_level=req.escalation_level + 1,
            escalation_reason=reason,
            submitted_timestamp=req.applied_at,
            sla_deadline=req.sla_deadline
        )

        req.current_approver = next_approver
        req.is_escalated = True
        req.escalated_at = now
        req.escalation_level += 1
        req.sla_deadline = now + timezone.timedelta(hours=24)
        req.save()
        return True

    def _escalate_to_hr(self, req, now):
        original_approver = req.current_approver

        LeaveEscalationHistory.objects.create(
            leave_request=req,
            original_approver=original_approver,
            current_approver=None,
            escalation_level=req.escalation_level + 1,
            escalation_reason="Escalated straight to HR due to SLA breach (QA/Software dept)",
            submitted_timestamp=req.applied_at,
            sla_deadline=req.sla_deadline
        )

        req.status = 'PENDING_HR'
        req.current_approver = None
        req.is_escalated = True
        req.escalated_at = now
        req.escalation_level += 1
        req.sla_deadline = None
        req.save()
