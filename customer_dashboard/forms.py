from django import forms
from .models import Customer, CustomerCreditProfile
from .models import PaymentRemark, PaymentExpectedDateHistory

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




class PaymentRemarkForm(forms.ModelForm):
    class Meta:
        model = PaymentRemark
        fields = ["remark"]
        widgets = {
            "remark": forms.Textarea(attrs={"rows": 3})
        }


class ExpectedDateForm(forms.ModelForm):
    class Meta:
        model = PaymentExpectedDateHistory
        fields = ["expected_date"]
        widgets = {
            "expected_date": forms.DateInput(attrs={"type": "date"})
        }