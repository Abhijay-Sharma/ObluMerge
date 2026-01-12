from django.contrib import admin
from .models import *
# Register your models here.


admin.site.register(ProformaInvoice)
admin.site.register(ProductPriceTier)
admin.site.register(ProductPrice)
admin.site.register(ProformaInvoiceItem)
admin.site.register(CourierCharge)
admin.site.register(CourierChargeTier)
