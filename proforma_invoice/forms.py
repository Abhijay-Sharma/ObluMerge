from django import forms
from django.forms import modelformset_factory, BaseModelFormSet
from .models import ProformaInvoice, ProformaInvoiceItem
from quotations.models import Customer
from inventory.models import InventoryItem


class ProformaInvoiceForm(forms.ModelForm):
    class Meta:
        model = ProformaInvoice
        fields = ['customer', 'created_by']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if not user.is_accountant:
            self.fields['created_by'].widget = forms.HiddenInput()
            self.fields['created_by'].required = False


class ProformaInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = ProformaInvoiceItem
        fields = ['product', 'quantity']
        widgets = {
            "product": forms.HiddenInput(),
        }

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        quantity = cleaned_data.get("quantity")

        if product:
            # ✅ Check minimum requirement
            min_req = getattr(product, "min_quantity", 0)
            if quantity < min_req:
                raise forms.ValidationError(
                    f"Quantity for {product.name} cannot be less than the minimum requirement ({min_req})."
                )

            # ✅ Check stock availability
            available = getattr(product, "quantity", 0)
            if quantity > available:
                raise forms.ValidationError(
                    f"Only {available} units available in stock for {product.name}."
                )

        return cleaned_data


class BaseProformaItemFormSet(BaseModelFormSet):
    """
    Custom FormSet that safely injects the user object into each form.
    """

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        # only inject 'user' if form accepts it
        if 'user' in self.form.__init__.__code__.co_varnames:
            kwargs["user"] = self.user
        return super()._construct_form(i, **kwargs)

ProformaItemFormSet = modelformset_factory(
    ProformaInvoiceItem,
    form=ProformaInvoiceItemForm,
    formset=BaseProformaItemFormSet,
    extra=1,
    can_delete=True
)
