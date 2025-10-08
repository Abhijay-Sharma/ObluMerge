from django.db import models
from django.contrib.auth import get_user_model


User = get_user_model()


#this is models file

class SalesPerson(models.Model):
    name = models.CharField(max_length=255, unique=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
    related_name="salesperson_profile")

    def __str__(self):
        return self.name




class Customer(models.Model):
    name = models.CharField("Customer Name", max_length=255)
    email = models.EmailField(blank=True, null=True)
    pincode = models.CharField(max_length=20)
    address = models.TextField()
    state = models.CharField(max_length=128)
    district = models.CharField(max_length=128)
    phone = models.CharField(max_length=50)
    salesperson = models.ForeignKey(SalesPerson, null=True, blank=True, on_delete=models.SET_NULL,
    related_name="customers")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        unique_together = (('name', 'phone'),)


    def __str__(self):
        return f"{self.name} ({self.state})"