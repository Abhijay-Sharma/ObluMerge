from django.db import models
from django.contrib.auth.models import AbstractUser
import calendar
from django.db.models import Sum
from django.db.models.functions import TruncMonth
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




