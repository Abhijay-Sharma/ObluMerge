from django.db import models
# Create your models here.
# quotations/models.py
from django.db import models
from django.conf import settings

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

    def __str__(self):
        return self.name


class Quotation(models.Model):

    customer_name = models.CharField(max_length=255)
    customer_address = models.TextField()
    date_created = models.DateTimeField(auto_now_add=True)
    created_by=models.CharField(max_length=255,default="Bhavya")

    def total(self):
        return sum(item.total_price() for item in self.items.all())


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)  # flat amount discount
    tax= models.DecimalField(max_digits=10, decimal_places=2, default=0.0)

    def total_price(self):
        unit_price = self.product.price_per_unit

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
        return self.product.price_per_unit

    def unit_price_without_tax(self):
        price_without_tax=(self.product.price_per_unit*100)/(self.product.tax_rate+100)
        return int(price_without_tax)


class Customer(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField()
    state = models.CharField(max_length=255, default="Unknown")
    city = models.CharField(max_length=255, default="Unknown")
    pin_code = models.CharField(max_length=10, default="000000")
    phone = models.BigIntegerField(default=9999999999)  # numeric phone
    email = models.EmailField(blank=True, null=True)


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