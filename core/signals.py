from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def _capture_previous_department(sender, instance, **kwargs):
    """Stash the department this user had before this save, so the
    post_save handler below can tell whether department actually
    changed rather than re-running on every unrelated save (login
    timestamp, profile edit, etc.)."""
    if not instance.pk:
        instance._previous_department_id = None
        return
    try:
        instance._previous_department_id = sender.objects.only('department_id').get(pk=instance.pk).department_id
    except sender.DoesNotExist:
        instance._previous_department_id = None


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def auto_assign_to_department_project(sender, instance, created, **kwargs):
    """When a user is newly created, or their department actually
    changes, and the (new) department has a default_project set (e.g. a
    branch's Production department), make sure they're a current
    ProjectAssignment on that project — so the project's Manager and
    Lead automatically see them as part of the team, with no separate
    step needed in Projects. Only fires on creation or an actual
    department change, not on every unrelated save of the user."""
    dept = instance.department
    if not dept or not dept.default_project_id:
        return
    if not created and getattr(instance, '_previous_department_id', None) == dept.id:
        return

    from projects.models import ProjectAssignment

    ProjectAssignment.objects.get_or_create(
        project=dept.default_project,
        user=instance,
        is_current=True,
        defaults={
            'role_on_project': instance.get_role_display() if hasattr(instance, 'get_role_display') else '',
            'start_date': timezone.localdate(),
        },
    )
