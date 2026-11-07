# access_control/roles.py
from django.db.models import Q


# =====================================================================
# 1. ROLE & IDENTITY DETECTION
# =====================================================================

def is_admin(user):
    return user.is_superuser or user.groups.filter(name="Admin").exists()


def is_finance(user):
    """Accountant / Finance team access"""
    return getattr(user, 'is_accountant', False) or is_admin(user)


def is_hr(user):
    """HR team access for HRMS administration"""
    return is_admin(user) or user.groups.filter(name="HR").exists()


def get_salesperson_profile(user):
    """Returns the SalesPerson object linked to this user, if any."""
    from customer_dashboard.models import SalesPerson
    return SalesPerson.objects.filter(user=user).first()


# =====================================================================
# 2. HIERARCHY SCOPING (Sales: Saleshead -> RSM -> ASM)
# =====================================================================

def get_subordinate_salesperson_ids(user):
    """
    Finds all SalesPerson IDs under this user recursively.
    - If user is ASM: returns []
    - If user is RSM: returns their direct ASMs
    - If user is Saleshead: returns their RSMs AND the ASMs under those RSMs
    """
    from customer_dashboard.models import SalesPerson
    sp = get_salesperson_profile(user)
    if not sp:
        return []

    # Direct reports (e.g. ASMs under an RSM)
    direct_subordinates = list(SalesPerson.objects.filter(manager=sp).values_list('id', flat=True))

    # Indirect reports (e.g. ASMs under the RSMs that report to this Saleshead)
    indirect_subordinates = list(
        SalesPerson.objects.filter(manager_id__in=direct_subordinates).values_list('id', flat=True)
    )

    return list(set(direct_subordinates + indirect_subordinates))


def get_accessible_salesperson_ids(user):
    """Returns own SalesPerson ID + all subordinate SalesPerson IDs."""
    sp = get_salesperson_profile(user)
    if not sp:
        return []
    return [sp.id] + get_subordinate_salesperson_ids(user)


# =====================================================================
# 3. DATA SCOPE QUERYSETS (The Central Engine)
# =====================================================================

def get_accessible_customers(user):
    """
    Single source of truth for Customer visibility:
    - Admin & Finance -> See ALL customers
    - Saleshead -> Sees all team customers (RSM + ASM)
    - RSM -> Sees self + team ASMs customers
    - ASM -> Sees ONLY own customers
    """
    from customer_dashboard.models import Customer

    if is_finance(user):
        return Customer.objects.all()

    sp_ids = get_accessible_salesperson_ids(user)
    if not sp_ids:
        return Customer.objects.none()

    return Customer.objects.filter(salesperson_id__in=sp_ids)


def get_accessible_vouchers(user):
    """
    Single source of truth for CustomerVoucherStatus visibility:
    Used in Payment Followups, Reports, and Mobile API.
    """
    from customer_dashboard.models import CustomerVoucherStatus

    if is_finance(user):
        return CustomerVoucherStatus.objects.all()

    sp_ids = get_accessible_salesperson_ids(user)
    if not sp_ids:
        return CustomerVoucherStatus.objects.none()

    return CustomerVoucherStatus.objects.filter(
        Q(customer__salesperson_id__in=sp_ids) | Q(sold_by_id__in=sp_ids)
    )