from django.contrib import admin
from .models import InventoryItem, Category, MonthlyStockData, DailyStockData, User, PurchaseOrderTrackingItem, PurchaseOrderTracking, PurchaseOrderStage, PurchaseOrderStageLog

# Register your models here.
admin.site.register(User)

admin.site.register(InventoryItem)

admin.site.register(Category)

admin.site.register(MonthlyStockData)

admin.site.register(DailyStockData)

admin.site.register(PurchaseOrderTracking)

admin.site.register(PurchaseOrderTrackingItem)

admin.site.register(PurchaseOrderStage)

admin.site.register(PurchaseOrderStageLog)

