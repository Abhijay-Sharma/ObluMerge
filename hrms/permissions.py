"""
Refined RBAC Permissions Matrix for HRMS:

1. SUPERADMIN (User.is_superuser = True)
2. HR         (User.is_staff / Group 'HR' / Department 'HR')
3. MANAGER    (Employee.is_manager = True or has active subordinates)
4. EMPLOYEE   (Standard self-service profile)
5. FINANCE    (User.is_finance / Group 'Finance' / Department 'Accounts/Finance') -> Payroll & Payslips access
"""
from functools import wraps
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

ROLE_SUPERADMIN = 'superadmin'
ROLE_HR = 'hr'
ROLE_MANAGER = 'manager'
ROLE_EMPLOYEE = 'employee'


def get_employee_profile(user):
    """Returns the Employee record linked to this login, checking both related names."""
    if not user or not user.is_authenticated:
        return None
    return getattr(user, 'employee_profile', None) or getattr(user, 'employee', None)


def is_superadmin(user):
    return bool(user and user.is_authenticated and user.is_superuser)


def user_has_role(user, role_keys):
    """Helper to check if user has any of the specified RBAC role keys."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    emp = get_employee_profile(user)
    if not emp:
        return False
    if isinstance(role_keys, str):
        role_keys = [role_keys]
    return emp.role_assignments.filter(role__role_key__in=role_keys, is_active=True).exists()


def is_hr(user):
    """Strictly checks if user belongs to HR, has HR permissions, or is Superadmin."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # 1. Central RBAC check
    try:
        from access_control.services import can
        if can(user, "employee.manage") or can(user, "leave.manage"):
            return True
    except Exception:
        pass

    if user_has_role(user, ["admin", "hr"]):
        return True

    # 2. Legacy group check
    if user.groups.filter(name__iexact='HR').exists():
        return True

    # 3. Department fallback
    emp = get_employee_profile(user)
    if emp and emp.department:
        dept_name = emp.department.name.lower()
        if any(keyword in dept_name for keyword in ['hr', 'human resource', 'people ops']):
            return True
    return False


def is_manager(user):
    """Checks if the logged-in user has manager permissions, manager roles, or active subordinates."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # 1. Central RBAC check
    try:
        from access_control.services import can
        if can(user, "manager.dashboard") or can(user, "leave.approve"):
            return True
    except Exception:
        pass

    if user_has_role(user, ["admin", "manager", "business", "sales_head", "rsm", "asm"]):
        return True

    emp = get_employee_profile(user)
    if not emp:
        return False
    if getattr(emp, 'is_manager', False):
        return True
    # Check if any employee lists this user as their reporting manager
    return emp.subordinates.filter(status='active').exists() if hasattr(emp, 'subordinates') else False


def is_finance(user):
    """Checks if user has Finance / Payroll rights via RBAC roles or permissions."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # 1. Central RBAC permissions check
    try:
        from access_control.services import can
        if can(user, "payroll.manage") or can(user, "payroll.payout") or can(user, "payroll.run"):
            return True
    except Exception:
        pass

    # 2. RBAC role assignment check
    if user_has_role(user, ["admin", "accounts_operations"]):
        return True

    # 3. Fallback to standard Django Finance groups if assigned
    if user.groups.filter(name__in=['Finance', 'Accounts', 'Payroll']).exists():
        return True

    return False


def get_role(user):
    """Determines primary UI dashboard persona."""
    if not user.is_authenticated:
        return None
    if is_superadmin(user):
        return ROLE_SUPERADMIN
    if is_hr(user):
        return ROLE_HR
    if is_manager(user):
        return ROLE_MANAGER
    return ROLE_EMPLOYEE


def is_hr_or_above(user):
    """True for Superadmin and HR only (Managers are excluded)."""
    return is_superadmin(user) or is_hr(user)


def is_manager_or_above(user):
    """True for Superadmin, HR, and Reporting Managers."""
    return is_superadmin(user) or is_hr(user) or is_manager(user)


def can_access_payroll(user):
    """Permissions for Payroll runs, Salary Structures, and Payslip access."""
    if is_superadmin(user) or is_hr(user) or is_finance(user):
        return True
    try:
        from access_control.services import can
        if can(user, "payroll.view") or can(user, "payroll.run") or can(user, "payroll.manage"):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Function-Based View Decorators
# ---------------------------------------------------------------------------
def hr_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not is_hr_or_above(request.user):
            raise PermissionDenied('HR or Admin access required.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def manager_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not is_manager_or_above(request.user):
            raise PermissionDenied('Reporting Manager, HR, or Admin access required.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def finance_or_hr_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not can_access_payroll(request.user):
            raise PermissionDenied('Finance, HR, or Admin access required to access Payroll.')
        return view_func(request, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Class-Based View Mixins
# ---------------------------------------------------------------------------
class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return is_superadmin(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied('Super Admin access required.')


class HRRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return is_hr_or_above(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied('HR or Admin access required.')


class ManagerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return is_manager_or_above(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied('Reporting Manager, HR, or Admin access required.')


class FinanceOrHRRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Protects Payroll & Salary CBVs."""
    def test_func(self):
        return can_access_payroll(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied('Finance or HR access required.')


class EmployeeSelfOrHRMixin(LoginRequiredMixin, UserPassesTestMixin):
    def get_object_employee(self, obj):
        return getattr(obj, 'employee', obj)

    def test_func(self):
        user = self.request.user
        if is_hr_or_above(user):
            return True
        obj = self.get_object() if hasattr(self, 'get_object') else None
        if obj is None:
            return True
        employee = get_employee_profile(user)
        return employee is not None and self.get_object_employee(obj) == employee