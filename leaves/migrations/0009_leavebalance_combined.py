from django.db import migrations, models


def reset_to_twelve(apps, schema_editor):
    """Per decision: reset every employee's combined balance to a fresh 12,
    ignoring whatever was left across the old CL/EL/Sick fields."""
    LeaveBalance = apps.get_model('leaves', 'LeaveBalance')
    LeaveBalance.objects.all().update(balance=12)


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0008_leaverequest_current_approver_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='leavebalance',
            name='balance',
            field=models.DecimalField(decimal_places=1, default=12, max_digits=4),
        ),
        migrations.RunPython(reset_to_twelve, migrations.RunPython.noop),
        migrations.RemoveField(model_name='leavebalance', name='cl_balance'),
        migrations.RemoveField(model_name='leavebalance', name='el_balance'),
        migrations.RemoveField(model_name='leavebalance', name='sick_balance'),
    ]
