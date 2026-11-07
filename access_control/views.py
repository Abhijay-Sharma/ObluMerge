# access_control/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model

from access_control.services import get_user_effective_permissions, can
from access_control.hierarchy import get_downline_user_ids
from access_control.roles import (
    get_subordinate_salesperson_ids,
    get_accessible_salesperson_ids,
    get_salesperson_profile,
    is_admin,
    is_finance,
    is_hr,
)
from customer_dashboard.models import SalesPerson

User = get_user_model()


def get_allowed_apps_for_user(user, effective_perms):
    """
    Inspects user permissions and role to determine accessible apps.
    Always includes 'all' at the beginning.
    """
    apps = [
        {"id": "all", "label": "All", "icon": "grid"}
    ]

    is_super = user.is_superuser or user.is_staff or is_admin(user)
    finance_access = is_finance(user)
    hr_access = is_hr(user)

    sp = get_salesperson_profile(user)
    sp_role = (getattr(sp, "role", "") or "").lower() if sp else ""
    user_role_str = getattr(user, "role", "").lower()
    is_wildcard = "*" in effective_perms

    # 1. Customers (All sales roles, management, operations, finance, admin)
    has_customer = (
        is_super or finance_access or is_wildcard
        or "customer.view" in effective_perms
        or "customer.view_all" in effective_perms
        or sp is not None
        or any(r in sp_role or r in user_role_str for r in ["sales", "asm", "rsm", "head", "director", "manager", "accounts", "operations"])
    )
    if has_customer:
        apps.append({"id": "customers", "label": "Customers", "icon": "people"})

    # 2. Leads (Sales team, marketing, business dev, admin)
    has_leads = (
        is_super or is_wildcard
        or "lead.view" in effective_perms
        or "customer.view" in effective_perms
        or sp is not None
        or any(r in sp_role or r in user_role_str for r in ["sales", "asm", "rsm", "head", "director", "manager", "business"])
    )
    if has_leads:
        apps.append({"id": "leads", "label": "Leads", "icon": "filter"})

    # 3. Inventory (Warehouse, accounts/operations, business, admin)
    has_inventory = (
        is_super or is_wildcard
        or "inventory.view" in effective_perms
        or any(r in sp_role or r in user_role_str for r in ["warehouse", "inventory", "operations", "accounts", "admin", "business"])
    )
    if has_inventory:
        apps.append({"id": "inventory", "label": "Inventory", "icon": "cube"})

    # 4. Proforma (Saleshead, RSM, ASM, accounts, warehouse, admin)
    has_proforma = (
        is_super or finance_access or is_wildcard
        or any(p in effective_perms for p in ["proforma.view", "proforma.create", "proforma.dispatch", "dispatch.queue"])
        or any(r in sp_role or r in user_role_str for r in ["sales", "asm", "rsm", "head", "accounts", "warehouse", "admin", "business"])
    )
    if has_proforma:
        apps.append({"id": "proforma", "label": "Proforma", "icon": "document"})

    # 5. HRMS (HR, super admin, managers with direct reports)
    emp = getattr(user, "employee_profile", None)
    has_hrms = (
        is_super or hr_access or is_wildcard
        or "hrms.view" in effective_perms
        or "hrms.admin" in effective_perms
        or (emp is not None and getattr(emp, "is_manager", False))
    )
    if has_hrms:
        apps.append({"id": "hrms", "label": "HRMS", "icon": "briefcase"})

    # 6. Incentives (Sales reps, ASMs, RSMs, Salesheads, Finance, Admin)
    has_incentives = (
        is_super or finance_access or is_wildcard
        or "incentive.view" in effective_perms
        or sp is not None
        or any(r in sp_role or r in user_role_str for r in ["sales", "asm", "rsm", "head", "director", "admin"])
    )
    if has_incentives:
        apps.append({"id": "incentives", "label": "Incentives", "icon": "trophy"})

    return apps


