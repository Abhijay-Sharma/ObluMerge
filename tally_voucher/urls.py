# vouchers/urls.py
from django.urls import path
from .views import VoucherDetailView, customer_item_purchases, VoucherListView, party_autocomplete_for_item, get_voucher_products, SaveEmiFromVoucherListView, EmiUpdationListView, EmiUpdateActionView, StockItemListView, StockItemLedgerView

urlpatterns = [
    path("voucher/<int:pk>/", VoucherDetailView.as_view(), name="voucher_detail"),
    path("customer/<int:customer_id>/items/", customer_item_purchases, name="customer_item_purchases"),
    path("list/", VoucherListView.as_view(), name="voucher_list"),
    path('party-autocomplete-item/', party_autocomplete_for_item, name='party_autocomplete'),
    path("get-products/<int:voucher_id>/", get_voucher_products, name="get_voucher_products"),
    path("save-emi-list/", SaveEmiFromVoucherListView.as_view(), name="save_emi_list"),
    path("emi-updation/", EmiUpdationListView.as_view(), name="emi_updation"),
    path("emi-update-action/", EmiUpdateActionView.as_view(), name="emi_update_action"),
    path("items/", StockItemListView.as_view(), name="stock_item_list"),
    path("items/<int:item_id>/ledger/", StockItemLedgerView.as_view(), name="stock_item_ledger"),
]
