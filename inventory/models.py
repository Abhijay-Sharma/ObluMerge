from django.db import models
from django.contrib.auth.models import AbstractUser
import calendar
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from decimal import Decimal
from django.utils import timezone
# Create your models here.

class User(AbstractUser):
    is_accountant = models.BooleanField(default=False)
    is_viewer = models.BooleanField(default=True)
    pass

class InventoryItem(models.Model):
    name=models.CharField(max_length=200)
    quantity=models.IntegerField(null=True)
    category=models.ForeignKey('Category', on_delete=models.SET_NULL, blank=True, null=True)
    date_created=models.DateTimeField(auto_now_add=True)
    min_quantity = models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_closing = models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_outwards = models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_gpt = models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_average = models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_average_three=models.IntegerField(null=True, blank=True, default=-1)
    min_quantity_nitin=models.IntegerField(null=True, blank=True, default=-1)
    unit=models.CharField(max_length=200, blank=True, null=True)
    total_historical_entries=models.IntegerField(null=True, blank=True, default=0)
    expected_delivery = models.DateField(null=True, blank=True)
    expected_quantity = models.IntegerField(null=True, blank=True)
    expected_delivery_days = models.IntegerField(
        null=True, blank=True,
        help_text="Number of days expected for delivery after placing a purchase order"
    )
    minimum_order_quantity = models.IntegerField(
        null=True, blank=True,
        help_text="Minimum quantity that must be ordered from the supplier in a single batch"
    )


    def __str__(self):
        return self.name

    def get_monthly_outwards_history(self):
        from inventory.models import DailyStockData

        # Group by month and sum outward quantities
        qs = (
            DailyStockData.objects
            .filter(product=self)
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(total_outwards=Sum('outwards_quantity'))
            .order_by('month')
        )

        # Convert to desired structure
        history = []
        for entry in qs:
            if entry['total_outwards'] is not None:
                history.append({
                    "month": entry['month'].strftime("%Y-%m"),
                    "outward_qty": float(entry['total_outwards'])
                })

        return history

class Category(models.Model):
    name=models.CharField(max_length=200)

    class Meta:
        verbose_name_plural="Categories"

    def __str__(self):
        return self.name

class MonthlyStockData(models.Model):
    product=models.ForeignKey(InventoryItem, on_delete=models.CASCADE,related_name='monthly_data')
    month = models.IntegerField(choices=[(i, calendar.month_name[i]) for i in range(1, 13)])
    year = models.IntegerField()

    inwards_quantity = models.FloatField(null=True, blank=True)
    inwards_value = models.FloatField(null=True, blank=True)

    outwards_quantity = models.FloatField(null=True, blank=True)
    outwards_value = models.FloatField(null=True, blank=True)

    closing_quantity = models.FloatField(null=True, blank=True)
    closing_value = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        month_name = calendar.month_name[self.month]
        return f"{self.product.name} - {month_name} {self.year}"

    def average_inward_rate(self):
        return self.inwards_value / self.inwards_quantity if self.inwards_quantity else 0

    def average_outward_rate(self):
        return self.outwards_value / self.outwards_quantity if self.outwards_quantity else 0

#units - kg, no, pcs, sets
class DailyStockData(models.Model):
    PRODUCT_UNITS = [
        ('no', 'Number'),
        ('pcs', 'Pieces'),
        ('kg', 'Kilograms'),
        ('set', 'Set'),
    ]

    VOUCHER_TYPES = [
        ('sale', 'Sale'),
        ('purc', 'Purchase'),
        ('C/Note', 'Credit Note'),
        ('D/Note', 'Debit Note'),
        ('Stk Jrnl', 'Stock Journal'),
    ]

    product=models.ForeignKey(InventoryItem, on_delete=models.CASCADE,related_name='daily_data')
    date = models.DateField()

    inwards_quantity = models.FloatField(null=True, blank=True)
    inwards_value = models.FloatField(null=True, blank=True)

    outwards_quantity = models.FloatField(null=True, blank=True)
    outwards_value = models.FloatField(null=True, blank=True)

    closing_quantity = models.FloatField(null=True, blank=True)
    closing_value = models.FloatField(null=True, blank=True)

    unit = models.CharField(max_length=5, choices=PRODUCT_UNITS, default='no')
    voucher_type = models.CharField(max_length=10, choices=VOUCHER_TYPES, default='sale')



    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)



    class Meta:
        ordering = ['-date']
        unique_together = ('product', 'date', 'voucher_type','inwards_quantity','outwards_quantity','closing_quantity')


    def __str__(self):
        return f"{self.product.name} - {self.date.strftime('%B %d, %Y')} - {dict(self.PRODUCT_UNITS).get(self.unit)}"



