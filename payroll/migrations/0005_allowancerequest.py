import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0004_allowancecycle_allowance'),
    ]

    operations = [
        migrations.CreateModel(
            name='AllowanceRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('allowance_type', models.CharField(choices=[
                    ('ATTENDANCE_BONUS', 'Attendance Bonus'),
                    ('TRAVEL', 'Travel Allowance'),
                    ('INTERNET', 'Internet Allowance'),
                    ('INCENTIVE', 'Incentive'),
                    ('NIGHT_SHIFT', 'Night Shift Allowance'),
                ], default='ATTENDANCE_BONUS', max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('year', models.IntegerField()),
                ('month', models.IntegerField()),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('proof', models.FileField(help_text='Upload proof as PDF, JPG or PNG.', upload_to='allowance_proofs/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])])),
                ('status', models.CharField(choices=[
                    ('PENDING_PM', 'Pending Project Manager Approval'),
                    ('PENDING_HR', 'Pending HR Approval'),
                    ('APPROVED', 'Approved'),
                    ('REJECTED', 'Rejected'),
                ], default='PENDING_HR', max_length=12)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('pm_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('pm_remarks', models.CharField(blank=True, max_length=255)),
                ('hr_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('hr_remarks', models.CharField(blank=True, max_length=255)),
                ('applied_allowance', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_requests', to='payroll.allowance')),
                ('reviewed_by_hr', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='allowance_requests_hr_reviewed', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by_pm', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='allowance_requests_pm_reviewed', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allowance_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-submitted_at'],
            },
        ),
    ]
