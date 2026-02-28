from django.urls import path
from . import views


app_name = "customers"


urlpatterns = [
    path("", views.CustomerListView.as_view(), name="data"),   # <-- name="data"
    path("charts/", views.ChartsView.as_view(), name="charts"),
    path("unassigned/", views.UnassignedView.as_view(), name="unassigned"),
    path("map/", views.MapView.as_view(), name="map"),
    path("detailedmap/", views.DetailedMapView.as_view(), name="detailedmap"),
    path("sales-dashboard/", views.SalesPersonCustomerOrdersView.as_view(), name="salesperson_customer_orders"),
    path("salesperson-customers/", views.AdminSalesPersonCustomersView.as_view(), name="salesperson_customers"),
    path("customers/<int:pk>/payment-status/",views.CustomerPaymentStatusView.as_view(),name="customerpaymentstatus"),
    path("customer/<int:pk>/edit/", views.CustomerEditView.as_view(), name="customer_edit"),
    path("sales/claim-own-voucher/",views.ClaimOwnVoucherView.as_view(),name="claim_own_voucher"),
    path("sales/request-voucher-claim/",views.RequestVoucherClaimView.as_view(),name="request_voucher_claim",),
    path("sales/approve-voucher-claims/",views.ApproveVoucherClaimsView.as_view(),name="approve_voucher_claims",),
    path("sales/customer-vouchers/",views.CustomerVouchersOverviewView.as_view(),name="customer_vouchers_overview",),
    path("admin/voucher-claims/", views.AdminVoucherClaimManagementView.as_view(),name="admin_voucher_claim_management", ),
    path("credit-period/<int:pk>/edit_credit/",views.EditCreditPeriodView.as_view(),name="edit-credit-period"),
    path(
        "payment-thread/<int:voucher_status_id>/",
        views.PaymentThreadDetailView.as_view(),
        name="payment_thread_detail",
    ),

]