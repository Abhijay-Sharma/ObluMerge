from django import forms
from django.forms import modelformset_factory, BaseModelFormSet
from .models import ProformaInvoice, ProformaInvoiceItem, ProformaPriceChangeRequest
from customer_dashboard.models import Customer, SalesPerson
from tally_voucher.models import Voucher, VoucherRow
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
            # available = getattr(product, "quantity", 0)
            # if quantity > available:
            #     raise forms.ValidationError(
            #         f"Only {available} units available in stock for {product.name}."
            #     )

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

class ProformaPriceChangeRequestForm(forms.ModelForm):
    """
    Used by normal users to request a price change
    for a Proforma Invoice.
    """

    class Meta:
        model = ProformaPriceChangeRequest
        fields = ["reason"]   # product + courier handled manually in view
        widgets = {
            "reason": forms.Textarea(attrs={
                "rows": 3,
                "class": "form-control",
                "placeholder": "Explain why price or courier charge change is needed..."
            }),
        }

    def __init__(self, *args, **kwargs):
        self.invoice = kwargs.pop("invoice", None)
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Optional: make reason required
        self.fields["reason"].required = True


class NewProformaCustomerForm(forms.ModelForm):
    # Compulsory
    name = forms.CharField(label="Company Name (Customer Name)", required=True)
    address = forms.CharField(label="Billing Address", widget=forms.Textarea(attrs={'rows': 2}), required=True)
    phone = forms.CharField(label="Phone No.", required=True)
    pincode = forms.CharField(label="Pincode", required=True)
    state = forms.CharField(label="State", required=True)

    # Defaults
    email = forms.CharField(required=False, label="Mail ID")
    dci_no = forms.CharField(initial="N/A", required=False, label="DCI No.")
    md42 = forms.CharField(initial="N/A", required=False, label="MD42")
    gst_number = forms.CharField(initial="N/A", required=False, label="GST No.")
    shipping_address = forms.CharField(label="Shipping Address", initial="N/A", required=False, widget=forms.Textarea(attrs={'rows': 2}))
    shipping_phone = forms.CharField(label="Shipping Phone", initial="N/A", required=False)
    shipping_email = forms.CharField(label="Shipping Mail ID", initial="N/A", required=False)
    shipping_pincode = forms.CharField(label="Shipping Pincode", initial="N/A", required=False)

    # Accountant Dropdown
    sp_assigned = forms.ModelChoiceField(
        queryset=SalesPerson.objects.all(),
        required=False,
        label="Assign Salesperson",
        empty_label="-- Select Salesperson --",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Customer
        fields = ['name', 'address', 'state', 'pincode', 'phone', 'email',
                  'shipping_address', 'shipping_phone', 'shipping_email',
                  'shipping_pincode', 'gst_number']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and not getattr(user, 'is_accountant', False):
            self.fields['sp_assigned'].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        phone = cleaned_data.get('phone')
        state = cleaned_data.get('state')

        if not name or not phone or not state:
            return cleaned_data

        # --- 1. TALLY CHECK ---
        if Voucher.objects.filter(party_name__iexact=name).exists():
            # 🔥 ATTACH ERROR TO THE NAME FIELD SPECIFICALLY
            self.add_error('name', f"❌ Error: '{name}' already has a Ledger/Voucher in Tally.")

        # --- 2. DASHBOARD UNIQUENESS CHECK ---
        existing_phone_state = Customer.objects.filter(phone=phone, state__iexact=state)

        if existing_phone_state.exists():
            match = existing_phone_state.first()

            if match.name.lower() != name.lower():
                self.add_error('phone', f"❌ This phone number is already registered to '{match.name}' in {state}.")
            else:
                # 🔥 ATTACH ERROR TO THE NAME FIELD SPECIFICALLY
                self.add_error('name', f"❌ Error: This exact customer ('{name}' in {state}) already exists.")

        return cleaned_data