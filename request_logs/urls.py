from django.urls import path
from . import views

urlpatterns = [
    path("", views.LogsDashboardView.as_view(), name="logs_dashboard"),
    path("<int:log_id>/", views.LogDetailView.as_view(), name="log_detail"),
    path("sessions/<str:session_id>/", views.SessionTimelineView.as_view(), name="session_timeline"),

    path("history/", views.HistoryView.as_view(), name="log_history"),
    path("history/<int:history_id>/", views.HistoryLogDetailView.as_view(), name="history_log_detail"),
    path("history/sessions/<str:session_id>/", views.SessionTimelineView.as_view(), name="history_session_timeline"),
]