def get_hierarchy_scope_for_user(user, effective_perms):
    is_super = user.is_superuser or user.is_staff or is_admin(user)
    finance_access = is_finance(user)

    sp = get_salesperson_profile(user)
    sp_role = (getattr(sp, "role", "") or "").lower() if sp else ""
    user_role_str = getattr(user, "role", "").lower()

    can_view_all = (
        is_super or finance_access
        or "customer.view_all" in effective_perms
        or "sales_head" in sp_role or "saleshead" in sp_role
        or "director" in sp_role or "admin" in user_role_str
        or "*" in effective_perms
    )

    subordinates = []

    # 1. Salesperson downline (RSM -> ASM, Saleshead -> RSM + ASM)
    sub_sp_ids = get_subordinate_salesperson_ids(user)
    if sub_sp_ids:
        for sub_sp in SalesPerson.objects.filter(id__in=sub_sp_ids).select_related("user"):
            sub_role = getattr(sub_sp, "role", None)
            if not sub_role:
                sub_role = "ASM" if sub_sp.team_members.exists() else "Salesperson"
            subordinates.append({
                "id": sub_sp.user_id or sub_sp.id,
                "salesperson_id": sub_sp.id,
                "username": sub_sp.user.username if sub_sp.user else "",
                "name": sub_sp.name,
                "role": sub_role,
            })

    # 2. HRMS downline
    downline_ids = get_downline_user_ids(user)
    existing_uids = {s["id"] for s in subordinates}
    if downline_ids:
        for u in User.objects.filter(id__in=downline_ids).select_related("employee_profile__designation"):
            if u.id not in existing_uids:
                u_emp = getattr(u, "employee_profile", None)
                subordinates.append({
                    "id": u.id,
                    "salesperson_id": None,
                    "username": u.username,
                    "name": u_emp.full_name if u_emp else (u.get_full_name() or u.username),
                    "role": getattr(u_emp.designation, "title", "Employee") if (u_emp and u_emp.designation) else "Employee",
                })

    return {
        "can_view_all_salespersons": can_view_all,
        "subordinates": subordinates,
    }


class MeProfileView(APIView):
    """
    GET /api/auth/me/ or GET /api/access-control/my-permissions/
    Inspects user permissions, role hierarchy level, and returns allowed apps.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        emp = getattr(user, "employee_profile", None)
        sp = get_salesperson_profile(user)
        effective_perms = get_user_effective_permissions(user)

        serialized_scopes = {
            perm: sorted(list(scopes)) for perm, scopes in effective_perms.items()
        }

        # Resolve Display Name
        if emp and emp.full_name:
            user_name = emp.full_name
        elif sp and sp.name:
            user_name = sp.name
        else:
            user_name = user.get_full_name() or user.username

        # Resolve Role
        user_role = "Salesperson"
        roles_list = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    roles_list.append({
                        "key": assignment.role.role_key,
                        "name": assignment.role.display_name,
                        "temporary": bool(assignment.ends_at),
                        "expires_at": assignment.ends_at.strftime("%Y-%m-%d %H:%M") if assignment.ends_at else None,
                    })

        ## for admin and directors role access in mobile app 
        if user.is_superuser or user.is_staff or getattr(user, 'role', '') == 'admin' or user.username.lower() in ['nitin_aggarwal', 'akshay_narayan', 'infected']:
            user_role = "Admin"
        elif is_finance(user):
            user_role = "Finance"
        elif roles_list:
            user_role = roles_list[0]["name"]
        elif emp and emp.designation:
            user_role = emp.designation.title
        elif sp:
            if getattr(sp, "role", None):
                r = sp.role.strip()
                user_role = r.upper() if r.lower() in ["asm", "rsm", "vp"] else r.title()
            elif sp.team_members.exists():
                user_role = "ASM"
            else:
                user_role = "Salesperson"

        # Resolve Reporting Manager
        reporting_to = None
        if emp and emp.reporting_manager:
            reporting_to = emp.reporting_manager.full_name
        elif sp and sp.manager:
            reporting_to = sp.manager.name

        allowed_apps = get_allowed_apps_for_user(user, effective_perms)
        hierarchy_scope = get_hierarchy_scope_for_user(user, effective_perms)

        return Response({
            "user": {
                "id": user.id,
                "username": user.username,
                "name": user_name,
                "role": user_role,
                "reporting_to": reporting_to,
            },
            "allowed_apps": allowed_apps,
            "hierarchy_scope": hierarchy_scope,
            # Direct compatibility fields
            "user_id": user.id,
            "username": user.username,
            "full_name": user_name,
            "email": emp.email if emp else user.email,
            "employee_code": emp.employee_code if emp else None,
            "roles": roles_list,
            "company": {
                "id": emp.company.id,
                "name": emp.company.name
            } if emp and emp.company else None,
            "department": {
                "id": emp.department.id,
                "name": emp.department.name
            } if emp and emp.department else None,
            "designation": {
                "id": emp.designation.id,
                "title": emp.designation.title
            } if emp and emp.designation else None,
            "reporting_manager": {
                "id": emp.reporting_manager.id,
                "name": emp.reporting_manager.full_name
            } if emp and emp.reporting_manager else None,
            "permissions": list(effective_perms.keys()),
            "scopes": serialized_scopes,
        })


class MyTeamListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subordinate_user_ids = get_downline_user_ids(request.user)
        subordinates = User.objects.filter(id__in=subordinate_user_ids).select_related("employee_profile__designation")

        team_data = []
        for u in subordinates:
            emp = getattr(u, "employee_profile", None)
            team_data.append({
                "user_id": u.id,
                "full_name": emp.full_name if emp else (u.get_full_name() or u.username),
                "employee_code": emp.employee_code if emp else None,
                "designation": emp.designation.title if emp and emp.designation else None,
            })

        return Response(team_data)
