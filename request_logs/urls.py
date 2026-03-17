from django.urls import path
from .views import logs_dashboard
from .views import log_detail
from .views import session_timeline


urlpatterns = [
    path("logs/", logs_dashboard, name="logs_dashboard"),
    path("logs/<int:log_id>/", log_detail, name="log_detail"),
    path("sessions/<str:session_id>/", session_timeline, name="session_timeline"),
]