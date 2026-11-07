# access_control/models.py
from django.db import models
from django.utils import timezone


class Role(models.Model):
    role_key = models.CharField(max_length=50, unique=True, db_index=True)
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.display_name} ({self.role_key})"


class Permission(models.Model):
    permission_key = models.CharField(max_length=100, unique=True, db_index=True)
    display_name = models.CharField(max_length=150)
    module_name = models.CharField(max_length=50, db_index=True)

    def __str__(self):
        return f"[{self.module_name.upper()}] {self.display_name} ({self.permission_key})"


class RolePermission(models.Model):
    class ScopeChoice(models.TextChoices):
        SELF = "self", "Self (Own records)"
        TEAM = "team", "Team (Downline hierarchy)"
        DEPARTMENT = "department", "Department"
        COMPANY = "company", "Company"
        ALL = "all", "Organization-wide"

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")
    default_scope = models.CharField(max_length=20, choices=ScopeChoice.choices, default=ScopeChoice.SELF)

    class Meta:
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role.role_key} -> {self.permission.permission_key} [{self.default_scope}]"


class RoleAssignment(models.Model):
    employee = models.ForeignKey(
        'hrms.Employee',
        on_delete=models.CASCADE,
        related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="assigned_employees")
    is_active = models.BooleanField(default=True)

    # Optional time window for temporary/acting roles
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Start of temporary assignment (optional)")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Expiry of temporary assignment (optional)")

    class Meta:
        unique_together = ("employee", "role")

    def is_currently_valid(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    def __str__(self):
        validity = "Permanent" if not self.ends_at else f"Until {self.ends_at.strftime('%Y-%m-%d')}"
        return f"{self.employee.full_name} -> {self.role.display_name} ({validity})"