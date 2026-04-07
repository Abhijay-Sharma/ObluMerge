from django.contrib import admin
from .models import ProductIncentive, ProductIncentiveTier, IncentivePaymentStatus, IncentiveCategory

#Register your models here.

admin.site.register(IncentiveCategory)
admin.site.register(IncentivePaymentStatus)
admin.site.register(ProductIncentive)
admin.site.register(ProductIncentiveTier)