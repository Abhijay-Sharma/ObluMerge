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
from django.views.generic import ListView
from django.contrib.auth import get_user_model
from .models import ProductPrice, ProductPriceTier
from django.db.models import Prefetch
from django.conf import settings
import os


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
        # ---- load signature base64 from file ----
        signature_path = os.path.join(
            settings.BASE_DIR,
            "proforma_invoice",
            "assets",
            "sujal_signature_base64.txt",
        )
        with open(signature_path, "r") as f:
            signature_base64 = f.read().strip()
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


class ProformaInvoiceListView(LoginRequiredMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list.html"
    context_object_name = "invoices"

    def get_queryset(self):
        user = self.request.user

        # -------------------------
        # ROLE BASED ACCESS
        # -------------------------
        if user.is_accountant:
            qs = ProformaInvoice.objects.select_related("customer").all()
        else:
            # Normal users see only their own invoices
            qs = ProformaInvoice.objects.select_related("customer").filter(
                created_by=user.username
            )

        # -------------------------
        # FILTERS
        # -------------------------
        created_by = self.request.GET.get("created_by")
        customer = self.request.GET.get("customer")
        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")
        sort_by = self.request.GET.get("sort_by")

        if created_by:
            qs = qs.filter(created_by=created_by)

        if customer:
            qs = qs.filter(customer__id=customer)

        if start_date and end_date:
            qs = qs.filter(date_created__date__range=[start_date, end_date])

        # -------------------------
        # SORTING
        # -------------------------
        if sort_by == "date_desc":
            qs = qs.order_by("-date_created")
        elif sort_by == "date_asc":
            qs = qs.order_by("date_created")
        elif sort_by == "customer":
            qs = qs.order_by("customer__name")

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        User = get_user_model()

        # For filters dropdowns
        ctx["users"] = (
            User.objects.filter(is_active=True)
            if self.request.user.is_accountant
            else []
        )

        ctx["customers"] = (
            ProformaInvoice.objects
            .select_related("customer")
            .values("customer__id", "customer__name")
            .distinct()
        )

        return ctx




class ProformaProductListView(LoginRequiredMixin, ListView):
    model = ProductPrice
    template_name = "proforma_invoice/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        qs = (
            ProductPrice.objects
            .select_related("product")
            .prefetch_related(
                Prefetch(
                    "price_tiers",
                    queryset=ProductPriceTier.objects.order_by("min_quantity")
                )
            )
            .order_by("product__name")
        )

        return qs