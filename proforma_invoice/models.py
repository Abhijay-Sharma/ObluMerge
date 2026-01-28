# proforma_invoice/models.py

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

# Import existing models
from quotations.models import Customer
from inventory.models import InventoryItem
from django.urls import reverse
from decimal import Decimal

# 🧾 1. Product Pricing
class ProductPrice(models.Model):
    """
    Each InventoryItem can have a single base price and optional dynamic pricing tiers.
    """
    product = models.OneToOneField(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="proforma_price"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    has_dynamic_price = models.BooleanField(default=False)
    min_requirement = models.PositiveIntegerField(default=1)
    tax_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    terms_and_conditions = models.TextField(blank=True, null=True)
    hsn=models.DecimalField(max_digits=10, decimal_places=0, blank=True, null=True)

    class Meta:
        verbose_name = "Product Price"
        verbose_name_plural = "Product Prices"

    def __str__(self):
        return f"{self.product.name} - ₹{self.price}"


class ProductPriceTier(models.Model):
    """
    Quantity-based dynamic pricing tiers for a product.
    Example: Buy 10+ @ ₹95 each, 50+ @ ₹90 each, etc.
    """
    product = models.ForeignKey(
        ProductPrice,
        related_name="price_tiers",
        on_delete=models.CASCADE
    )
    min_quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["min_quantity"]
        verbose_name = "Product Price Tier"
        verbose_name_plural = "Product Price Tiers"

    def __str__(self):
        return f"{self.product.product.name} - {self.min_quantity}+ @ ₹{self.unit_price}"



# ------adding mode------
class CourierMode(models.TextChoices):
    SURFACE = "surface", "Surface"
    AIR = "air", "Air"


# 📅 2. Proforma Invoice Core Models
def validity_default():
    return timezone.now() + timedelta(weeks=2)


class ProformaInvoice(models.Model):
    """
    The main proforma invoice model — similar to a quotation but restricted to items in stock.
    """
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True)
    validity = models.DateTimeField(default=validity_default)
    created_by = models.CharField(max_length=255, default="Oblu")
    courier_mode = models.CharField(
        max_length=10,
        choices=CourierMode.choices,
        default=CourierMode.SURFACE)

    def taxable_total(self):
        return sum(item.amount_excl_tax() for item in self.items.all())

    def total(self):
        return sum(item.total_price() for item in self.items.all())

    def __str__(self):
        return f"Proforma #{self.id} - {self.customer.name}"

    def get_absolute_url(self):
        return reverse("proforma_detail", args=[self.pk])

    def items_total(self):
        """Sum of all products including GST"""
        # return sum(item.total_price_incl_gst() for item in self.items.all())
        return sum(item.total_price() for item in self.items.all())

    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    def courier_charge(self):
        total_courier = Decimal("0.00")

        print("\n========== COURIER CHARGE DEBUG ==========")
        print("INVOICE:", self.id)
        print("MODE:", self.courier_mode)

        for item in self.items.all():
            qty = item.quantity
            product = item.product

            print("\nPRODUCT:", product.name)
            print("QTY:", qty)

            sheet = product.courier_sheets.filter(mode=self.courier_mode).first()

            if not sheet:
                print("❌ NO COURIER SHEET FOUND")
                continue

            print("✔ SHEET FOUND:", sheet.mode)

            tiers = sheet.tiers.all()
            print("ALL TIERS:", list(tiers))

            tier = (
                tiers
                .filter(min_quantity__lte=qty)
                .filter(
                    models.Q(max_quantity__gte=qty) |
                    models.Q(max_quantity__isnull=True)
                )
                .order_by("-min_quantity")  # ✅ FIX
                .first()
            )

            print("MATCHED TIER:", tier)

            if tier:
                total_courier += tier.charge
                print("ADDED CHARGE:", tier.charge)
            else:
                print("❌ NO MATCHING TIER")

        print("TOTAL COURIER CHARGE:", total_courier)
        print("========================================\n")

        return total_courier

    def courier_gst(self):
        """
        Correct courier GST calculation:
        - Splits courier charge proportionally to product value
        - Applies exact GST rate per product from ProductPrice.tax_rate
        """
        total_courier = self.courier_charge()
        if total_courier == 0:
            return Decimal("0.00")

        total_value = sum(item.total_price() for item in self.items.all())
        if total_value == 0:
            return Decimal("0.00")

        total_gst = Decimal("0.00")

        for item in self.items.all():
            item_value = item.total_price()

            # Proportional courier part for this item
            courier_part = total_courier * (item_value / total_value)

            # Get exact GST rate from ProductPrice.tax_rate
            gst_rate = Decimal(item.taxrate() or 0)

            # Apply GST correctly
            gst_amount = courier_part * gst_rate / Decimal("100")

            total_gst += gst_amount

        return total_gst

    def courier_gst_breakup(self):
        """
        Returns courier GST split per product
        - courier_part: proportion of total courier based on item value
        - gst_rate: exact product GST from ProductPrice
        - gst_amount: courier_part * gst_rate / 100
        """
        breakup = []

        total_value = sum(item.total_price() for item in self.items.all())
        total_courier = self.courier_charge()

        if total_value == 0 or total_courier == 0:
            return breakup

        for item in self.items.all():
            item_value = item.total_price()

            # Split courier proportionally
            courier_part = total_courier * (item_value / total_value)

            # Exact GST rate from product
            gst_rate = Decimal(item.taxrate() or 0)

            # Correct GST amount
            gst_amount = courier_part * gst_rate / Decimal("100")

            breakup.append({
                "product": item.product.name,
                "quantity": item.quantity,
                "item_value": round(item_value, 2),
                "courier": round(courier_part, 2),
                "gst_rate": gst_rate,
                "gst_amount": round(gst_amount, 2),
                "total_courier_with_gst": round(courier_part + gst_amount, 2)
            })

        return breakup

    def grand_total(self):
        """Products incl GST + Courier incl GST"""
        # return self.items_total() + self.courier_charge() + self.courier_gst()
        return self.items_total() + self.courier_charge() + self.courier_gst()

    def __str__(self):
        return f"Proforma #{self.id} - {self.customer.name}"


