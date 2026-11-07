# access_control/admin.py
from django.contrib import admin
from .models import Role, Permission, RolePermission, RoleAssignment


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 1


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("role_key", "display_name", "description")
    search_fields = ("role_key", "display_name")
    inlines = [RolePermissionInline]


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("permission_key", "display_name", "module_name")
    list_filter = ("module_name",)
    search_fields = ("permission_key", "display_name")


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = (
        "employee__employee_code",
        "employee__first_name",
        "employee__last_name",
        "role__display_name"
    )