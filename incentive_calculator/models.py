from django.db import models
from inventory.models import InventoryItem
from django.conf import settings
from customer_dashboard.models import CustomerVoucherStatus
from decimal import Decimal

# Create your models here.

class IncentiveCategory(models.Model):
    """
    Groups products like 'Sheets', 'Standard Resins', 'High-end Machines'.
    Example:
    Name: 'Sheets' -> ASM: 3, RSM: 1
    Name: 'Standard Resins' -> ASM: 100, RSM: 50
    """
    name = models.CharField(max_length=100, unique=True)
    base_ASM_incentive = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    base_RSM_incentive = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Incentive Categories"



class ProductIncentive(models.Model):
    product = models.OneToOneField(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="incentive_config"
    )
    category = models.ForeignKey(
        IncentiveCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="products"
    )

    asm_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rsm_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # MSP Logic: Default is 0.00
    msp = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    is_special_pack = models.BooleanField(default=False)
    pack_size_multiplier = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1.00,
        help_text="If pack of 5, set 5. If 5kg, set 5."
    )

    has_dynamic_price = models.BooleanField(default=False)

    def __str__(self):
        return self.product.name

    @property
    def get_effective_rates(self):
        # Ensure it returns Decimals, NOT floats
        asm = self.asm_override if self.asm_override is not None else (
            self.category.base_ASM_incentive if self.category else Decimal('0.00')
        )
        rsm = self.rsm_override if self.rsm_override is not None else (
            self.category.base_RSM_incentive if self.category else Decimal('0.00')
        )
        return asm, rsm

    def calculate_payout(self, sale_qty, sale_price):
        """
        Calculates payout.
        Note: If MSP is 0.00, the check is effectively skipped.
        """
        # 1. Check MSP
        # Logic: If msp is 0, it means 'No MSP set', so allow incentive.
        if self.msp > 0 and sale_price < self.msp:
            return 0, 0

        # 2. Get Rates
        asm_rate, rsm_rate = self.get_effective_rates

        # 3. Calculate with Multiplier
        effective_qty = sale_qty * float(self.pack_size_multiplier)

        total_asm = effective_qty * asm_rate
        total_rsm = effective_qty * rsm_rate

        return total_asm, total_rsm


class IncentivePaymentStatus(models.Model):
    # One-to-One ensures each invoice-status has exactly one incentive payment record
    voucher_status = models.OneToOneField(
        CustomerVoucherStatus,
        on_delete=models.CASCADE,
        related_name="incentive_payout_record"
    )

    is_paid_to_asm = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    # Lead's requirement: Freeze the amount so future rate changes don't affect history
    amount_frozen = models.DecimalField(max_digits=14, decimal_places=2)



    class Meta:
        verbose_name = "Incentive Payment Status"

    def __str__(self):
        return f"{self.voucher_status.voucher.voucher_number} - Paid: {self.is_paid_to_asm}"



class ProductIncentiveTier(models.Model):
    Product_Incentive = models.ForeignKey(ProductIncentive,on_delete=models.CASCADE)
    min_quantity = models.PositiveIntegerField()
    ASM_incentive = models.DecimalField(max_digits=10, decimal_places=2)
    RSM_incentive = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return self.Product_Incentive.product.name


# class ProductIncentive(models.Model):
#     product = models.OneToOneField(
#         InventoryItem,
#         on_delete=models.CASCADE,
#         related_name="incentive_price"
#     )
#     ASM_incentive = models.DecimalField(max_digits=10, decimal_places=2)
#     RSM_incentive = models.DecimalField(max_digits=10, decimal_places=2)
#
#     has_dynamic_price=models.BooleanField(default=False)
#
#     def __str__(self):
#         return self.product.name