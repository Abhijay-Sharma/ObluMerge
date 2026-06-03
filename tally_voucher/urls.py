# vouchers/urls.py
from django.urls import path
from .views import VoucherDetailView, customer_item_purchases, VoucherListView

urlpatterns = [
    path("voucher/<int:pk>/", VoucherDetailView.as_view(), name="voucher_detail"),
    path("customer/<int:customer_id>/items/", customer_item_purchases, name="customer_item_purchases"),
    path("list/", VoucherListView.as_view(), name="voucher_list"),

]
