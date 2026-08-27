# Generated manually — allows multiple DailyProduction rows per
# employee per date (QA/Production employees work several projects
# in one day, each as its own row).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('production_tracker', '0002_dailyproduction_is_submitted'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='dailyproduction',
            unique_together=set(),
        ),
    ]
