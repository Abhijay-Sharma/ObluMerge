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
from django.forms import modelformset_factory, BaseModelFormSet

class BaseQuotationItemFormSet(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)   # catch user here
        super().__init__(*args, **kwargs)

    def _construct_forms(self):
        # inject user into each form
        self.forms = []
        for i in range(self.total_form_count()):
            self.forms.append(self._construct_form(i, user=self.user))


QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    formset=BaseQuotationItemFormSet,
    extra=1,
    can_delete=True
)
