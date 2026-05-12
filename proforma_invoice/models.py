# proforma_invoice/models.py

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
# Import existing models
from customer_dashboard.models import Customer
from inventory.models import InventoryItem
from django.urls import reverse
from decimal import Decimal
from num2words import num2words
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
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
    # NEW: The Maximum Selling Price / Manufacturer Suggested Price
    msrp = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00, null=True,blank=True,
        help_text="The ceiling price. Requests above this are auto-approved."
    )

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

    # ------add field---------
    msrp = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00, null=True,blank=True,
        help_text="The ceiling price. Requests above this are auto-approved."
    )



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
    shipping_customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipping_invoices"
    )
    date_created = models.DateTimeField(auto_now_add=True)
    validity = models.DateTimeField(default=validity_default)
    created_by = models.CharField(max_length=255, default="Oblu")
    DISPATCH_CHOICES = [
        ('processing', 'Still Processing'),
        ('pending', 'Pending'),
        ('dispatched', 'Dispatched'),
    ]
    dispatch_status = models.CharField(
        max_length=20,
        choices=DISPATCH_CHOICES,
        default='processing'
    )
    dispatch_requested_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    is_price_altered = models.BooleanField(default=False)

    courier_mode = models.CharField(
        max_length=10,
        choices=CourierMode.choices,
        default=CourierMode.SURFACE)

    # 🔥 NEW FIELD  (Converted-pi)
    is_converted_to_pi = models.BooleanField(default=False, help_text="Converted-pi")




    def is_intra_state(self):
        """
        True  -> CGST + SGST
        False -> IGST
        """

        seller_state = "Delhi"

        if self.shipping_customer:
            supply_state = self.shipping_customer.state
        else:
            supply_state = self.customer.state

        return supply_state == seller_state

    def gst_type(self):
        """
        Returns the GST type label used in templates.
        """

        if self.is_intra_state():
            return "CGST + SGST"
        return "IGST"

    def ship_to(self):
        return self.shipping_customer if self.shipping_customer else self.customer

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

        # print("\n========== COURIER CHARGE DEBUG ==========")
        # print("INVOICE:", self.id)
        # print("MODE:", self.courier_mode)
        #
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

            # print(f"\nCATEGORY: {category.name}")
            # print("TOTAL QTY:", total_qty)
            #
            sheet = product.courier_sheets.filter(
                mode=self.courier_mode
            ).first()

            if not sheet:
                # print("❌ NO COURIER SHEET FOUND")
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

            # print("MATCHED TIER:", tier)

            if tier:
                total_courier += tier.charge
                # print("ADDED CHARGE:", tier.charge)

        # print("\nTOTAL COURIER CHARGE:", total_courier)
        # print("========================================\n")

        return total_courier

    def courier_charge(self):

        total_courier = Decimal("0.00")

        items = self.items.select_related(
            "product",
            "product__category"
        )

        total_sheet_qty = Decimal("0")
        sheet_product = None

        # Categories that must be summed together
        SHEET_CATEGORIES = [
            "THERMOFORMING SHEETS",
            "BAY MATERIALS",
        ]

        # Categories that charge per unit
        PER_UNIT_KEYWORDS = [
            "PRINTER",
            "RESIN",
            "ANYCUBIC",
            "FILAMENT",
            "MACHINE",
        ]

        # -------------------------
        # STEP 1 — SUM SHEETS
        # -------------------------

        for item in items:

            qty = Decimal(str(item.quantity or 0))
            if qty <= 0:
                continue

            product = item.product

            category_name = (
                product.category.name.upper()
                if product.category else ""
            )

            # print("CATEGORY:", category_name)

            if category_name in SHEET_CATEGORIES:

                total_sheet_qty += qty

                if not sheet_product:
                    sheet_product = product

        # print("TOTAL SHEET QTY:", total_sheet_qty)

        # -------------------------
        # STEP 2 — APPLY SHEET SLAB ONCE
        # -------------------------

        if total_sheet_qty > 0 and sheet_product:

            try:

                sheet_rule = CourierCharge.objects.get(
                    product=sheet_product,
                    mode=self.courier_mode
                )

            except CourierCharge.DoesNotExist:

                sheet_rule = None

            if sheet_rule:

                tier = (
                    sheet_rule.tiers
                    .filter(min_quantity__lte=total_sheet_qty)
                    .filter(
                        Q(max_quantity__gte=total_sheet_qty)
                        | Q(max_quantity__isnull=True)
                    )
                    .order_by("-min_quantity")
                    .first()
                )

                if tier:
                    # print("SHEET CHARGE:", tier.charge)

                    total_courier += tier.charge

        # -------------------------
        # STEP 3 — OTHER PRODUCTS
        # -------------------------

        for item in items:

            qty = Decimal(str(item.quantity or 0))
            if qty <= 0:
                continue

            product = item.product

            category_name = (
                product.category.name.upper()
                if product.category else ""
            )

            # Skip sheets (already handled)
            if category_name in SHEET_CATEGORIES:
                continue

            try:

                rule = CourierCharge.objects.get(
                    product=product,
                    mode=self.courier_mode
                )

            except CourierCharge.DoesNotExist:
                continue

            tier = (
                rule.tiers
                .filter(min_quantity__lte=qty)
                .filter(
                    Q(max_quantity__gte=qty)
                    | Q(max_quantity__isnull=True)
                    | Q(max_quantity=0)
                )
                .order_by("-min_quantity")
                .first()
            )

            if not tier:
                continue

            # Detect per-unit categories safely
            is_per_unit = any(
                keyword in category_name
                for keyword in PER_UNIT_KEYWORDS
            )

            if is_per_unit:

                charge = qty * tier.charge

            else:

                charge = tier.charge

            # print(
            #     "PRODUCT:", product.name,
            #     "| CATEGORY:", category_name,
            #     "| QTY:", qty,
            #     "| RATE:", tier.charge,
            #     "| CHARGE:", charge
            # )
            #
            total_courier += charge

        # print("FINAL COURIER:", total_courier)

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
        amount = self.grand_total().quantize(Decimal("0.01"))

        rupees = int(amount)
        paise = int((amount - Decimal(rupees)) * 100)

        words = (
                num2words(rupees, lang="en_IN")
                .replace(",", "")
                .title()
                + " Rupees"
        )

        if paise > 0:
            words += (
                    " "
                    + num2words(paise, lang="en_IN")
                    .replace(",", "")
                    .title()
                    + " Paise"
            )

        words += " Only"
        return words

    def grand_total_in_words(self):
        amount = self.grand_total().quantize(Decimal("0.01"))

        rupees = int(amount)
        paise = int((amount - Decimal(rupees)) * 100)

        words = (
                num2words(rupees, lang="en_IN")
                .replace(",", "")
                .title()
                + " Rupees"
        )

        if paise > 0:
            words += (
                    " "
                    + num2words(paise, lang="en_IN")
                    .replace(",", "")
                    .title()
                    + " Paise"
            )

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

    # 🔥 NEW FIELDS  (# added section)
    customer_name_snapshot = models.CharField(max_length=255, blank=True, null=True, help_text="customer")

    current_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Price at time of creation"
    )
    requested_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Price requested by user"
    )

    current_courier_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Current - courier"
    )
    requested_courier_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Requested - courier"
    )

    current_msrp = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Current - MSRP"
    )

    stock_requested = models.IntegerField(
        default=0,
        help_text="Stock-requested (Integer)"
    )

    made_by = models.CharField(
        max_length=255, null=True, blank=True,
        help_text="made-by"
    )

    # =====================================================
    # 🔥 LOGIC UPDATES
    # =====================================================

    def save(self, *args, **kwargs):
        """
        Auto-populate snapshot fields from the product/invoice
        on first save if not provided.
        """
        if not self.pk:
            # Snapshot the current values from the ProductPrice model
            price_obj = getattr(self.product, "proforma_price", None)
            if price_obj:
                self.current_price = self.get_unit_price_incl_tax()
                self.current_msrp = price_obj.msrp

            self.customer_name_snapshot = self.invoice.customer.name

            # Initial stock requested equals current quantity
            if not self.stock_requested:
                self.stock_requested = self.quantity

        super().save(*args, **kwargs)

    # =====================================================
    # 🔥 SINGLE SOURCE OF TRUTH FOR UNIT PRICE (INC GST)
    # =====================================================
    def get_unit_price_incl_tax(self):
        """
        Returns correct unit price INCLUDING GST
        - Applies dynamic tier pricing if enabled
        - Falls back to base price
        """
        try:
            if not self.product: return Decimal("0.00")
            p = self.product
        except:
            return Decimal("0.00")

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
        try:
            if not self.product: return
            p = self.product
        except:
            return


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
        # available_qty = getattr(self.product, "quantity", 0)
        # if self.quantity > available_qty:
        #     raise ValidationError(
        #         f"Only {available_qty} units available in stock "
        #         f"for {self.product.name}."
        #     )
        #

    def save(self, *args, **kwargs):
        if not self.pk:
            # ONLY snapshot from master if the field is currently empty
            if not self.current_price or self.current_price == 0:
                price_obj = getattr(self.product, "proforma_price", None)
                if price_obj:
                    # This is where 180 was coming from
                    self.current_price = self.get_unit_price_incl_tax()

            if not self.current_msrp:
                price_obj = getattr(self.product, "proforma_price", None)
                if price_obj:
                    self.current_msrp = price_obj.msrp

            self.customer_name_snapshot = self.invoice.customer.name
            if not self.stock_requested:
                self.stock_requested = self.quantity

        super().save(*args, **kwargs)



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
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    # True = Product Price Request, False = Courier Request
    is_product_request = models.BooleanField(default=True)

    invoice = models.ForeignKey(
        ProformaInvoice,
        on_delete=models.CASCADE,
        related_name="price_requests"
    )

    # --- NEW: Link Customer directly for easier filtering/memory check ---
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True)
    # If is_product_request is True, we need to know which product
    product = models.ForeignKey(
        InventoryItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Only required if is_product_request is True")


    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proforma_price_requests_made" )


    # --- UPDATED: JSON Structure will now store: --- ❌
    # { "item_id": {"req_price": 60, "rec_price": 100, "msrp": 70, "under_msrp": true} } ❌
    #removed JSON PATTERN

# -----product fields --------
    requested_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    recommended_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    msrp_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)


