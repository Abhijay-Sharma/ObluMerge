# access_control/hierarchy.py
from django.core.cache import cache

def get_descendant_employee_ids(employee_id, max_depth=10):
    from hrms.models import Employee

    subordinates = set()
    current_level = [employee_id]
    depth = 0

    while current_level and depth < max_depth:
        next_level = list(
            Employee.objects.filter(
                reporting_manager_id__in=current_level,
                status=Employee.Status.ACTIVE
            ).values_list("id", flat=True)
        )
        if not next_level:
            break
        subordinates.update(next_level)
        current_level = next_level
        depth += 1

    return subordinates


def get_downline_user_ids(user):
    """Subordinates ONLY (does not include the user). Used for team lists/dropdowns."""
    if not user.is_authenticated:
        return []

    emp = getattr(user, "employee_profile", None)
    if not emp:
        return []

    cache_key = f"rbac_downline_only_{user.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from hrms.models import Employee
    descendant_ids = get_descendant_employee_ids(emp.id)

    downline_user_ids = list(
        Employee.objects.filter(id__in=descendant_ids)
        .exclude(user__isnull=True)
        .values_list("user_id", flat=True)
    )

    cache.set(cache_key, downline_user_ids, timeout=300)
    return downline_user_ids


def get_accessible_user_ids(user):
    """User PLUS all their subordinates. Used for scoping queries."""
    if not user.is_authenticated:
        return []

    ids = get_downline_user_ids(user)
    ids.append(user.id)
    return list(set(ids))