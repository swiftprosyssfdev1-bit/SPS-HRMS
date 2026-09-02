# Generated manually — adds a required Work Type (Production / QA) to
# DailyProduction. Existing rows default to 'PRODUCTION' since that's
# what every current row represents.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production_tracker', '0003_remove_dailyproduction_unique_employee_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyproduction',
            name='work_type',
            field=models.CharField(
                choices=[('PRODUCTION', 'Production'), ('QA', 'QA')],
                default='PRODUCTION',
                max_length=10,
            ),
        ),
    ]
