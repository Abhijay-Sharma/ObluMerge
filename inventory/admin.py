from django.contrib import admin
from .models import InventoryItem, Category, MonthlyStockData, DailyStockData, User

# Register your models here.
admin.site.register(User)

admin.site.register(InventoryItem)

admin.site.register(Category)

admin.site.register(MonthlyStockData)

admin.site.register(DailyStockData)