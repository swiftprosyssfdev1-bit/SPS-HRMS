import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_motivationalpost'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ActivityLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor_name', models.CharField(blank=True, max_length=150)),
                ('actor_role', models.CharField(blank=True, max_length=50)),
                ('action', models.CharField(choices=[('ADDED', 'Employee added'), ('UPDATED', 'Profile updated'), ('ROLE', 'Role changed'), ('STATUS', 'Status changed'), ('SALARY', 'Salary updated'), ('CREDENTIALS', 'Credentials reset'), ('DOCUMENT', 'Document deleted'), ('ONBOARDED', 'Onboarding completed'), ('RESIGNATION', 'Resignation reviewed'), ('DELETED', 'Employee deleted')], db_index=True, max_length=20)),
                ('target_name', models.CharField(blank=True, max_length=150)),
                ('summary', models.CharField(max_length=255)),
                ('changes', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.branch')),
                ('target', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
