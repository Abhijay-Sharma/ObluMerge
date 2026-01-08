from django.db import models
from inventory.models import InventoryItem

# Create your models here.

class ProductIncentive(models.Model):
    product = models.OneToOneField(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="incentive_price"
    )
    ASM_incentive = models.DecimalField(max_digits=10, decimal_places=2)
    RSM_incentive = models.DecimalField(max_digits=10, decimal_places=2)

    has_dynamic_price=models.BooleanField(default=False)

    def __str__(self):
        return self.product.name

class ProductIncentiveTier(models.Model):
    Product_Incentive = models.ForeignKey(ProductIncentive,on_delete=models.CASCADE)
    min_quantity = models.PositiveIntegerField()
    ASM_incentive = models.DecimalField(max_digits=10, decimal_places=2)
    RSM_incentive = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return self.Product_Incentive.product.name




