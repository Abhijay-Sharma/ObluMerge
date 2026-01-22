from django.contrib import admin
from .models import SalesPerson, Customer, CustomerCreditProfile, CustomerFollowUp




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


admin.site.register(CustomerFollowUp)