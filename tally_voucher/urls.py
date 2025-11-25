# vouchers/urls.py
from django.urls import path
from .views import voucher_list, VoucherDetailView, customer_item_purchases

urlpatterns = [
    path("all/", voucher_list, name="voucher_list"),
    path("voucher/<int:pk>/", VoucherDetailView.as_view(), name="voucher_detail"),
    path("customer/<int:customer_id>/items/", customer_item_purchases, name="customer_item_purchases"),

]
