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
    path(
        "customer/<int:customer_id>/payment-threads/",
        views.CustomerPaymentThreadsView.as_view(),
        name="customer_payment_threads",
    ),
    path(
        "payment-followups/",
        views.PaymentFollowUpDashboardView.as_view(),
        name="payment_followup_dashboard",
    ),
    path(
        "payment-followups/action/",
        views.payment_followup_action,
        name="payment_followup_action",
    ),
    path("sales-report/",views.SalesReportView.as_view(),name="sales_report",),
    path("monthly-sales-report/", views.MonthlySalesReportView.as_view(),name="monthly_sales_report"),
    path("all-months-sales-report/", views.AllMonthsSalesReportView.as_view(), name="all_months_sales_report"),

    path("sales-by-products/", views.SalesByProductsView.as_view(), name="product_sales_report"),

    # Page created by Swasti
    path("salesperson/customer-summary/", views.SalesPersonCustomerOwnershipView.as_view(),name="salesperson_customer_summary"),

    #Page created by Swasti
    path("salesperson-performance-report/",views.AdminSalespersonConversionReportView.as_view(),name="customer_performance_report"),

    path("remark-interaction-gap/", views.RemarkInteractionGapView.as_view(), name="remark_interaction_gap"),

    path("payment-summary", views.SalespersonPaymentSummaryView.as_view(), name="payment_collection_summary"),

    path("performance-collection/", views.SalespersonPerformanceCollectionView.as_view(), name="performance_collection"),

    path("geo-sales-report/", views.GeoSalesReportView.as_view(), name="geo_sales_report"),

    path("sales-perfomance-review/", views.SalesPerformanceReviewView.as_view(), name="performance_review"),
]