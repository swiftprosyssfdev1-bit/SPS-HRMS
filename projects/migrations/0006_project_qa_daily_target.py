# Generated manually — adds a separate QA Daily Target to Project,
# alongside the existing (Production) daily_target.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0005_project_daily_target'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='qa_daily_target',
            field=models.PositiveIntegerField(
                default=0,
                help_text="Target/Day applied to every member's QA rows for this project.",
                verbose_name='QA Daily Target',
            ),
        ),
    ]
