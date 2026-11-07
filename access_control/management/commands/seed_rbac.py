# access_control/management/commands/seed_rbac.py
from django.core.management.base import BaseCommand
from django.db import transaction
from access_control.models import Role, Permission, RolePermission

ROLE_MATRIX = {
    # =========================================================================
    # 👑 Super Admin (Full Unrestricted System Access)
    # =========================================================================
    "admin": {
        "display_name": "Super Admin",
        "perms": {
            "customer.view_all": "all",
            "customer.view": "all",
            "customer.edit": "all",
            "customer.edit_credit": "all",
            "payment.view": "all",
            "proforma.create": "all",
            "proforma.view": "all",
            "dispatch.queue": "all",            # Access Accounts Dispatch Dashboard & Queue
            "proforma.dispatch": "all",         # Access Dispatch Detail page to execute/approve
            "proforma.price_approval": "all",
            "stock.request": "all",
            "credit.overdue": "all",
            "inventory.view": "all",
            "product_price.view": "all",
            "product_tier.manage": "all",
            "analytics.view": "all",
            "customer.unassigned": "all",
        }
    },

    # =========================================================================
    # 💼 Sales Head (Sales Leadership & Approval Authority)
    # =========================================================================
    "sales_head": {
        "display_name": "Sales Head",
        "perms": {
            "customer.view_all": "all",
            "customer.view": "all",
            "customer.edit": "all",
            "customer.edit_credit": "all",
            "payment.view": "all",
            "proforma.create": "all",
            "proforma.view": "all",
            "proforma.price_approval": "all",   # Approves price changes >= MSP
            "credit.overdue": "all",            # Approves overdue credit bypass
            "stock.request": "all",
            "product_price.view": "all",
        }
    },

    # =========================================================================
    # 🏢 Business Manager (Executive Overview)
    # =========================================================================
    "business": {
        "display_name": "Business Manager",
        "perms": {
            "customer.view_all": "all",
            "customer.view": "all",
            "customer.edit": "all",
            "payment.view": "all",
            "proforma.create": "all",
            "proforma.view": "all",
            "stock.request": "all",
            "inventory.view": "all",
            "product_price.view": "all",
        }
    },

    # =========================================================================
    # 📊 Accounts & Operations (Finance, Dispatch Queue & Tier Slabs)
    # =========================================================================
     "accounts_operations": {
        "display_name": "Accounts & Operations",
        "perms": {
            "customer.view_all": "all",
            "customer.view": "all",
            "payment.view": "all",
            "dispatch.queue": "all",
            "proforma.dispatch": "all",
            "stock.request": "all",
            "inventory.view": "all",
            "product_tier.manage": "all",
        }
    },


    # =========================================================================
    # 📦 Warehouse (Execution Only: Dispatch Detail & Photo Uploads)
    # =========================================================================
    "warehouse": {
        "display_name": "Warehouse",
        "perms": {
            "proforma.dispatch": "all",

        }
    },

    # =========================================================================
    # 🗺️ Regional Sales Manager (RSM)
    # =========================================================================
    "rsm": {
        "display_name": "Regional Sales Manager",
        "perms": {
            "customer.view": "team",
            "customer.edit": "team",
            "payment.view": "team",
            "proforma.create": "team",
            "proforma.view": "team",
            "stock.request": "team",
            "product_price.view": "all",
        }
    },

    # =========================================================================
    # 📍 Area Sales Manager (ASM)
    # =========================================================================
    "asm": {
        "display_name": "Area Sales Manager",
        "perms": {
            "customer.view": "team",
            "customer.edit": "team",
            "payment.view": "team",
            "proforma.create": "team",
            "proforma.view": "team",
            "stock.request": "team",
            "product_price.view": "all",
        }
    },

    # =========================================================================
    # 👤 Salesperson
    # =========================================================================
    "salesperson": {
        "display_name": "Salesperson",
        "perms": {
            "customer.view": "self",
            "payment.view": "self",
            "proforma.create": "self",
            "proforma.view": "self",
            "stock.request": "self",
            "product_price.view": "all",
        }
    },

    # =========================================================================
    # 👥 Human Resources
    # =========================================================================
    "hr": {
        "display_name": "Human Resources",
        "perms": {
            "customer.view": "all",
        }
    },
}


class Command(BaseCommand):
    help = "Seeds and synchronizes standard RBAC roles and permissions"

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("--- Synchronizing RBAC Matrix ---"))

        for r_key, data in ROLE_MATRIX.items():
            role, _ = Role.objects.update_or_create(
                role_key=r_key,
                defaults={"display_name": data["display_name"]}
            )

            current_perm_keys = set(data["perms"].keys())

            deleted_count, _ = RolePermission.objects.filter(
                role=role
            ).exclude(
                permission__permission_key__in=current_perm_keys
            ).delete()

            if deleted_count > 0:
                self.stdout.write(f"  • Removed {deleted_count} stale permission(s) from '{role.display_name}'")

            for p_key, scope in data["perms"].items():
                module = p_key.split(".")[0]
                label = p_key.replace(".", " ").replace("_", " ").title()

                perm, _ = Permission.objects.update_or_create(
                    permission_key=p_key,
                    defaults={
                        "display_name": label,
                        "module_name": module
                    }
                )

                RolePermission.objects.update_or_create(
                    role=role,
                    permission=perm,
                    defaults={"default_scope": scope}
                )

            self.stdout.write(f"  ✓ Role '{role.display_name}' ({r_key}) synchronized.")

        self.stdout.write(self.style.SUCCESS("\nSuccessfully synchronized all RBAC roles and permissions!"))