from django.urls import path
from .views import logs_dashboard
from .views import log_detail
from .views import session_timeline
from . import views


urlpatterns = [
    path("", views.LogsDashboardView.as_view(), name="logs_dashboard"),
    path("<int:log_id>/", views.LogDetailView.as_view(), name="log_detail"),
    path("sessions/<str:session_id>/", views.SessionTimelineView.as_view(), name="session_timeline"),
]