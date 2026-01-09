from django import forms
from .models import Customer

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
