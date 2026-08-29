from django.conf import settings
from django.db import models


class FAQ(models.Model):
    """A single question/answer pair the chatbot can answer instantly.

    `keywords` is a comma-separated list of extra words/phrases (besides
    the question text itself) that should also match this FAQ, e.g. for
    question "What are the working hours?" you might add
    keywords="office timing, shift timing, work hours".
    """
    question = models.CharField(max_length=255, help_text="The question, e.g. 'What is the attendance policy?'")
    keywords = models.CharField(
        max_length=500, blank=True,
        help_text="Comma-separated extra words/phrases that should also match this FAQ."
    )
    answer = models.TextField(help_text="The reply the chatbot should give. Supports plain text/line breaks.")
    target_roles = models.CharField(
        max_length=255, 
        default='ALL',
        help_text="Comma-separated roles or 'ALL'."
    )
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(
        default=0,
        help_text="Higher priority FAQs are checked first when a question matches more than one."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', 'question']
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question

    def all_match_terms(self):
        """Returns a lowercase list of words/phrases to match against,
        combining the question text and the keywords field."""
        terms = [self.question.lower()]
        if self.keywords:
            terms += [k.strip().lower() for k in self.keywords.split(',') if k.strip()]
        return terms


class ChatLog(models.Model):
    """Lightweight log of what employees asked and how it was answered,
    so HR can see unanswered questions and add new FAQs over time."""
    SOURCE_CHOICES = [
        ('FAQ', 'FAQ match'),
        ('DATA', 'Live HRMS data'),
        ('RAG', 'RAG (policy docs / FAQ knowledge base)'),
        ('SQL', 'Live database query'),
        ('UNKNOWN', 'No match — escalated to HR'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chatbot_logs')
    question = models.CharField(max_length=500)
    answer_source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    matched_faq = models.ForeignKey(FAQ, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} asked: {self.question[:50]}"
