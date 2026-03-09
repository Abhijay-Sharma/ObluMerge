from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem , ProformaPriceChangeRequest
from .forms import ProformaInvoiceForm, ProformaItemFormSet, ProformaPriceChangeRequestForm
from quotations.models import Customer
from inventory.models import Category, InventoryItem
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView,DetailView
from django.contrib.auth import get_user_model
from .models import ProductPrice, ProductPriceTier
from django.db.models import Prefetch
from django.conf import settings
import os
from django.views.generic import FormView
from django.contrib import messages
from django.urls import reverse
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from inventory.mixins import AccountantRequiredMixin
from django.utils import timezone
from decimal import Decimal
from decimal import Decimal, ROUND_HALF_UP
from num2words import num2words

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
            "signature_base64": signature_base64,
        })

class ProformaInvoiceDetailView(DetailView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_detail.html"
    context_object_name = "invoice"


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        # ---- load signature base64 from file ----
        signature_path = os.path.join(
            settings.BASE_DIR,
            "proforma_invoice",
            "assets",
            "sujal_signature_base64.txt",
        )
        with open(signature_path, "r") as f:
            signature_base64 = f.read().strip()

        context["signature_base64"] = signature_base64

        items_qs = invoice.items.select_related("product")
        context["items"] = items_qs

        altered_prices = {}
        approved_request = None

        if invoice.is_price_altered:
            approved_request = (
                ProformaPriceChangeRequest.objects
                .filter(invoice=invoice, status="approved")
                .order_by("-id")
                .first()
            )

            if approved_request:
                altered_prices = approved_request.requested_product_prices or {}

        context["altered_prices"] = altered_prices

        if altered_prices:
            self.template_name = "proforma_invoice/proforma_detail_altered.html"

        # =========================
        # 🔥 RECALCULATION LOGIC
        # =========================
        # =========================
        # 🔥 RECALCULATION LOGIC
        # =========================

        recalculated_items = []

        subtotal_excl = Decimal("0.00")
        subtotal_incl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            if str(item.id) in altered_prices:
                unit_price_incl = Decimal(str(altered_prices[str(item.id)]))
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

            if gst_rate > 0:
                divisor = Decimal("1.00") + (gst_rate / Decimal("100"))
                unit_price_excl = (unit_price_incl / divisor).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                unit_price_excl = unit_price_incl

            taxable_value = (unit_price_excl * qty).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            amount_incl = (unit_price_incl * qty).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            product_gst = (amount_incl - taxable_value).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            # 🔥 ADD TOTALS HERE
            subtotal_excl += taxable_value
            subtotal_incl += amount_incl
            total_product_gst += product_gst

            recalculated_items.append({
                "item": item,
                "unit_price_incl": unit_price_incl,
                "unit_price_excl": unit_price_excl,
                "taxable_value": taxable_value,
                "amount_incl": amount_incl,
                "gst_amount": product_gst,
                "gst_rate": gst_rate,
            })

        # =========================
        # 🔥 Courier Calculation (Dynamic GST)
        # =========================

        if approved_request and approved_request.requested_courier_charge:
            courier_charge = Decimal(str(approved_request.requested_courier_charge))
        else:
            courier_charge = Decimal(str(invoice.courier_charge() or 0))

        courier_gst_rate = Decimal("18")

        courier_gst = (courier_charge * courier_gst_rate / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        total_igst = total_product_gst + courier_gst

        grand_total = (subtotal_excl + courier_charge + total_igst).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        amount_in_words = (
                num2words(grand_total, lang="en_IN").title() + " Rupees"
        )
        context["recalculated_items"] = recalculated_items

        context.update({
            "recalculated_subtotal": subtotal_excl,
            "recalculated_igst": total_igst,
            "recalculated_grand_total": grand_total,
            "approved_request": approved_request,
            "amount_in_words": amount_in_words,
        })

        return context

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

class ProformaPriceChangeRequestCreateView(LoginRequiredMixin, FormView):
    template_name = "proforma_invoice/request_price_change.html"
    form_class = ProformaPriceChangeRequestForm

    def dispatch(self, request, *args, **kwargs):
        """
        Only allow non-accountants to request price changes.
        """
        invoice_id = self.kwargs["invoice_id"]
        self.invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        if request.user.is_superuser:
            messages.error(request, "Super users cannot request price changes.")
            return redirect("proforma_detail", pk=self.invoice.id)

        if ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            status="pending"
        ).exists():
            messages.warning(request, "There is already a pending request for this Proforma Invoice.")
            return redirect("proforma_detail", pk=self.invoice.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """
        Add invoice items to template context.
        """
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice
        context["items"] = self.invoice.items.select_related("product")
        return context

    def form_valid(self, form):
        """
        Save the request with new product prices + courier charge.
        """
        items = self.invoice.items.select_related("product")

        # 🔥 Collect requested product prices
        requested_product_prices = {
            str(item.id): self.request.POST.get(f"new_price_{item.id}")
            for item in items
            if self.request.POST.get(f"new_price_{item.id}")
        }

        # 🔥 Courier charge override
        requested_courier_charge = self.request.POST.get("new_courier_charge")

        price_request = form.save(commit=False)
        price_request.invoice = self.invoice
        price_request.requested_by = self.request.user
        price_request.requested_product_prices = requested_product_prices

        if requested_courier_charge:
            price_request.requested_courier_charge = requested_courier_charge

        price_request.save()

        # ---------------- EMAIL NOTIFICATION ----------------
        to_emails = [
            "abhijay.obluhc@gmail.com",
            "swasti.obluhc@gmail.com",
            "nitin.a@obluhc.com"
        ]

        email_context = {
            "request_obj": price_request,
            "invoice": self.invoice,
            "requested_by": self.request.user,
            "requested_product_prices": requested_product_prices,
            "requested_courier_charge": requested_courier_charge,
            "review_url":"https://oblutools.com/proforma/price-change-requests/"
        }

        html_content = render_to_string(
            "proforma_invoice/price_change_request_email.html",
            email_context
        )

        subject = f"🔔 Price Change Request Submitted (Proforma #{self.invoice.id})"
        from_email = "proforma@oblutools.com"

        msg = EmailMultiAlternatives(subject, "", from_email, to_emails)
        msg.attach_alternative(html_content, "text/html")
        msg.send()
        # ----------------------------------------------------

        messages.success(self.request, "Your price change request has been submitted for review.")
        return redirect("proforma_detail", pk=self.invoice.id)


# View for accountants to list all Proforma price change requests
# ----------------------------------------------------------

class ProformaPriceChangeRequestListView(AccountantRequiredMixin, ListView):
    model = ProformaPriceChangeRequest
    template_name = "proforma_invoice/price_change_request_list.html"
    context_object_name = "requests"
    ordering = ["-created_at"]

    def get_queryset(self):
        """
        Show all proforma price change requests with related
        invoice and user information.
        """
        return ProformaPriceChangeRequest.objects.select_related(
            "invoice",        # FK to ProformaInvoice
            "requested_by",
            "reviewed_by"
        ).prefetch_related(
            "invoice__items__product"
        )


class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(
            ProformaPriceChangeRequest,
            id=kwargs["pk"],
            status="pending"
        )

        invoice = price_request.invoice

        # 🔥 Update product prices
        if price_request.requested_product_prices:
            for item_id, new_price in price_request.requested_product_prices.items():
                try:
                    item = invoice.items.get(id=item_id)
                    item.custom_price = float(new_price)  # assuming you use custom_price
                    item.save()
                except:
                    continue

        # 🔥 Update courier charge
        if price_request.requested_courier_charge is not None:
            invoice.courier_charge = price_request.requested_courier_charge
            invoice.save()

        # ✅ Mark request approved
        price_request.status = "approved"
        price_request.reviewed_by = request.user
        price_request.reviewed_at = timezone.now()
        price_request.save()

        # Optional flag if you have it
        invoice.is_price_altered = True
        invoice.save()

        # messages.success(
        #     request,
        #     f"Proforma Invoice #{invoice.id} prices updated successfully."
        # )

        return redirect("proforma_price_change_requests")

class ProformaPriceChangeRequestRejectView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(
            ProformaPriceChangeRequest,
            id=kwargs["pk"],
            status="pending"
        )

        price_request.status = "rejected"
        price_request.reviewed_by = request.user
        price_request.reviewed_at = timezone.now()
        price_request.save()

        messages.info(
            request,
            f"Request #{price_request.id} has been rejected."
        )

        return redirect("proforma_price_change_requests")
