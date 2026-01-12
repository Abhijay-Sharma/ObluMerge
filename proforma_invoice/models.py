# proforma_invoice/models.py

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

# Import existing models
from quotations.models import Customer
from inventory.models import InventoryItem
from django.urls import reverse

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

    def total(self):
        return sum(item.total_price() for item in self.items.all())

    def __str__(self):
        return f"Proforma #{self.id} - {self.customer.name}"

    def get_absolute_url(self):
        return reverse("proforma_detail", args=[self.pk])


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

    def unit_price(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0
        else:
            return price_obj.price

    def unit_price_excl_tax(self):
        price_obj = getattr(self.product, "proforma_price", None)
        if not price_obj:
            return 0
        else:
            price = price_obj.price
            taxrate=price_obj.tax_rate
            return price / (1+(taxrate/100))



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
