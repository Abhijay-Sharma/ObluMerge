"""
access_control/services.py

Central Authorization Engine.
Provides capability verification, object-level ownership checks,
and dynamic database queryset scoping based on RBAC roles and scopes.
"""

from django.db.models import Q
from django.utils import timezone
from django.contrib.auth import get_user_model
from access_control.models import RolePermission, RoleAssignment
from access_control.hierarchy import get_accessible_user_ids
from customer_dashboard.models import SalesPerson

User = get_user_model()


# =============================================================================
# 1. EFFECTIVE PERMISSION RESOLVER (WITH REQUEST-LEVEL CACHING)
# =============================================================================

def get_user_effective_permissions(user):
    """
    Returns a dictionary of effective permissions and their granted scopes:
        {"permission.key": {"self", "team", ...}}

    Performance Optimization:
    Caches the calculated dictionary on the user object in memory
    to avoid redundant database queries during a single HTTP request cycle.
    """
    if not user or not user.is_authenticated:
        return {}

    # Check request-level memory cache
    if hasattr(user, "_effective_perms_cache"):
        return user._effective_perms_cache

    # Superuser has unrestricted master access
    if user.is_superuser:
        perms = {"*": {"all"}}
        user._effective_perms_cache = perms
        return perms

    emp = getattr(user, "employee_profile", None)
    if not emp:
        user._effective_perms_cache = {}
        return {}

    now = timezone.now()
    active_assignments = RoleAssignment.objects.filter(
        employee=emp,
        is_active=True
    ).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now)
    ).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gte=now)
    )

    role_ids = list(active_assignments.values_list("role_id", flat=True))
    if not role_ids:
        user._effective_perms_cache = {}
        return {}

    # Fetch all permissions granted to active roles
    role_perms = RolePermission.objects.filter(
        role_id__in=role_ids
    ).select_related("permission")

    perm_map = {}
    for rp in role_perms:
        key = rp.permission.permission_key
        perm_map.setdefault(key, set()).add(rp.default_scope)

    # Cache on user object for subsequent calls in the same request
    user._effective_perms_cache = perm_map
    return perm_map


# =============================================================================
# 2. OBJECT OWNERSHIP RESOLVER
# =============================================================================

def get_object_owner_user_id(obj):
    """
    Polymorphic resolver to extract the Django User ID owner from any model.
    Handles ForeignKeys, CharField usernames (Proforma/Quotation), and indirect links.
    """
    if not obj:
        return None

    # 1. Direct User ForeignKey / ID
    if hasattr(obj, "user_id") and obj.user_id:
        return obj.user_id

    # 2. Direct created_by ForeignKey / ID
    if hasattr(obj, "created_by_id") and obj.created_by_id:
        return obj.created_by_id

    # 3. CharField created_by username (Legacy Proforma/Quotation schema)
    if hasattr(obj, "created_by") and isinstance(obj.created_by, str) and obj.created_by:
        owner_id = User.objects.filter(username=obj.created_by).values_list("id", flat=True).first()
        if owner_id:
            return owner_id

    # 4. Direct Salesperson profile link
    if hasattr(obj, "salesperson") and obj.salesperson and obj.salesperson.user_id:
        return obj.salesperson.user_id

    # 5. Customer -> Salesperson link
    if hasattr(obj, "customer") and obj.customer and obj.customer.salesperson:
        return obj.customer.salesperson.user_id

    return None


# =============================================================================
# 3. CAPABILITY & OBJECT-LEVEL SECURITY (can)
# =============================================================================

