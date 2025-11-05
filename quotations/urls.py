# quotations/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('new/', views.CreateQuotationView.as_view(), name='create_quotation'),
    path('<int:pk>/', views.QuotationDetailView.as_view(), name='quotation_detail'),
    path('get-customer/', views.get_customer, name='get_customer'),
    path('create-customer/', views.CustomerCreateView.as_view(), name='create_customer'),
    path('customer-list/', views.CustomerListView.as_view(), name='customer_list'),
    path("quotations-list/", views.QuotationListView.as_view(), name="quotation_list"),
    path("get-products-by-category/", views.get_products_by_category, name="get_products_by_category"),
    path("products/", views.ProductListView.as_view(), name="product_list") ,
    path("products/<int:pk>/edit/", views.EditProductView.as_view(), name="edit_product"),
    path("products/add/", views.CreateProductView.as_view(), name="create_product"),
    path("quotation/<int:quotation_id>/request-price-change/", views.PriceChangeRequestCreateView.as_view(),
         name="request_price_change"),
    path("price-change-requests/", views.PriceChangeRequestListView.as_view(), name="price_change_requests"),
    path("price-change-requests/<int:pk>/approve/", views.PriceChangeRequestApproveView.as_view(),
         name="approve_price_change"),
    path("price-change-requests/<int:pk>/reject/", views.PriceChangeRequestRejectView.as_view(), name="reject_price_change"),
]