from django.contrib import admin
from .models import SalesPerson, Customer, CustomerCreditProfile, CustomerFollowUp, CustomerRemark, PaymentDiscussionThread, CustomerVoucherStatus




@admin.register(SalesPerson)
class SalesPersonAdmin(admin.ModelAdmin):
    list_display = ("name", "user")
    search_fields = ("name",)




@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "state", "district", "salesperson")
    list_filter = ("state", "salesperson")
    search_fields = ("name", "phone", "email", "address")


@admin.register(CustomerCreditProfile)
class CustomerCreditProfileAdmin(admin.ModelAdmin):
    list_display = ("customer","outstanding_balance","credit_period_days")


@admin.register(CustomerFollowUp)
class CustomerFollowUpAdmin(admin.ModelAdmin):
    list_display = ("salesperson","customer","followup_date","is_completed")

@admin.register(CustomerRemark)
class CustomerRemarkAdmin(admin.ModelAdmin):
    list_display = ("salesperson","customer","created_at")

@admin.register(PaymentDiscussionThread)
class PaymentDiscussionThreadAdmin(admin.ModelAdmin):
    list_display = ("voucher_status","ticket_status","raised_by","raised_at")


@admin.register(CustomerVoucherStatus)
class CustomerVoucherStatusAdmin(admin.ModelAdmin):
    list_display = ("customer","voucher","voucher_type","voucher_category","voucher_date","voucher_amount","unpaid_amount")
    search_fields = ("customer","voucher")