class ProformaInvoiceItem(models.Model):
    """
    Items listed in a proforma invoice. Prices come from ProductPrice (and dynamic tiers if any).
    """
    invoice = models.ForeignKey(
        ProformaInvoice,
        related_name="items",
        on_delete=models.CASCADE
    )
    product = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    def total_price(self):
        """
        Calculate price dynamically, considering price tiers.
        """
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0

        unit_price = price_obj.price
        if price_obj.has_dynamic_price:
            tier = (
                price_obj.price_tiers.filter(min_quantity__lte=self.quantity)
                .order_by("-min_quantity")
                .first()
            )
            if tier:
                unit_price = tier.unit_price

        return unit_price * self.quantity

    def unit_price(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0

        # price already includes GST
        return price_obj.price

    def unit_price_excl_tax(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0

        price = price_obj.price  # GST included
        tax_rate = price_obj.tax_rate or 0  # safety

        return price / (1 + (tax_rate / 100))

    def total_price_excl_tax(self):
        return self.unit_price_excl_tax() * self.quantity

    def hsn(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0
        else:
            return price_obj.hsn

    def taxrate(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0
        else:
            return price_obj.tax_rate


    def clean(self):
        """
        Validation before saving:
        - ensure price exists
        - ensure stock available
        - ensure min requirement met
        """
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            raise ValidationError(f"No price defined for {self.product.name}.")

        # ✅ Check minimum requirement
        if self.quantity < price_obj.min_requirement:
            raise ValidationError(
                f"Minimum order for {self.product.name} is {price_obj.min_requirement} units."
            )

        # ✅ Check stock
        available_qty = getattr(self.product, "quantity", 0)
        if self.quantity > available_qty:
            raise ValidationError(
                f"Only {available_qty} units available in stock for {self.product.name}."
            )

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"


class CourierCharge(models.Model):
    product = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="courier_sheets"
    )
    mode = models.CharField(
        max_length=10,
        choices=CourierMode.choices,
        default=CourierMode.SURFACE
    )
    class Meta:
        unique_together = ("product", "mode")


    def __str__(self):
        return f"{self.product.name} - {self.mode}"

class CourierChargeTier(models.Model):
    """
    Quantity-based courier charge slabs
    Example:
    0–60   → 200
    100–200 → 600
    200–400 → 800
    """
    courier_product = models.ForeignKey(
        CourierCharge,
        related_name="tiers",
        on_delete=models.CASCADE
    )
    min_quantity = models.PositiveIntegerField()
    max_quantity = models.PositiveIntegerField(null=True, blank=True)
    charge = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["min_quantity"]

    def clean(self):
        """
        Prevent overlapping quantity slabs for same product + mode
        """
        qs = CourierChargeTier.objects.filter(sheet=self.sheet)

        # exclude self while editing
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        for tier in qs:
            tier_min = tier.min_quantity
            tier_max = tier.max_quantity

            # treat NULL max as infinity
            current_max = self.max_quantity or float("inf")
            existing_max = tier_max or float("inf")

            # overlap condition
            if (
                self.min_quantity <= existing_max
                and tier_min <= current_max
            ):
                raise ValidationError(
                    f"Overlapping slab detected: "
                    f"{tier_min}-{tier_max or '∞'} already exists."
                )

    def __str__(self):
        if self.max_quantity:
            return f"{self.courier_product}+→{self.min_quantity}-{self.max_quantity} → ₹{self.charge}"
        return f"{self.courier_product}+→{self.min_quantity}+ → ₹{self.charge}"

