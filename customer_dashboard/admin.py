from django.contrib import admin
from .models import SalesPerson, Customer




@admin.register(SalesPerson)
class SalesPersonAdmin(admin.ModelAdmin):
    list_display = ("name", "user")
    search_fields = ("name",)




@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "state", "district", "salesperson")
    list_filter = ("state", "salesperson")
    search_fields = ("name", "phone", "email", "address")