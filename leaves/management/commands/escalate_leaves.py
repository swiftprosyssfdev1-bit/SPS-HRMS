from django.core.management.base import BaseCommand
from django.utils import timezone
from leaves.models import LeaveRequest, LeaveEscalationHistory
from projects.models import ProjectAssignment

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
            original_approver = req.current_approver
            next_approver = None
            reason = "SLA breached"

            # Determine next approver based on project hierarchy
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
                    continue
                else:
                    # Fallback
                    next_approver = project.gm
            
            if not next_approver:
                # If not project based or we can't figure it out, skip
                continue

            # Log escalation
            LeaveEscalationHistory.objects.create(
                leave_request=req,
                original_approver=original_approver,
                current_approver=next_approver,
                escalation_level=req.escalation_level + 1,
                escalation_reason=reason,
                submitted_timestamp=req.applied_at,
                sla_deadline=req.sla_deadline
            )

            # Update request
            req.current_approver = next_approver
            req.is_escalated = True
            req.escalated_at = now
            req.escalation_level += 1
            # Reset SLA deadline for the new approver
            req.sla_deadline = now + timezone.timedelta(hours=24)
            req.save()
            count += 1
            
        self.stdout.write(self.style.SUCCESS(f'Successfully escalated {count} leave requests.'))
