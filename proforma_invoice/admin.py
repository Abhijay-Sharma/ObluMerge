from django.contrib import admin
from .models import *
# Register your models here.


admin.site.register(ProformaInvoice)
admin.site.register(ProductPriceTier)
admin.site.register(ProductPrice)
admin.site.register(ProformaInvoiceItem)
admin.site.register(CourierCharge)
admin.site.register(CourierChargeTier)
admin.site.register(ProformaPriceChangeRequest)
admin.site.register(ApprovedPriceMemory)
admin.site.register(ProformaStockShortageRequest)
admin.site.register(ProformaRemark)
admin.site.register(QuotationMaker)
admin.site.register(QuotationMakerItem)
admin.site.register(CreditPeriodOverdueByPassRequest)
admin.site.register(ShipmentMethod)
admin.site.register(DispatchRequest)
admin.site.register(DispatchPhoto)
admin.site.register(WarehouseDispatch)
admin.site.register(DispatchRemark)
admin.site.register(DispatchStateHistory)
admin.site.register(DispatchInvoice)