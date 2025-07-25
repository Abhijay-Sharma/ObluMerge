from django.contrib import admin
from django.urls import path
from .views import Index  , SignUpView , LogoutView , Dashboard , AddItem , EditItem, DeleteItem , stock_chart_view, predict_min_stock_view, ShowProductData, stock_chart_view_2, predict_min_stock_2, ShowProductStockHistory, stock_chart_view_3 , predict_min_stock_from_daily #this Index is name of the class we created in views
from django.contrib.auth import views as auth_views


urlpatterns = [
    path('', Index.as_view(), name="index"),
    path('dashboard/', Dashboard.as_view(), name="dashboard"),
    path('add-item/',AddItem.as_view(), name='add-item'),
    path('edit-item/<int:pk>', EditItem.as_view(), name='edit-item'),
    path('delete-item/<int:pk>',DeleteItem.as_view(), name='delete-item'),
    path('charts/stock/', stock_chart_view, name='stock_chart'),
    path('predict/min-stock/', predict_min_stock_view, name='predict_min_stock'),
    path('showdata/<int:pk>/', ShowProductData.as_view(), name='showdata'),
    path('charts/<int:pk>/', stock_chart_view_3, name='stock_chart_2'),
    path('predict/<int:pk>/', predict_min_stock_from_daily , name='predict_min_stock'),
    path('history/<int:pk>/',ShowProductStockHistory.as_view(), name='history'),
]