# -------For courier-------
    requested_courier_charge = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    recommended_courier_charge = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)


    # NEW: Track courier status specifically
    COURIER_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    courier_status = models.CharField(
        max_length=10,
        choices=COURIER_STATUS_CHOICES,
        default="pending"
    )

    # Note: requested_product_prices will now look like this:
    # {
    #   "101": {"requested_price": 500, "recommended_price": 600, "status": "approved"},
    #   "102": {"requested_price": 400, "recommended_price": 600, "status": "rejected"}
    # }



    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    # --- NEW: Approval Logic Fields ---
    is_under_msrp = models.BooleanField(
        default=False,
        help_text="True if ANY item in this request is priced below its MSRP"
    )

    # Tracks which role has signed off
    accountant_approved = models.BooleanField(default=False)
    superuser_approved = models.BooleanField(default=False)

    # --- NEW: Permitted Field (Auto-unlock) ---
    is_permitted = models.BooleanField(
        default=False,
        help_text="Ticked if this price/customer combo was already approved in the past"
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="proforma_price_requests_reviewed"
    )

    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    recommended_price= models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_this_product_price = models.BooleanField(
        default=False,
    )

    def save(self, *args, **kwargs):
        # 1. Logic for Product Requests
        if self.is_product_request:
            # Clear courier fields to ensure data integrity
            self.requested_courier_charge = None

            # Auto-calculate MSRP status
            if self.requested_price and self.msrp_snapshot:
                self.is_under_msrp = self.requested_price < self.msrp_snapshot

        # 2. Logic for Courier Requests
        else:
            # Clear product fields
            self.product = None
            self.requested_price = None
            self.msrp_snapshot = None
            self.is_under_msrp = False

        super().save(*args, **kwargs)

    def __str__(self):
        req_type = "Product" if self.is_product_request else "Courier"
        return f"{req_type} Request #{self.id} (Inv: {self.invoice_id}) - MSRP Status: {self.is_under_msrp}"

