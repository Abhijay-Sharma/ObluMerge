from django import forms
from .models import Customer, CustomerCreditProfile

class CustomerReassignForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["phone", "email", "salesperson"]

        widgets = {
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Phone number"
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "Email address"
            }),
            "salesperson": forms.Select(attrs={
                "class": "form-control"
            }),
        }

class CustomerCreditForm(forms.ModelForm):
    class Meta:
        model = CustomerCreditProfile
        fields = ["credit_period_days"]
        widgets = {
            "credit_period_days": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter credit period in days"
                }
            )
        }
