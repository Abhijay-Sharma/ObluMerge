# proforma_invoice/urls.py

from django.urls import path
from . import views

urlpatterns = [
    # 🧾 Create a new Proforma Invoice
    path('create/', views.CreateProformaInvoiceView.as_view(), name='create_proforma'),

    # 📄 View a specific Proforma Invoice
    path('<int:pk>/', views.ProformaInvoiceDetailView.as_view(), name='proforma_detail'),

    # 🔍 Fetch Inventory Items by Category (for the product modal)
    path('api/inventory_by_category/', views.get_inventory_by_category, name='get_inventory_by_category'),

    path('',views.home,name='home'),

    path("proformas/", views.ProformaInvoiceListView.as_view(), name="proforma_list"),

    path("products/", views.ProformaProductListView.as_view(), name="proforma_product_list"),
]