class ApprovedPriceMemory(models.Model):
    """
    Stores previously approved prices for a specific Customer + Product combination.
    Used to implement the 'is_permitted' logic.
    """
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    product = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    min_approved_price = models.DecimalField(max_digits=10, decimal_places=2)
    # If the base price (recommended price) changes, this memory becomes invalid
    base_price_at_approval = models.DecimalField(max_digits=10, decimal_places=2)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('customer', 'product')

    def __str__(self):
        return f"{self.customer.name} | {self.product.name} | Min: ₹{self.min_approved_price}"

class ProformaStockShortageRequest(models.Model):
    """Handles requests where quantity ordered > warehouse stock."""
    STATUS_CHOICES = [("pending", "Pending Approval"), ("approved", "Stock Confirmed"), ("rejected", "Unavailable")]

    invoice = models.OneToOneField(ProformaInvoice, on_delete=models.CASCADE, related_name="stock_request")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    shortage_details = models.JSONField(encoder=DjangoJSONEncoder)  # { "Product Name": "Requested: 10, Available: 2" }
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="stock_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ProformaRemark(models.Model):
    # 1. Links
    invoice = models.ForeignKey('ProformaInvoice', on_delete=models.CASCADE, related_name="remarks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # 2. Content
    remark = models.TextField()

    # 3. Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def str(self):
        return f"{self.user.username} - {self.created_at.strftime('%d %b, %H:%M')}"


