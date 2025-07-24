from django.db import models

# Create your models here.
# quotations/models.py
from django.db import models

class ProductCategory(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(ProductCategory, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    is_quantity_dependent = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Quotation(models.Model):
    customer_name = models.CharField(max_length=255)
    customer_address = models.TextField()
    date_created = models.DateTimeField(auto_now_add=True)

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
        tax_amount = (self.tax / 100) * discounted_price

        total = discounted_price + tax_amount
        return max(total, 0)  # prevent negative totals