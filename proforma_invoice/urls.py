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

    path("<int:invoice_id>/request-price-change/",views.ProformaPriceChangeRequestCreateView.as_view(),name='proforma_price_change_request_create'),

    path(
            "price-change-requests/",views.ProformaPriceChangeRequestListView.as_view(),
            name="proforma_price_change_requests"
        ),


    path("proforma/price-request/<int:pk>/approve/",views.ProformaPriceChangeRequestApproveView.as_view(),
         name="proforma_price_change_approve"),

    path("proforma/price-request/<int:pk>/reject/",views.ProformaPriceChangeRequestRejectView.as_view(),
         name="proforma_price_change_reject"),

    path('courier-editor/', views.CourierPricingView.as_view(), name='courier_editor'),
    path('courier-editor/save/', views.SaveCourierSlabsView.as_view(), name='save_courier_slabs'),
    path('new-customer', views.CreateNewProformaCustomerView.as_view(), name='proforma_new_customer'),
    path('dispatch-page/', views.ProformaInvoiceListViewForDispatch.as_view(), name='proforma_invoice_dispatch'),
    path('set-dispatch/<int:pk>/<str:status>/', views.set_dispatch_status, name='set_dispatch'),
    path('stock-requests/', views.StockRequestDashboardView.as_view(), name='stock_request_dashboard'),
    path('stock-requests/<int:pk>/approve/', views.ApproveStockRequestView.as_view(), name='approve_stock_request'),
    path('request-dispatch/<int:pk>/', views.request_dispatch, name='request_dispatch'),
    path('price-request/<int:pk>/remark/', views.ProformaPriceChangeRequestRemarkView.as_view(),
         name='proforma_price_change_remark'),
    path('price-request/<int:pk>/sp-remark/', views.ProformaSPRemarkView.as_view(), name='proforma_sp_remark'),
    path('update-price-remark/', views.update_proforma_price_remark, name='update_proforma_price_remark'),

    path('time-tracker/', views.ProformaTimeTrackerDashboardView.as_view(), name='proforma_time_tracker'),

    path('invoice/remark/manage/<int:pk>/', views.ManageInvoiceRemarkView.as_view(), name='manage_invoice_remark'),
]
