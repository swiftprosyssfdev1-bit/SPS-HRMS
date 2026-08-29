from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0010_trainingfeedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='trainingfeedback',
            name='work_sample',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='training_feedback_work_samples/',
                verbose_name='Work Sample',
                help_text="Optional: attach a file showing the employee's actual work during training (e.g. a completed sheet, a screenshot, a report).",
            ),
        ),
    ]