class PurchaseOrderTracking(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("arrived", "Arrived"),
        ("cancelled", "Cancelled"),
    ]

    tally_voucher = models.OneToOneField(
        "tally_voucher.Voucher",
        on_delete=models.CASCADE,
        related_name="purchase_order_tracking"
    )

    order_date = models.DateField()
    arrival_datetime = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active"
    )

    created_by = models.ForeignKey(
        "inventory.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_purchase_order_trackings"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    remarks = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"PO Tracking - {self.tally_voucher.voucher_number}"

    @property
    def party_name(self):
        return self.tally_voucher.party_name

    @property
    def voucher_number(self):
        return self.tally_voucher.voucher_number

    @property
    def total_days_taken(self):
        if self.arrival_datetime:
            return (self.arrival_datetime.date() - self.order_date).days
        return None

    def mark_arrived_if_final_stage_done(self):
        final_stage = self.stage_logs.filter(stage__is_final_stage=True).order_by("-exit_datetime").first()

        if final_stage and final_stage.exit_datetime and self.status != "arrived":
            self.status = "arrived"
            self.arrival_datetime = final_stage.exit_datetime
            self.save(update_fields=["status", "arrival_datetime", "updated_at"])


class PurchaseOrderTrackingItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrderTracking,
        on_delete=models.CASCADE,
        related_name="items"
    )

    voucher_stock_item = models.OneToOneField(
        "tally_voucher.VoucherStockItem",
        on_delete=models.CASCADE,
        related_name="purchase_order_tracking_item"
    )

    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    item_name_text = models.CharField(max_length=255, blank=True, null=True)

    ordered_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    arrived_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_fully_arrived = models.BooleanField(default=False)

    remarks = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.item_name_text or self.inventory_item} - {self.ordered_quantity}"

    def save(self, *args, **kwargs):
        if self.arrived_quantity is None:
            self.arrived_quantity = self.ordered_quantity

        self.is_fully_arrived = Decimal(self.arrived_quantity) >= Decimal(self.ordered_quantity)

        super().save(*args, **kwargs)


class PurchaseOrderStage(models.Model):
    name = models.CharField(max_length=150, unique=True)

    estimated_days = models.PositiveIntegerField(
        default=0,
        help_text="Expected number of days order usually stays in this stage"
    )

    is_final_stage = models.BooleanField(
        default=False,
        help_text="If this stage is completed, PO will be marked as arrived"
    )

    sort_order = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["sort_order", "name"]


class PurchaseOrderStageLog(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrderTracking,
        on_delete=models.CASCADE,
        related_name="stage_logs"
    )

    stage = models.ForeignKey(
        PurchaseOrderStage,
        on_delete=models.PROTECT,
        related_name="po_logs"
    )

    entered_at = models.DateTimeField(default=timezone.now)
    exit_datetime = models.DateTimeField(null=True, blank=True)

    manual_days_at_stage = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional manual duration. If blank, duration is calculated from entered/exited datetime."
    )

    remarks = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(
        "inventory.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["entered_at"]

    def __str__(self):
        return f"{self.purchase_order} - {self.stage.name}"

    @property
    def days_at_stage(self):
        if self.manual_days_at_stage is not None:
            return self.manual_days_at_stage

        end_time = self.exit_datetime or timezone.now()
        diff = end_time - self.entered_at
        return round(Decimal(diff.total_seconds()) / Decimal(86400), 2)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.stage.is_final_stage and self.exit_datetime:
            self.purchase_order.mark_arrived_if_final_stage_done()



