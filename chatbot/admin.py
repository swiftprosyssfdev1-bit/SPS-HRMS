from django import forms
from django.contrib import admin
from chatbot.models import FAQ, ChatLog
from core.models import User

class FAQAdminForm(forms.ModelForm):
    ROLE_CHOICES = [('ALL', 'All Roles')] + list(User.ROLE_CHOICES)
    
    target_roles = forms.MultipleChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        initial=['ALL'],
        help_text="Select which roles can see this answer. Select 'All Roles' for everyone."
    )

    class Meta:
        model = FAQ
        fields = '__all__'

    def clean_target_roles(self):
        roles = self.cleaned_data.get('target_roles')
        if not roles:
            return 'ALL'
        if 'ALL' in roles:
            return 'ALL'
        return ','.join(roles)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.target_roles:
            self.initial['target_roles'] = self.instance.target_roles.split(',')


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    form = FAQAdminForm
    list_display = ('question', 'target_roles', 'is_active', 'priority', 'updated_at')
    list_filter = ('is_active', 'target_roles')
    search_fields = ('question', 'keywords', 'answer')
    list_editable = ('is_active', 'priority')


@admin.register(ChatLog)
class ChatLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'question', 'answer_source', 'matched_faq', 'created_at')
    list_filter = ('answer_source', 'created_at')
    search_fields = ('question', 'user__username', 'user__first_name', 'user__last_name')
    readonly_fields = ('user', 'question', 'answer_source', 'matched_faq', 'created_at')

    def has_add_permission(self, request):
        return False
