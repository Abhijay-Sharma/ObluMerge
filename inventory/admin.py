from django.contrib import admin
from .models import (
    InventoryItem,
    Category,
    ParentCategory,
    MonthlyStockData,
    DailyStockData,
    User,
    PurchaseOrderTrackingItem,
    PurchaseOrderTracking,
    PurchaseOrderStage,
    PurchaseOrderStageLog,
)

# Register models with clean admin controls
admin.site.register(User)

@admin.register(ParentCategory)
class ParentCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'subcategories_count', 'total_products_count', 'is_active', 'created_at')
    search_fields = ('name',)
    list_filter = ('is_active',)

    def subcategories_count(self, obj):
        return obj.subcategories.count()
    subcategories_count.short_description = "Sub-categories"

    def total_products_count(self, obj):
        return sum(sub.inventoryitem_set.count() for sub in obj.subcategories.all())
    total_products_count.short_description = "Total Products"

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'product_count')
    list_editable = ('parent',)  # Allows 1-click inline parent assignment directly from the table list
    list_filter = ('parent',)
    search_fields = ('name', 'parent__name')
    ordering = ('name',)

    def product_count(self, obj):
        return obj.inventoryitem_set.count()
    product_count.short_description = "Products"

@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'category', 'unit', 'is_new_product')
    list_filter = ('category__parent', 'category', 'is_new_product')
    search_fields = ('name',)

admin.site.register(MonthlyStockData)
admin.site.register(DailyStockData)
admin.site.register(PurchaseOrderTracking)
admin.site.register(PurchaseOrderTrackingItem)
admin.site.register(PurchaseOrderStage)
admin.site.register(PurchaseOrderStageLog)