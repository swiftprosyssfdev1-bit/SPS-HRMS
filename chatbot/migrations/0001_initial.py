from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0014_department_default_project_alter_user_role'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FAQ',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.CharField(help_text="The question, e.g. 'What is the attendance policy?'", max_length=255)),
                ('keywords', models.CharField(blank=True, help_text='Comma-separated extra words/phrases that should also match this FAQ.', max_length=500)),
                ('answer', models.TextField(help_text='The reply the chatbot should give. Supports plain text/line breaks.')),
                ('is_active', models.BooleanField(default=True)),
                ('priority', models.IntegerField(default=0, help_text='Higher priority FAQs are checked first when a question matches more than one.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'FAQ',
                'verbose_name_plural': 'FAQs',
                'ordering': ['-priority', 'question'],
            },
        ),
        migrations.CreateModel(
            name='ChatLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question', models.CharField(max_length=500)),
                ('answer_source', models.CharField(choices=[('FAQ', 'FAQ match'), ('DATA', 'Live HRMS data'), ('UNKNOWN', 'No match — told to contact HR')], max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('matched_faq', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='chatbot.faq')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chatbot_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
