# quotations/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('new/', views.CreateQuotationView.as_view(), name='create_quotation'),
    path('<int:pk>/', views.quotation_detail, name='quotation_detail'),
    path('get-customer/', views.get_customer, name='get_customer'),
    path('create-customer/', views.CustomerCreateView.as_view(), name='create_customer'),
    path('customer-list/', views.CustomerListView.as_view(), name='customer_list'),
    path("quotations-list/", views.QuotationListView.as_view(), name="quotation_list")
]