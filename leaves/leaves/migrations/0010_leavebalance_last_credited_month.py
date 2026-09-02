from django.db import migrations, models
from django.utils import timezone


def mark_as_credited_through_now(apps, schema_editor):
    """Existing balances stay untouched — we only mark them as already
    credited through the current month, so the new monthly-accrual
    command starts adding from here on instead of backfilling a large
    one-off catch-up against balances that were built under the old
    flat-12-upfront system."""
    LeaveBalance = apps.get_model('leaves', 'LeaveBalance')
    current_month = timezone.localdate().replace(day=1)
    LeaveBalance.objects.all().update(last_credited_month=current_month)


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0009_leavebalance_combined'),
    ]

    operations = [
        migrations.AddField(
            model_name='leavebalance',
            name='last_credited_month',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(mark_as_credited_through_now, migrations.RunPython.noop),
    ]
