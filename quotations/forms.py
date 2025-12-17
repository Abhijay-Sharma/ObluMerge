# quotations/forms.py
from django import forms
from .models import Quotation, QuotationItem, Product, Customer, ProductPriceTier , PriceChangeRequest
from django.forms import inlineformset_factory


class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = ['customer_name', 'customer_address', 'created_by','customer_state','customer_city','customer_pincode','customer_phone']

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
        widgets = {
            "product": forms.HiddenInput(),  #  hide real field, we’ll set via modal
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)  # catch user
        super().__init__(*args, **kwargs)

        if self.user and not self.user.is_accountant:
            # hide the field if not accountant
            self.fields['discount'].widget = forms.HiddenInput()
            self.fields['discount'].required = False
            self.fields['discount'].initial = 0

    #  Add server-side validation for checking prod min req
    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        product = self.cleaned_data.get('product')

        if product and quantity < product.min_requirement:
            raise forms.ValidationError(
                f"Quantity for {product.name} cannot be less than the minimum requirement ({product.min_requirement})."
            )
        return quantity

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



class CustomerCreateForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'state','city','pin_code','phone','email','company','address']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].initial = None  # 👈 wipe any model default


class ProductForm(forms.ModelForm):
    product_info_text = forms.CharField(
        label="Product Information (one point per line)",
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 5,
            "placeholder": "High precision scanning\n2 Years warranty\nInstallation included"
        })
    )

    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "price_per_unit",
            "tax_rate",
            "is_quantity_dependent",
            "min_requirement",
            "terms_and_conditions",
            "has_dynamic_pricing",
        ]
        widgets = {
            "terms_and_conditions": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # preload JSON into textarea
        if self.instance and self.instance.product_info:
            self.fields["product_info_text"].initial = "\n".join(
                self.instance.product_info
            )

    def save(self, commit=True):
        instance = super().save(commit=False)

        raw_text = self.cleaned_data.get("product_info_text", "")
        points = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip()
        ]

        instance.product_info = points if points else None

        if commit:
            instance.save()

        return instance


# Inline formset for dynamic pricing tiers
ProductPriceTierFormSet = inlineformset_factory(
    parent_model=Product,
    model=ProductPriceTier,
    fields=["min_quantity", "unit_price"],
    extra=1,         # always show 1 empty row for adding new tier
    can_delete=True  # allow deleting old tiers
)

class PriceChangeRequestForm(forms.ModelForm):
    """
    Used by normal users to request a price change for a quotation.
    """

    class Meta:
        model = PriceChangeRequest
        fields = ['reason']
        widgets = {
            'reason': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Explain why price change is needed...'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.quotation = kwargs.pop('quotation', None)
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)