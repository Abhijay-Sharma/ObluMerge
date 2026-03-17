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