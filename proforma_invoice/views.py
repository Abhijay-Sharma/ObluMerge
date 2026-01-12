from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem
from .forms import ProformaInvoiceForm, ProformaItemFormSet
from quotations.models import Customer
from inventory.models import Category, InventoryItem
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required


class CreateProformaInvoiceView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(
            queryset=ProformaInvoiceItem.objects.none(),
            user=request.user
        )

        customers = Customer.objects.all() if request.user.is_accountant else Customer.objects.filter(created_by=request.user)
        categories = Category.objects.all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
        })

    def post(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)
        formset = ProformaItemFormSet(request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        selected_customer = None
        customer_id = request.POST.get("customer")
        if customer_id:
            from quotations.models import Customer
            selected_customer = Customer.objects.filter(id=customer_id).first()
        if invoice_form.is_valid() and formset.is_valid():
            invoice = invoice_form.save(commit=False)
            invoice.courier_mode = request.POST.get("courier_mode", "surface")
            if not request.user.is_accountant:
                invoice.created_by = request.user.username
            invoice.save()

            for form in formset:
                if form.cleaned_data and form.cleaned_data.get("product"):
                    item = form.save(commit=False)
                    item.invoice = invoice
                    item.save()

            return redirect("proforma_detail", pk=invoice.pk)

        customers = Customer.objects.all() if request.user.is_accountant else Customer.objects.filter(created_by=request.user)
        categories = Category.objects.all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "selected_customer": selected_customer,
        })


class ProformaInvoiceDetailView(View):
    """
    Shows the details of a created Proforma Invoice,
    including customer, items, and total.
    """
    def get(self, request, pk):
        invoice = get_object_or_404(ProformaInvoice, pk=pk)
        items = invoice.items.select_related("product")
        return render(request, "proforma_invoice/proforma_detail.html", {
            "invoice": invoice,
            "items": items,
        })

def get_inventory_by_category(request):
    category_id = request.GET.get("category_id")

    # ✅ Fetch only InventoryItems in this category that have a ProductPrice entry
    items = (
        InventoryItem.objects
        .filter(category_id=category_id, proforma_price__isnull=False)
        .select_related("proforma_price")
        .values("id", "name")
    )

    return JsonResponse({"products": list(items)})


@login_required
def home(request):
    return render(request, 'proforma_invoice/home.html')