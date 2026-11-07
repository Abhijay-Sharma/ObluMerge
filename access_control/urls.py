# access_control/urls.py
from django.urls import path
from .views import MeProfileView, MyTeamListView

urlpatterns = [
    # 1. Essential profile & permissions endpoint for Mobile login and drawer
    path('me/', MeProfileView.as_view(), name='auth-me'),
    path('my-permissions/', MeProfileView.as_view(), name='access-control-my-permissions'),

    # 2. Hierarchy endpoint: returns the direct and indirect team reporting to the user
    path('my-team/', MyTeamListView.as_view(), name='my-team'),
]