def can(user, permission_key, obj=None):
    """
    Evaluates capability and object-level permission for a given user.
    Usage:
        can(user, "proforma.create")          -> General action check
        can(user, "proforma.view", invoice)   -> Object-level ownership check
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    perms = get_user_effective_permissions(user)
    if "*" in perms:
        return True

    granted_scopes = perms.get(permission_key)
    if not granted_scopes:
        return False

    # Action-level check or organization-wide scope
    if obj is None or "all" in granted_scopes:
        return True

    owner_user_id = get_object_owner_user_id(obj)
    if not owner_user_id:
        return False

    # Ownership checks against scopes
    if "self" in granted_scopes and owner_user_id == user.id:
        return True

    if "team" in granted_scopes:
        accessible_ids = get_accessible_user_ids(user)
        if owner_user_id in accessible_ids:
            return True

    return False


# =============================================================================
# 4. DATABASE QUERYSET SCOPING (Row-Level Security)
# =============================================================================

def _is_relation(model, field_name):
    """Helper to determine if a model field is a relational field (ForeignKey, etc.)."""
    try:
        return model._meta.get_field(field_name).is_relation
    except Exception:
        return False


def scope_queryset(user, permission_key, queryset):
    """
    Injects SQL WHERE clauses to restrict querysets to the user's authorized scope.
    Translates 'self', 'team', and 'all' scopes into native Django Q objects.
    """
    if not user or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser:
        return queryset

    perms = get_user_effective_permissions(user)
    if "*" in perms:
        return queryset

    granted_scopes = perms.get(permission_key)
    if not granted_scopes:
        return queryset.none()

    # If granted 'all' scope (e.g. Sales Head, Admin), return entire queryset unfiltered
    if "all" in granted_scopes:
        return queryset

    model = queryset.model
    emp = getattr(user, "employee_profile", None)
    sp_profile = SalesPerson.objects.filter(user=user).first()

    union_filter = Q()

    # -------------------------------------------------------------------------
    # Scope Rule: SELF (Own records only)
    # -------------------------------------------------------------------------
    if "self" in granted_scopes:
        self_q = Q()
        if hasattr(model, "created_by"):
            self_q |= Q(created_by=user) if _is_relation(model, "created_by") else Q(created_by=user.username)
        if hasattr(model, "user"):
            self_q |= Q(user=user)
        if hasattr(model, "salesperson"):
            self_q |= Q(salesperson=sp_profile) if sp_profile else Q(salesperson__user=user)
        if hasattr(model, "customer"):
            self_q |= Q(customer__salesperson=sp_profile) if sp_profile else Q(customer__salesperson__user=user)
        union_filter |= self_q

    # -------------------------------------------------------------------------
    # Scope Rule: TEAM (Own records + Downline reports)
    # -------------------------------------------------------------------------
    if "team" in granted_scopes:
        accessible_user_ids = get_accessible_user_ids(user)
        accessible_usernames = list(
            User.objects.filter(id__in=accessible_user_ids).values_list("username", flat=True)
        )

        team_sp_ids = []
        if sp_profile:
            team_sp_ids = list(SalesPerson.objects.filter(manager=sp_profile).values_list("id", flat=True))
            team_sp_ids.append(sp_profile.id)

        team_q = Q()
        if hasattr(model, "created_by"):
            team_q |= Q(created_by_id__in=accessible_user_ids) if _is_relation(model, "created_by") else Q(created_by__in=accessible_usernames)
        if hasattr(model, "user"):
            team_q |= Q(user_id__in=accessible_user_ids) if _is_relation(model, "user") else Q(user__in=accessible_usernames)
        if hasattr(model, "salesperson"):
            team_q |= Q(salesperson__user_id__in=accessible_user_ids) | Q(salesperson_id__in=team_sp_ids)
        if hasattr(model, "customer"):
            team_q |= Q(customer__salesperson__user_id__in=accessible_user_ids) | Q(customer__salesperson_id__in=team_sp_ids)
        union_filter |= team_q

    # -------------------------------------------------------------------------
    # Scope Rule: DEPARTMENT
    # -------------------------------------------------------------------------
    if "department" in granted_scopes and emp and emp.department_id:
        if hasattr(model, "department"):
            union_filter |= Q(department=emp.department)

    # -------------------------------------------------------------------------
    # Scope Rule: COMPANY
    # -------------------------------------------------------------------------
    if "company" in granted_scopes and emp:
        if hasattr(model, "company"):
            allowed_cos = list(emp.managed_companies.values_list("id", flat=True))
            if emp.company_id:
                allowed_cos.append(emp.company_id)
            union_filter |= Q(company_id__in=allowed_cos)

    if not union_filter:
        return queryset.none()

    return queryset.filter(union_filter).distinct()