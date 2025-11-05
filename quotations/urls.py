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
    path("quotations-list/", views.QuotationListView.as_view(), name="quotation_list"),
    path("get-products-by-category/", views.get_products_by_category, name="get_products_by_category"),
    path("products/", views.ProductListView.as_view(), name="product_list") ,
    path("products/<int:pk>/edit/", views.EditProductView.as_view(), name="edit_product"),
    path("products/add/", views.CreateProductView.as_view(), name="create_product"),

]