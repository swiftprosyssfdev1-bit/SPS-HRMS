from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0006_alter_leaverequest_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='leaverequest',
            name='request_type',
            field=models.CharField(choices=[('PERMISSION', 'Permission'), ('LEAVE', 'Leave'), ('WFH', 'Work From Home')], default='PERMISSION', max_length=10),
        ),
    ]
