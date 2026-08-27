from django.db import migrations, models


def backfill_is_qa(apps, schema_editor):
    """One-time backfill: mark every department whose name currently
    contains 'quality' as is_qa=True, so existing QA departments keep
    working exactly as before the switch away from name-matching.
    HR can then untag/tag departments correctly going forward via the
    real is_qa checkbox instead of relying on the department's name."""
    Department = apps.get_model('core', 'Department')
    Department.objects.filter(name__icontains='quality').update(is_qa=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_department_default_project_alter_user_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='department',
            name='is_qa',
            field=models.BooleanField(
                default=False,
                verbose_name='QA Department',
                help_text="Employees in this department get Work Type = QA and are counted for QA targets.",
            ),
        ),
        migrations.RunPython(backfill_is_qa, noop_reverse),
    ]
