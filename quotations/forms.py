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
            # instead of hiding, drop it completely
            self.fields.pop('created_by')


class QuotationItemForm(forms.ModelForm):
    class Meta:
        model = QuotationItem
        fields = ['product', 'quantity','discount']
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)  # catch user
        super().__init__(*args, **kwargs)

        if self.user and not self.user.is_accountant:
            # hide the field if not accountant
            self.fields['discount'].widget = forms.HiddenInput()

from django.forms import modelformset_factory, BaseModelFormSet

class BaseQuotationItemFormSet(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)  # ✅ pop user safely
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        # inject user into each form
        kwargs["user"] = self.user
        return super()._construct_form(i, **kwargs)



QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    formset=BaseQuotationItemFormSet,
    extra=1,
    can_delete=True
)
