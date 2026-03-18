from django.urls import path
from .views import app_detail, apps_overview, docs_home, field_detail, model_detail, urls_view

urlpatterns = [

    path("", apps_overview),
    path("app/<str:app_name>/", app_detail),

    path("model/<str:app>/<str:model>/", model_detail),

    path("field/<str:app>/<str:model>/<str:field>/", field_detail),

    path("urls/", urls_view),

]