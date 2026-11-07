# access_control/mixins.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from access_control.services import can, scope_queryset


class RBACViewMixin(LoginRequiredMixin):
    """
    Drop-in mixin for standard Django TemplateView, ListView, View.
    1. Checks if the user has the required capability (raises 403 if not).
    2. Provides self.scope_qs() to filter any queryset to user's scope.
    """
    rbac_permission = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if self.rbac_permission and not can(request.user, self.rbac_permission):
            raise PermissionDenied("You do not have permission to view this page.")

        return super().dispatch(request, *args, **kwargs)

    def scope_qs(self, queryset, perm_key=None):
        """Helper to scope any queryset inside get_queryset() or get_context_data()."""
        key = perm_key or self.rbac_permission
        return scope_queryset(self.request.user, key, queryset)