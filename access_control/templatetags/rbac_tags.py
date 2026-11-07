# access_control/templatetags/rbac_tags.py
from django import template
from access_control.services import can

register = template.Library()

@register.filter(name="can")
def can_filter(user, perm_key):
    """
    Usage in templates:
    {% if request.user|can:"proforma.create" %}
    """
    if not user or not user.is_authenticated:
        return False
    return can(user, perm_key)


@register.simple_tag
def can_object(user, perm_key, obj):
    """
    Usage in templates for object-level checks (e.g., checking a specific invoice):
    {% can_object request.user "proforma.view" invoice as allowed %}
    {% if allowed %} ... {% endif %}
    """
    if not user or not user.is_authenticated:
        return False
    return can(user, perm_key, obj=obj)


@register.filter(name="has_role")
def has_role_filter(user, role_key):
    """
    Usage in templates:
    {% if request.user|has_role:"warehouse" %}
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    emp = getattr(user, "employee_profile", None)
    if not emp:
        return False
    return emp.role_assignments.filter(
        role__role_key=role_key,
        is_active=True
    ).exists()