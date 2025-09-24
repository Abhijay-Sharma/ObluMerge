from django.db import models
# Create your models here.
# quotations/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class ProductCategory(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(ProductCategory, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate= models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    is_quantity_dependent = models.BooleanField(default=True)
    min_requirement = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    terms_and_conditions = models.TextField(blank=True, null=True)
    has_dynamic_pricing = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class ProductPriceTier(models.Model):
    product = models.ForeignKey("Product", related_name="price_tiers", on_delete=models.CASCADE)
    min_quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["min_quantity"]

    def __str__(self):
        return f"{self.product.name} - {self.min_quantity}+ @ {self.unit_price}"


class Quotation(models.Model):

    customer_name = models.CharField(max_length=255)
    customer_address = models.TextField()
    customer_state = models.TextField(blank=True, null=True)
    customer_city = models.TextField(blank=True, null=True)
    customer_pincode = models.TextField(blank=True, null=True)
    customer_company = models.TextField(blank=True, null=True)
    customer_phone = models.TextField(blank=True, null=True)
    customer_email = models.TextField(blank=True, null=True)
    customer_zip = models.TextField(blank=True, null=True)          #not needed added by mistake
    date_created = models.DateTimeField(auto_now_add=True)
    validity = models.DateTimeField(
        default=lambda: timezone.now() + timedelta(weeks=4)
    )

    created_by=models.CharField(max_length=255,default="Oblu")

    def total(self):
        return sum(item.total_price() for item in self.items.all())

    #  helper to get product-specific terms
    def product_terms(self):
        terms = []
        for item in self.items.all():
            if item.product.terms_and_conditions:
                terms.append(f"{item.product.name}: {item.product.terms_and_conditions}")
        return "\n".join(terms)


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)  # flat amount discount
    tax= models.DecimalField(max_digits=10, decimal_places=2, default=0.0)

    def total_price(self):
        unit_price = self.product.price_per_unit
        if self.product.has_dynamic_pricing:
            tier = (
                self.product.price_tiers.filter(min_quantity__lte=self.quantity)
                .order_by("-min_quantity")
                .first()
            )
            if tier:
                unit_price= tier.unit_price


        base_price = unit_price * self.quantity if self.product.is_quantity_dependent else unit_price

        # Apply flat discount
        discounted_price = base_price - self.discount

        # Apply tax on discounted price
        # tax_amount = (self.product.tax_rate / 100) * discounted_price
        # not applying tax now price input already has tax inclusion

        total = discounted_price
        return max(total, 0)  # prevent negative totals

    def gst_amount(self):
        total = self.total_price()
        unit_price_without_tax=self.unit_price_without_tax()
        return total - (unit_price_without_tax * self.quantity)

    def gst_unit_price(self):
        # return self.product.price_per_unit + ((self.product.tax_rate / 100) * self.product.price_per_unit)
        unit_price = self.product.price_per_unit
        if self.product.has_dynamic_pricing:
            tier = (
                self.product.price_tiers.filter(min_quantity__lte=self.quantity)
                .order_by("-min_quantity")
                .first()
            )
            if tier:
                unit_price= tier.unit_price

        return unit_price

    def unit_price_without_tax(self):
        unit_price = self.product.price_per_unit
        if self.product.has_dynamic_pricing:
            tier = (
                self.product.price_tiers.filter(min_quantity__lte=self.quantity)
                .order_by("-min_quantity")
                .first()
            )
            if tier:
                unit_price = tier.unit_price
        price_without_tax=(unit_price*100)/(self.product.tax_rate+100)
        return int(price_without_tax)


class Customer(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField()
    state = models.CharField(max_length=255, default="Unknown")
    city = models.CharField(max_length=255, default="Unknown")
    pin_code = models.CharField(max_length=10, default="000000")
    phone = models.BigIntegerField(default=9999999999)  # numeric phone
    email = models.EmailField(blank=True, null=True)
    company=models.CharField(max_length=255,blank=True, null=True)


    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # <-- use this instead of 'User'
        on_delete=models.CASCADE,
        related_name="customers",
        default = 1
    )

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"

    class Meta:
        unique_together = ('name', 'address', 'phone')
        ordering = ['name']