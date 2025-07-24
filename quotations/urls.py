# quotations/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('new/', views.create_quotation, name='create_quotation'),
    path('<int:pk>/', views.quotation_detail, name='quotation_detail'),
    path('<int:pk>/pdf/', views.quotation_pdf, name='quotation_pdf'),
]