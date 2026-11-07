# access_control/management/commands/seed_rbac.py
from django.core.management.base import BaseCommand
from django.db import transaction
from access_control.models import Role, Permission, RolePermission

# Baseline self-service permissions granted to every employee in the company
BASE_EMPLOYEE_PERMS = {
    "employee.view_self": "self",
    "employee.doc_upload": "self",
    "attendance.punch": "self",
    "attendance.view": "self",
    "attendance.regularize_apply": "self",
    "leave.apply": "self",
    "leave.view": "self",
    "payroll.view_payslip": "self",
    "hiring.my_interviews": "self",
    "policy.view": "all",
    "notice.view": "all",
    "asset.view_self": "self",
}

# Manager permissions (supervision over direct & indirect reports)
MANAGER_TEAM_PERMS = {
    "manager.dashboard": "team",
    "employee.view": "team",
    "attendance.view": "team",
    "attendance.regularize_review": "team",
    "leave.approve": "team",
    "leave.view": "team",
    "hiring.interview_feedback": "team",
}

# HR Administrator permissions (organization-wide)
HR_ADMIN_PERMS = {
    "org.manage": "all",
    "employee.manage": "all",
    "employee.view": "all",
    "employee.enroll_face": "all",
    "attendance.manage": "all",
    "attendance.view": "all",
    "attendance.kiosk": "all",
    "attendance.live_track": "all",
    "leave.manage": "all",
    "leave.approve": "all",
    "leave.view": "all",
    "hiring.manage": "all",
    "hiring.view": "all",
    "payroll.view": "all",
    "payroll.run": "all",
    "policy.manage": "all",
    "notice.manage": "all",
    "asset.manage": "all",
}

# Payroll & Accounts Operations permissions
PAYROLL_ACCOUNTS_PERMS = {
    "payroll.manage": "all",
    "payroll.view": "all",
    "payroll.run": "all",
    "payroll.payout": "all",
    "payroll.salary_structure": "all",
    "payroll.loans": "all",
    "employee.bank_verify": "all",
}

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
            # HRMS Global
            **HR_ADMIN_PERMS,
            **PAYROLL_ACCOUNTS_PERMS,
            **MANAGER_TEAM_PERMS,
            **BASE_EMPLOYEE_PERMS,
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
            **MANAGER_TEAM_PERMS,
            **BASE_EMPLOYEE_PERMS,
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
            **MANAGER_TEAM_PERMS,
            **BASE_EMPLOYEE_PERMS,
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
            **PAYROLL_ACCOUNTS_PERMS,
            **BASE_EMPLOYEE_PERMS,
        }
    },


    # =========================================================================
    # 📦 Warehouse (Execution Only: Dispatch Detail & Photo Uploads)
    # =========================================================================
    "warehouse": {
        "display_name": "Warehouse",
        "perms": {
            "proforma.dispatch": "all",
            **BASE_EMPLOYEE_PERMS,

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
            **MANAGER_TEAM_PERMS,
            **BASE_EMPLOYEE_PERMS,
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
            **MANAGER_TEAM_PERMS,
            **BASE_EMPLOYEE_PERMS,
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
            **BASE_EMPLOYEE_PERMS,
        }
    },

    # =========================================================================
    # 👥 Human Resources
    # =========================================================================
    "hr": {
        "display_name": "HR Manager",
        "perms": {
            HR_ADMIN_PERMS,
            BASE_EMPLOYEE_PERMS,
        }
    },
    "manager": {
        "display_name": "Reporting Manager",
        "perms": {
            MANAGER_TEAM_PERMS,
            BASE_EMPLOYEE_PERMS,
        }
    },
    "employee": {
        "display_name": "Employee",
        "perms": {
            **BASE_EMPLOYEE_PERMS,
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