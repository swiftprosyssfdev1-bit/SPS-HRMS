from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0004_project_completed_at_project_completed_by_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='daily_target',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Target/Day applied to every member currently assigned to this project.',
                verbose_name='Daily Target',
            ),
        ),
    ]
