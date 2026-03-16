from django.urls import path
from . import views



urlpatterns = [
    path("dashboard/", views.MetaDashboardView.as_view(), name="meta_dashboard"),
]
