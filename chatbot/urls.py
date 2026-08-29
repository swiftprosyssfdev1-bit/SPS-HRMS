from django.urls import path
from chatbot import views

app_name = 'chatbot'

urlpatterns = [
    path('ask/', views.chatbot_ask, name='ask'),
    path('resolve/', views.chatbot_resolve, name='resolve'),
    path('analytics/', views.chatbot_analytics, name='analytics'),
]
