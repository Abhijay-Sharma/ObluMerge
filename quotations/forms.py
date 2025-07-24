# quotations/forms.py
from django import forms
from .models import Quotation, QuotationItem, Product

class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = ['customer_name', 'customer_address']

class QuotationItemForm(forms.ModelForm):
    class Meta:
        model = QuotationItem
        fields = ['product', 'quantity','discount','tax']

from django.forms import modelformset_factory

QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    extra=1,  # Show at least 1 row initially
    can_delete=True
)
