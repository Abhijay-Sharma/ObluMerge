from django.db import models
from django.contrib.auth import get_user_model
from decimal import Decimal

User = get_user_model()


#this is models file

class SalesPerson(models.Model):
    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
    related_name="salesperson_profile")
    manager = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="team_members")
    def __str__(self):
        return self.name




class Customer(models.Model):
    name = models.CharField("Customer Name", max_length=255)
    email = models.EmailField(blank=True, null=True)
    pincode = models.CharField(max_length=20)
    address = models.TextField()
    state = models.CharField(max_length=128)
    district = models.CharField(max_length=128)
    phone = models.CharField(max_length=50)
    salesperson = models.ForeignKey(SalesPerson, null=True, blank=True, on_delete=models.SET_NULL,
    related_name="customers")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # new fields:
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)


    class Meta:
        unique_together = (('name', 'phone'),)


    def __str__(self):
        return f"{self.name} ({self.state})"

    @property
    def vouchers(self):
        from tally_voucher.models import Voucher
        return Voucher.objects.filter(party_name__iexact=self.name).order_by('-date')


class CustomerRemark(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="remarks")
    salesperson = models.ForeignKey(SalesPerson, on_delete=models.CASCADE)
    remark = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def str(self):
        return f"{self.customer.name} - {self.salesperson}"


class CustomerCreditProfile(models.Model):
    customer = models.OneToOneField(
        "Customer",
        on_delete=models.CASCADE,
        related_name="credit_profile"
    )

    # Outstanding balance as per Tally (uploaded from Excel)
    outstanding_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00")
    )

    # Credit period allowed for this customer (in days)
    credit_period_days = models.PositiveIntegerField(default=0)

    last_synced_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.customer.name} | Balance: {self.outstanding_balance}"


class CustomerVoucherStatus(models.Model):
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.CASCADE,
        related_name="voucher_statuses"
    )

    voucher = models.ForeignKey(
        "tally_voucher.Voucher",
        on_delete=models.CASCADE,
        related_name="customer_status"
    )

    # Always stored (for ALL voucher types)
    voucher_type = models.CharField(max_length=100)
    voucher_category = models.CharField(max_length=100)
    voucher_date = models.DateField()

    # -------------------------------
    # TAX INVOICE ONLY (nullable)
    # -------------------------------
    voucher_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True
    )

    unpaid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True
    )

    is_fully_paid = models.BooleanField(null=True, blank=True)
    is_partially_paid = models.BooleanField(null=True, blank=True)
    is_unpaid = models.BooleanField(null=True, blank=True)

    credit_days_elapsed = models.PositiveIntegerField(null=True, blank=True)
    is_credit_period_crossed = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("customer", "voucher")
        ordering = ["-voucher_date"]

    def __str__(self):
        return f"{self.customer.name} | {self.voucher.voucher_number}"


class CustomerFollowUp(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="followups"   # ✅ ONLY here
    )
    salesperson = models.ForeignKey(
        SalesPerson,
        on_delete=models.CASCADE,
        related_name="followups")
    note = models.TextField(blank=True)
    followup_date = models.DateField()
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def str(self):
        return f"{self.customer.name} - {self.followup_date}"