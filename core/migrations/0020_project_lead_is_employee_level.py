from django.db import migrations


def lead_to_employee(apps, schema_editor):
    """Project Lead was first mapped to the Project Manager permission level.
    It is employee-level (a Project Manager assigns projects to them), so move
    any account already holding that title."""
    User = apps.get_model('core', 'User')
    User.objects.filter(role_title='PROJECT_LEAD', role='PROJECT_MANAGER').update(role='EMPLOYEE')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_user_role_title'),
    ]

    operations = [
        migrations.RunPython(lead_to_employee, migrations.RunPython.noop),
    ]
