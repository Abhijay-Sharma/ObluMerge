from django.contrib import admin
from django.urls import path
from .views import Index  , SignUpView , LogoutView , Dashboard, Dashboard2 , AddItem , EditItem, DeleteItem , stock_chart_view, predict_min_stock_view, ShowProductData, stock_chart_view_2, predict_min_stock_2, ShowProductStockHistory, stock_chart_view_3 , predict_min_stock_from_daily , CategoryDashboard , CategoryListView, search_items, InventoryReportView, MonthlyStockChartView, PredictMinStockView, LowStockReportView #this Index is name of the class we created in views
from django.contrib.auth import views as auth_views


urlpatterns = [
    path('', Index.as_view(), name="index"),
    path('dashboard/', Dashboard.as_view(), name="dashboard"),
    path('dashboard-test/', Dashboard2.as_view(), name="dashboard-test"),
    path('add-item/',AddItem.as_view(), name='add-item'),
    path('edit-item/<int:pk>', EditItem.as_view(), name='edit-item'),
    path('delete-item/<int:pk>',DeleteItem.as_view(), name='delete-item'),
    path('charts/stock/', stock_chart_view, name='stock_chart'),
    path('predict/min-stock/', predict_min_stock_view, name='predict_min_stock'),
    path('showdata/<int:pk>/', ShowProductData.as_view(), name='showdata'),
    path('charts/<int:pk>/', stock_chart_view_3, name='stock_chart_2'),
    path('predict/<int:pk>/', PredictMinStockView.as_view() , name='predict_min_stock'),
    path('history/<int:pk>/',ShowProductStockHistory.as_view(), name='history'),
    path('dashboard/<int:category>/',CategoryDashboard.as_view(), name='category_dashboard'),
    path('categories/',CategoryListView.as_view(), name='categories'),
    path('search/', search_items, name='search_items'),
    path('report/', InventoryReportView.as_view(), name='inventory_report'),
    path("stock/monthly/<int:pk>/", MonthlyStockChartView.as_view(), name="monthly-stock-chart"),
    path('low-stock-outwards-trend/', LowStockReportView.as_view(), name='low_stock_report'),
]