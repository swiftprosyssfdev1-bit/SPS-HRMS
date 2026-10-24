"""Recent-changes audit helpers.

Usage in a view:
    before = snapshot(obj, fields)          # BEFORE binding a ModelForm to obj
    ... save ...
    changes = diff_snapshots(before, snapshot(obj, fields))
    log_activity(request, ActivityLog.UPDATED, target_user, "Updated profile of X", changes)

Snapshots must be taken before a ModelForm is built on the instance,
because ModelForm validation writes the submitted values onto it.
"""
import logging

from django.db import models
from django.db.models.fields.files import FieldFile

logger = logging.getLogger(__name__)

EMPTY = '\u2014'
HIDDEN = '(hidden)'

# Fields whose values must never be shown in the log (only that they changed).
SENSITIVE_FIELDS = {
    'bank_account', 'account_number', 'pan_no', 'aadhar_no', 'uan_pf_number',
    'esi_number', 'ifsc_code',
}

USER_AUDIT_FIELDS = [
    'first_name', 'last_name', 'email', 'username', 'role', 'employee_id',
    'department', 'manager', 'branch', 'date_joined_company',
    'phone_country_code', 'phone', 'can_access_all_branches', 'accessible_branches',
]


def _display(instance, name):
    field = instance._meta.get_field(name)
    if field.many_to_many:
        if not instance.pk:
            return EMPTY
        return ', '.join(sorted(str(o) for o in getattr(instance, name).all())) or EMPTY
    if field.choices:
        value = getattr(instance, f'get_{name}_display')()
    elif field.is_relation:
        obj = getattr(instance, name, None)
        value = str(obj) if obj else ''
    else:
        value = getattr(instance, name)
        if isinstance(value, FieldFile):
            value = value.name or ''
    if value is None or value == '':
        return EMPTY
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def snapshot(instance, fields):
    """{field_name: display_value} for the listed fields."""
    return {name: _display(instance, name) for name in fields}


def all_field_names(instance, exclude=('id', 'user')):
    """Concrete, non auto-timestamp fields of a model instance."""
    names = []
    for f in instance._meta.concrete_fields:
        if f.name in exclude or isinstance(f, models.AutoField):
            continue
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            continue
        names.append(f.name)
    return names


def snapshot_all(instance, exclude=('id', 'user')):
    return snapshot(instance, all_field_names(instance, exclude))


def diff_snapshots(before, after, hide_values=False):
    """List of {'field', 'old', 'new'} for every field whose value changed.
    Sensitive fields (or everything, with hide_values=True) show only that a
    change happened, not the values."""
    changes = []
    for name, new in after.items():
        old = before.get(name, EMPTY)
        if old == new:
            continue
        hidden = hide_values or name in SENSITIVE_FIELDS
        changes.append({
            'field': name.replace('_', ' ').capitalize(),
            'old': HIDDEN if hidden else old,
            'new': HIDDEN if hidden else new,
        })
    return changes


def log_activity(request, action, target, summary, changes=None):
    """Record one entry. Never raises: a logging problem must not break the
    employee action that has already succeeded."""
    from core.models import ActivityLog
    try:
        actor = getattr(request, 'user', None)
        if actor is not None and not actor.is_authenticated:
            actor = None
        ActivityLog.objects.create(
            actor=actor,
            actor_name=(actor.get_full_name() or actor.username) if actor else '',
            actor_role=actor.get_role_display() if actor else '',
            action=action,
            target=target,
            target_name=(target.get_full_name() or target.username) if target else '',
            branch=getattr(target, 'branch', None) if target else None,
            summary=summary[:255],
            changes=changes or [],
        )
    except Exception:
        logger.exception('Could not write activity log entry')
