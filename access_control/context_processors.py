# access_control/context_processors.py
from customer_dashboard.models import SalesPerson
from access_control.services import get_user_effective_permissions

def rbac_context(request):
    if not request.user.is_authenticated:
        return {}

    user = request.user
    emp = getattr(user, "employee_profile", None)

    user_roles = []
    role_display_name = "User"

    # 1. Fetch from HRMS RoleAssignments
    if emp:
        for assignment in emp.role_assignments.filter(is_active=True):
            if assignment.is_currently_valid():
                user_roles.append(assignment.role.role_key)
                if role_display_name == "User":
                    role_display_name = assignment.role.display_name

    # 2. Bridge Superuser -> "admin"
    if user.is_superuser:
        role_display_name = "Superuser"
        if "admin" not in user_roles:
            user_roles.append("admin")

    # 3. Bridge Accounts / Finance -> "accounts_operations"
    is_finance_flag = getattr(user, "is_accountant", False) or user.username.lower() in ["accounts", "accountant", "finance"]
    if is_finance_flag:
        if role_display_name == "User":
            role_display_name = "Accounts & Operations"
        if "accounts_operations" not in user_roles:
            user_roles.append("accounts_operations")

    # 4. Bridge Warehouse user -> "warehouse"
    if user.username.lower() == "warehouse" or "warehouse" in user.username.lower():
        if role_display_name == "User":
            role_display_name = "Warehouse"
        if "warehouse" not in user_roles:
            user_roles.append("warehouse")

    # Fetch effective permissions dictionary
    perms = get_user_effective_permissions(user)
    perm_keys = set(perms.keys()) if perms else set()

    salesperson = SalesPerson.objects.filter(user=user).first()

    return {
        "user_roles": user_roles,
        "role_display_name": role_display_name,
        "permissions": perm_keys,
        "is_executive": bool(
            user.is_superuser
            or is_finance_flag
            or any(r in user_roles for r in ["admin", "sales_head", "business", "accounts_operations"])
        ),
        "salesperson": salesperson,
    }