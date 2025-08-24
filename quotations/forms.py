# quotations/forms.py
from django import forms
from .models import Quotation, QuotationItem, Product

class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = ['customer_name', 'customer_address', 'created_by']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)  # catch user
        super().__init__(*args, **kwargs)

        if not user.is_accountant:
            # hide the field if not accountant
            self.fields['created_by'].widget = forms.HiddenInput()
            self.fields['created_by'].required = False


class QuotationItemForm(forms.ModelForm):
    class Meta:
        model = QuotationItem
        fields = ['product', 'quantity','discount']
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)  # catch user
        super().__init__(*args, **kwargs)

        if not user.is_accountant:
            # hide the field if not accountant
            self.fields['discount'].widget = forms.HiddenInput()
            self.fields['discount'].required = False
from django.forms import modelformset_factory

QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    extra=1,  # Show at least 1 row initially
    can_delete=True
)
