from django.contrib import admin

# Register your models here.
# quotations/admin.py
from django.contrib import admin
from .models import ProductCategory, Product, Quotation, QuotationItem, Customer, ProductPriceTier, PriceChangeRequest

admin.site.register(ProductCategory)
admin.site.register(Product)
admin.site.register(Quotation)
admin.site.register(QuotationItem)
admin.site.register(Customer)
admin.site.register(ProductPriceTier)
admin.site.register(PriceChangeRequest)