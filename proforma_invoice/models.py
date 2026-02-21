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
from num2words import num2words
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

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

    is_price_altered = models.BooleanField(default=False)

    courier_mode = models.CharField(
        max_length=10,
        choices=CourierMode.choices,
        default=CourierMode.SURFACE)

    def taxable_total(self):
        return sum(item.total_price_excl_tax() for item in self.items.all())

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

        # category -> {"qty": int, "product": InventoryItem}
        category_data = {}

        print("\n========== COURIER CHARGE DEBUG ==========")
        print("INVOICE:", self.id)
        print("MODE:", self.courier_mode)

        # 🔹 Group quantities by category
        for item in self.items.all():
            category = item.product.category

            if category not in category_data:
                category_data[category] = {
                    "qty": 0,
                    "product": item.product,  # 👈 store product here
                }

            category_data[category]["qty"] += item.quantity

        # 🔹 Apply courier slab ONCE per category
        for category, data in category_data.items():
            total_qty = data["qty"]
            product = data["product"]

            print(f"\nCATEGORY: {category.name}")
            print("TOTAL QTY:", total_qty)

            sheet = product.courier_sheets.filter(
                mode=self.courier_mode
            ).first()

            if not sheet:
                print("❌ NO COURIER SHEET FOUND")
                continue

            tier = (
                sheet.tiers
                .filter(min_quantity__lte=total_qty)
                .filter(
                    models.Q(max_quantity__gte=total_qty) |
                    models.Q(max_quantity__isnull=True)
                )
                .order_by("-min_quantity")
                .first()
            )

            print("MATCHED TIER:", tier)

            if tier:
                total_courier += tier.charge
                print("ADDED CHARGE:", tier.charge)

        print("\nTOTAL COURIER CHARGE:", total_courier)
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

    def grand_total_in_words(self):
        amount = Decimal(self.grand_total() or 0).quantize(Decimal("1.00"))

        # Split into rupees and paise
        rupees = int(amount)
        paise = int((amount - rupees) * 100)

        words = num2words(rupees, lang='en_IN').replace(",", "").title() + " Rupees"

        if paise > 0:
            words += " " + num2words(paise, lang='en_IN').replace(",", "").title() + " Paise"

        words += " Only"
        return words

    def igst_total(self):
        """
        Total IGST = Product GST + Courier GST
        """
        product_gst = self.items_total() - self.taxable_total()
        return product_gst + self.courier_gst()

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

    # =====================================================
    # 🔥 SINGLE SOURCE OF TRUTH FOR UNIT PRICE (INC GST)
    # =====================================================
    def get_unit_price_incl_tax(self):
        """
        Returns correct unit price INCLUDING GST
        - Applies dynamic tier pricing if enabled
        - Falls back to base price
        """
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return Decimal("0.00")

        unit_price = price_obj.price  # base price (inc GST)

        # 🔥 CHANGED: dynamic tier logic centralized here
        if price_obj.has_dynamic_price:
            tier = (
                price_obj.price_tiers
                .filter(min_quantity__lte=self.quantity)
                .order_by("-min_quantity")
                .first()
            )
            if tier:
                unit_price = tier.unit_price

        return unit_price

    # =====================================================
    # TOTAL PRICE (INC GST)
    # =====================================================
    def total_price(self):
        """
        Total price INCLUDING GST
        """
        # 🔥 CHANGED: now uses centralized pricing logic
        return self.get_unit_price_incl_tax() * self.quantity

    # =====================================================
    # UNIT PRICE (INC GST)
    # =====================================================
    def unit_price(self):
        """
        Unit price INCLUDING GST
        """
        # 🔥 CHANGED: earlier always returned base price
        return self.get_unit_price_incl_tax()

    # =====================================================
    # UNIT PRICE (EXCLUDING GST)
    # =====================================================
    def unit_price_excl_tax(self):
        """
        Unit price EXCLUDING GST
        """
        unit_price = self.get_unit_price_incl_tax()  # 🔥 CHANGED
        tax_rate = self.taxrate() or 0

        return unit_price / (1 + (tax_rate / 100))

    # =====================================================
    # TOTAL PRICE (EXCLUDING GST)
    # =====================================================
    def total_price_excl_tax(self):
        return self.unit_price_excl_tax() * self.quantity

    # =====================================================
    # TAX / HSN HELPERS
    # =====================================================
    def taxrate(self):
        price_obj = getattr(self.product, "proforma_price", None)
        return price_obj.tax_rate if price_obj else 0

    def hsn(self):
        price_obj = getattr(self.product, "proforma_price", None)
        return price_obj.hsn if price_obj else None

    # =====================================================
    # VALIDATION
    # =====================================================
    def clean(self):
        """
        Validation before saving:
        - ensure price exists
        - ensure minimum order quantity
        - ensure stock availability
        """
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            raise ValidationError(
                f"No price defined for {self.product.name}."
            )

        # Minimum order check
        if self.quantity < price_obj.min_requirement:
            raise ValidationError(
                f"Minimum order for {self.product.name} is "
                f"{price_obj.min_requirement} units."
            )

        # Stock check
        available_qty = getattr(self.product, "quantity", 0)
        if self.quantity > available_qty:
            raise ValidationError(
                f"Only {available_qty} units available in stock "
                f"for {self.product.name}."
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


    def __str__(self):
        if self.max_quantity:
            return f"{self.courier_product}+→{self.min_quantity}-{self.max_quantity} → ₹{self.charge}"
        return f"{self.courier_product}+→{self.min_quantity}+ → ₹{self.charge}"


class ProformaPriceChangeRequest(models.Model):
    """
    Stores a request to change product prices and/or courier charge
    in a Proforma Invoice.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    invoice = models.ForeignKey(
        ProformaInvoice,
        on_delete=models.CASCADE,
        related_name="price_requests"
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proforma_price_requests_made"
    )

    # 🔥 Product price overrides
    # { "invoice_item_id": "new_unit_price_incl_gst" }
    requested_product_prices = models.JSONField(
        encoder=DjangoJSONEncoder,
        blank=True,
        null=True
    )

    # 🔥 Courier charge override
    requested_courier_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )

    reason = models.TextField(blank=True, null=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proforma_price_requests_reviewed"
    )

    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ProformaPriceChangeRequest #{self.id} - Invoice #{self.invoice.id} ({self.status})"

