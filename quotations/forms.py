# quotations/forms.py
from django import forms
from .models import Quotation, QuotationItem, Product, Customer


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
        self.user = kwargs.pop("user", None)  # catch user
        super().__init__(*args, **kwargs)

        # Group products by category
        categories = {}
        from .models import Product  # import here to avoid circular imports
        for product in Product.objects.select_related("category").all():
            cat = product.category.name if product.category else "Uncategorized"
            categories.setdefault(cat, []).append((product.id, product.name))

        choices = []
        for cat, items in categories.items():
            choices.append((cat, items))  # creates <optgroup>

        self.fields['product'].choices = choices
        self.fields['product'].widget.attrs.update({
            "class": "product-select"
        })

        if self.user and not self.user.is_accountant:
            # hide the field if not accountant
            self.fields['discount'].widget = forms.HiddenInput()
            self.fields['discount'].required = False
            self.fields['discount'].initial = 0

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
        fields = ['name', 'state','city','pin_code','phone','email', 'address']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].initial = None  # 👈 wipe any model default