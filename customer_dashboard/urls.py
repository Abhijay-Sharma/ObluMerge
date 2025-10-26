from django.urls import path
from . import views


app_name = "customers"


urlpatterns = [
    path("", views.CustomerListView.as_view(), name="data"),   # <-- name="data"
    path("charts/", views.ChartsView.as_view(), name="charts"),
    path("unassigned/", views.UnassignedView.as_view(), name="unassigned"),
    path("map/", views.MapView.as_view(), name="map"),
    path("detailedmap/", views.MapView.as_view(), name="detailedmap"),
]