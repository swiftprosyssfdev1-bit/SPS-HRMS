from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_activitylog'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='role_title',
            field=models.CharField(
                blank=True, default='', max_length=30,
                help_text='Display title (e.g. HR Assistant). Permissions always come from `role`.',
            ),
        ),
    ]
