from django.db import models
from django.utils import timezone
from decimal import Decimal

from inventory.models import InventoryItem



class Voucher(models.Model):
    date = models.DateField()
    voucher_type = models.CharField(max_length=100)
    voucher_number = models.CharField(max_length=100)
    party_name = models.CharField(max_length=255)
    voucher_category = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.voucher_type} {self.voucher_number} - {self.party_name}"


class VoucherRow(models.Model):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="rows")

    ledger = models.CharField(max_length=255)
    narration = models.TextField(blank=True, null=True)
    amount = models.FloatField()  # always POSITIVE

    def __str__(self):
        return f"{self.ledger} ({self.amount})"



class VoucherStockItem(models.Model):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="stock_rows")

    # Link to InventoryItem if exists
    item = models.ForeignKey(InventoryItem, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="voucher_rows")

    # Raw name if InventoryItem is not found
    item_name_text = models.CharField(max_length=255, blank=True, null=True)

    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    godown = models.CharField(max_length=255, null=True, blank=True)

    date_created = models.DateTimeField(auto_now_add=True)


    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "voucher",
                    "item",
                    "item_name_text",
                    "quantity",
                    "amount",
                    "godown"
                ],
                name="unique_stock_item_per_voucher"
            )
        ]
    def save(self, *args, **kwargs):
        # always positive
        if self.quantity:
            self.quantity = abs(Decimal(self.quantity))
        if self.amount:
            self.amount = abs(Decimal(self.amount))

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item or self.item_name_text} - {self.quantity}"