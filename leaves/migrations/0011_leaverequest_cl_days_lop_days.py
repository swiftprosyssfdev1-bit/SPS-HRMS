from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0010_leavebalance_last_credited_month'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaverequest',
            name='cl_days',
            field=models.DecimalField(max_digits=4, decimal_places=1, default=0, blank=True),
        ),
        migrations.AddField(
            model_name='leaverequest',
            name='lop_days',
            field=models.DecimalField(max_digits=4, decimal_places=1, default=0, blank=True),
        ),
    ]
