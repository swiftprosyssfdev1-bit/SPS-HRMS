from django.db import migrations, models


def seed_default_categories(apps, schema_editor):
    LeaveCategory = apps.get_model('leaves', 'LeaveCategory')
    for order, (code, name) in enumerate(
        [('CL', 'Casual Leave'), ('EL', 'Earned Leave'), ('SICK', 'Sick Leave')], start=1
    ):
        LeaveCategory.objects.get_or_create(code=code, defaults={'name': name, 'sort_order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0011_leaverequest_cl_days_lop_days'),
    ]

    operations = [
        migrations.CreateModel(
            name='LeaveCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(editable=False, max_length=30, unique=True)),
                ('name', models.CharField(max_length=60, unique=True)),
                ('is_active', models.BooleanField(default=True, help_text='Inactive categories no longer appear on the Apply form.')),
                ('sort_order', models.PositiveIntegerField(default=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name_plural': 'Leave categories', 'ordering': ['sort_order', 'name']},
        ),
        migrations.AlterField(
            model_name='leaverequest',
            name='leave_type',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.RunPython(seed_default_categories, migrations.RunPython.noop),
    ]
