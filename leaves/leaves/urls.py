from django.urls import path
from leaves import views

app_name = 'leaves'

urlpatterns = [
    path('me/', views.my_leaves, name='my_leaves'),
    path('approvals/', views.leave_approvals, name='approvals'),
    path('<int:leave_id>/<str:decision>/', views.review_leave, name='review_leave'),
    path('notifications/', views.notifications, name='notifications'),
    path('api/check_balance/', views.check_leave_balance, name='check_balance'),
    path('appreciation/', views.appreciation_report, name='appreciation_report'),
]

