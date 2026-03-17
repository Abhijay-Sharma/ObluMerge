from django.contrib import admin
from .models import *
# Register your models here.

# admin.site.register(Voucher)
# admin.site.register(VoucherRow)
# admin.site.register(VoucherStockItem)


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('party_name', 'date', 'voucher_type', 'voucher_number','voucher_category')
    search_fields = ['party_name','voucher_number']

@admin.register(VoucherRow)
class VoucherRowAdmin(admin.ModelAdmin):
    list_display = ('voucher','ledger','amount')

@admin.register(VoucherStockItem)
class VoucherStockItemAdmin(admin.ModelAdmin):
    list_display = ('voucher','item','quantity','date_created')
    search_fields = ['date_created']

