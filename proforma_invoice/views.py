from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem , ProformaPriceChangeRequest
from .forms import ProformaInvoiceForm, ProformaItemFormSet, ProformaPriceChangeRequestForm
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
from customer_dashboard.models import SalesPerson, Customer
from django.core.exceptions import PermissionDenied

class CreateProformaInvoiceViewLegacy(LoginRequiredMixin, View):
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

class CreateProformaInvoiceView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(
            queryset=ProformaInvoiceItem.objects.none(),
            user=request.user
        )
        # Determine which customers to show in dropdown
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp=request.user.salesperson_profile.first()
            customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
            # customers = Customer.objects.filter(salesperson=request.user.salesperson_profile)
        else:
            # Normal user: only customers for which this user has created invoices
            customers = Customer.objects.filter(proforma_invoices__created_by=request.user.username).distinct()

        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })


    def post(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)
        formset = ProformaItemFormSet(
            request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user
        )

        # ---------- Get customer IDs from POST ----------
        customer_id = request.POST.get("customer")
        shipping_customer_id = request.POST.get("shipping_customer")

        # ---------- Fetch Customer objects ----------
        selected_customer = (
            Customer.objects.filter(id=customer_id).first()
            if customer_id and customer_id.isdigit()
            else None
        )
        shipping_customer = (
            Customer.objects.filter(id=shipping_customer_id).first()
            if shipping_customer_id and shipping_customer_id.isdigit()
            else None
        )

        # If no shipping customer, default to billing customer
        if not shipping_customer:
            shipping_customer = selected_customer

        # ---------- Access control ----------
        if selected_customer:
            # Accountant can access any customer
            if request.user.is_accountant:
                pass
            # Salesperson: can only access their own customers
            elif hasattr(request.user, "salesperson_profile"):
                sp =request.user.salesperson_profile.first()
                if sp and selected_customer.salesperson_id != sp.id:
                    raise PermissionDenied("Cannot create invoice for this customer")
            # Normal user: can only use customers for which they already created invoices
            else:
                if not ProformaInvoice.objects.filter(
                        customer=selected_customer,
                        created_by=request.user.username
                ).exists():
                    raise PermissionDenied("Cannot create invoice for this customer")
        else:
            invoice_form.add_error(None, "Please select a valid customer.")
            # Prepare customer list for re-render
            if request.user.is_accountant:
                customers = Customer.objects.all()
            elif hasattr(request.user, "salesperson_profile") and request.user.salesperson_profile:
                customers = Customer.objects.filter(salesperson=request.user.salesperson_profile)
            else:
                customers = Customer.objects.filter(
                    proforma_invoices__created_by=request.user.username
                ).distinct()
            categories = Category.objects.all().order_by("name")
            return render(
                request,
                "proforma_invoice/create_proforma.html",
                {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "selected_customer": selected_customer,
                },
            )

        # ---------- Save invoice ----------
        if invoice_form.is_valid() and formset.is_valid():
            invoice = invoice_form.save(commit=False)
            invoice.customer = selected_customer
            invoice.shipping_customer = shipping_customer
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

        # ---------- Re-render if form invalid ----------
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile") and request.user.salesperson_profile:
            customers = Customer.objects.filter(salesperson=request.user.salesperson_profile)
        else:
            customers = Customer.objects.filter(
                id__in=ProformaInvoice.objects.filter(
                    created_by=request.user.username
                ).values_list("customer_id", flat=True)
            )
        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(
            request,
            "proforma_invoice/create_proforma.html",
            {
                "invoice_form": invoice_form,
                "formset": formset,
                "customers": customers,
                "categories": categories,
                "selected_customer": selected_customer,
                "items": items,
            },
        )


class CreateProformaInvoiceView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(
            queryset=ProformaInvoiceItem.objects.none(),
            user=request.user
        )
        # Determine which customers to show in dropdown
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp=request.user.salesperson_profile.first()
            customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
            # customers = Customer.objects.filter(salesperson=request.user.salesperson_profile)
        else:
            # Normal user: only customers for which this user has created invoices
            customers = Customer.objects.filter(proforma_invoices__created_by=request.user.username).distinct()

        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })


    def post(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)
        formset = ProformaItemFormSet(
            request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user
        )

        # ---------- Get customer IDs from POST ----------
        customer_id = request.POST.get("customer")
        shipping_customer_id = request.POST.get("shipping_customer")

        # ---------- Fetch Customer objects ----------
        selected_customer = (
            Customer.objects.filter(id=customer_id).first()
            if customer_id and customer_id.isdigit()
            else None
        )
        shipping_customer = (
            Customer.objects.filter(id=shipping_customer_id).first()
            if shipping_customer_id and shipping_customer_id.isdigit()
            else None
        )

        # If no shipping customer, default to billing customer
        if not shipping_customer:
            shipping_customer = selected_customer

        # ---------- Access control ----------
        if selected_customer:
            # Accountant can access any customer
            if request.user.is_accountant:
                pass
            # Salesperson: can only access their own customers
            elif hasattr(request.user, "salesperson_profile"):
                sp =request.user.salesperson_profile.first()
                if sp and selected_customer.salesperson_id != sp.id:
                    raise PermissionDenied("Cannot create invoice for this customer")
            # Normal user: can only use customers for which they already created invoices
            else:
                if not ProformaInvoice.objects.filter(
                        customer=selected_customer,
                        created_by=request.user.username
                ).exists():
                    raise PermissionDenied("Cannot create invoice for this customer")
        else:
            invoice_form.add_error(None, "Please select a valid customer.")
            # Prepare customer list for re-render
            if request.user.is_accountant:
                customers = Customer.objects.all()
            elif hasattr(request.user, "salesperson_profile"):
                sp = request.user.salesperson_profile.first()
                customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
            else:
                customers = Customer.objects.filter(
                    proforma_invoices__created_by=request.user.username
                ).distinct()
            categories = Category.objects.all().order_by("name")
            return render(
                request,
                "proforma_invoice/create_proforma.html",
                {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "selected_customer": selected_customer,
                },
            )

        # ---------- Save invoice ----------
        # ---------- Save invoice ----------
        if invoice_form.is_valid() and formset.is_valid():

            # ✅ DEFINE FIRST
            valid_items = [
                form for form in formset
                if form.cleaned_data and form.cleaned_data.get("product")
            ]

            # ❌ STOP if no products
            if not valid_items:
                invoice_form.add_error(None, "❌ Please add at least one product.")

                if request.user.is_accountant:
                    customers = Customer.objects.all()
                elif hasattr(request.user, "salesperson_profile"):
                    sp = request.user.salesperson_profile.first()
                    customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
                else:
                    customers = Customer.objects.filter(
                        proforma_invoices__created_by=request.user.username
                    ).distinct()

                categories = Category.objects.all().order_by("name")
                items = InventoryItem.objects.select_related("category").all().order_by("name")

                return render(request, "proforma_invoice/create_proforma.html", {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "items": items,
                    "selected_customer": selected_customer,
                })

            # ✅ SAVE ONLY AFTER VALIDATION
            invoice = invoice_form.save(commit=False)
            invoice.customer = selected_customer
            invoice.shipping_customer = shipping_customer
            invoice.courier_mode = request.POST.get("courier_mode", "surface")

            if not request.user.is_accountant:
                invoice.created_by = request.user.username

            invoice.save()

            # ✅ SAVE ITEMS
            for form in valid_items:
                item = form.save(commit=False)
                item.invoice = invoice
                item.save()

            return redirect("proforma_detail", pk=invoice.pk)

        # ---------- Re-render if form invalid ----------
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp = request.user.salesperson_profile.first()
            customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()

        else:
            customers = Customer.objects.filter(
                id__in=ProformaInvoice.objects.filter(
                    created_by=request.user.username
                ).values_list("customer_id", flat=True)
            )


        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
            "selected_customer": selected_customer,
        })


class CreateProformaInvoiceView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(
            queryset=ProformaInvoiceItem.objects.none(),
            user=request.user
        )
        # Determine which customers to show in dropdown
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp=request.user.salesperson_profile.first()
            customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
            # customers = Customer.objects.filter(salesperson=request.user.salesperson_profile)
        else:
            # Normal user: only customers for which this user has created invoices
            customers = Customer.objects.filter(proforma_invoices__created_by=request.user.username).distinct()

        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })


    def post(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)
        formset = ProformaItemFormSet(
            request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user
        )

        # ---------- Get customer IDs from POST ----------
        customer_id = request.POST.get("customer")
        shipping_customer_id = request.POST.get("shipping_customer")

        # ---------- Fetch Customer objects ----------
        selected_customer = (
            Customer.objects.filter(id=customer_id).first()
            if customer_id and customer_id.isdigit()
            else None
        )
        shipping_customer = (
            Customer.objects.filter(id=shipping_customer_id).first()
            if shipping_customer_id and shipping_customer_id.isdigit()
            else None
        )

        # If no shipping customer, default to billing customer
        if not shipping_customer:
            shipping_customer = selected_customer

        # ---------- Access control ----------
        if selected_customer:
            # Accountant can access any customer
            if request.user.is_accountant:
                pass
            # Salesperson: can only access their own customers
            elif hasattr(request.user, "salesperson_profile"):
                sp =request.user.salesperson_profile.first()
                if sp and selected_customer.salesperson_id != sp.id:
                    raise PermissionDenied("Cannot create invoice for this customer")
            # Normal user: can only use customers for which they already created invoices
            else:
                if not ProformaInvoice.objects.filter(
                        customer=selected_customer,
                        created_by=request.user.username
                ).exists():
                    raise PermissionDenied("Cannot create invoice for this customer")
        else:
            invoice_form.add_error(None, "Please select a valid customer.")
            # Prepare customer list for re-render
            if request.user.is_accountant:
                customers = Customer.objects.all()
            elif hasattr(request.user, "salesperson_profile"):
                sp = request.user.salesperson_profile.first()
                customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
            else:
                customers = Customer.objects.filter(
                    proforma_invoices__created_by=request.user.username
                ).distinct()
            categories = Category.objects.all().order_by("name")
            return render(
                request,
                "proforma_invoice/create_proforma.html",
                {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "selected_customer": selected_customer,
                },
            )

        # ---------- Save invoice ----------
        # ---------- Save invoice ----------
        if invoice_form.is_valid() and formset.is_valid():

            # ✅ DEFINE FIRST
            valid_items = [
                form for form in formset
                if form.cleaned_data and form.cleaned_data.get("product")
            ]

            # ❌ STOP if no products
            if not valid_items:
                invoice_form.add_error(None, "❌ Please add at least one product.")

                if request.user.is_accountant:
                    customers = Customer.objects.all()
                elif hasattr(request.user, "salesperson_profile"):
                    sp = request.user.salesperson_profile.first()
                    customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
                else:
                    customers = Customer.objects.filter(
                        proforma_invoices__created_by=request.user.username
                    ).distinct()

                categories = Category.objects.all().order_by("name")
                items = InventoryItem.objects.select_related("category").all().order_by("name")

                return render(request, "proforma_invoice/create_proforma.html", {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "items": items,
                    "selected_customer": selected_customer,
                })

            # ================= SURFACE VALIDATION =================
            courier_mode = request.POST.get("courier_mode", "surface")

            total_qty = sum(
                form.cleaned_data.get("quantity", 0)
                for form in valid_items
            )

            if courier_mode == "surface" and total_qty < 200:

                invoice_form.add_error(
                    None,
                    f"❌ Total quantity is {total_qty}. Sheets below 200 cannot be sent via Surface."
                )

                if request.user.is_accountant:
                    customers = Customer.objects.all()
                elif hasattr(request.user, "salesperson_profile"):
                    sp = request.user.salesperson_profile.first()
                    customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
                else:
                    customers = Customer.objects.filter(
                        proforma_invoices__created_by=request.user.username
                    ).distinct()

                categories = Category.objects.all().order_by("name")
                items = InventoryItem.objects.select_related("category").all().order_by("name")

                return render(request, "proforma_invoice/create_proforma.html", {
                    "invoice_form": invoice_form,
                    "formset": formset,
                    "customers": customers,
                    "categories": categories,
                    "items": items,
                    "selected_customer": selected_customer,
                })


            # ✅ SAVE ONLY AFTER VALIDATION
            invoice = invoice_form.save(commit=False)
            invoice.customer = selected_customer
            invoice.shipping_customer = shipping_customer
            invoice.courier_mode = request.POST.get("courier_mode", "surface")

            if not request.user.is_accountant:
                invoice.created_by = request.user.username

            invoice.save()

            # ✅ SAVE ITEMS
            for form in valid_items:
                item = form.save(commit=False)
                item.invoice = invoice
                item.save()

            return redirect("proforma_detail", pk=invoice.pk)

        # ---------- Re-render if form invalid ----------
        if request.user.is_accountant:
            customers = Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp = request.user.salesperson_profile.first()
            customers = Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()

        else:
            customers = Customer.objects.filter(
                id__in=ProformaInvoice.objects.filter(
                    created_by=request.user.username
                ).values_list("customer_id", flat=True)
            )


        categories = Category.objects.all().order_by("name")
        items = InventoryItem.objects.select_related("category").all().order_by("name")

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
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

class ProformaInvoiceDetailView(LoginRequiredMixin, DetailView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_detail.html"
    context_object_name = "invoice"

    from decimal import Decimal, ROUND_HALF_UP

    from decimal import Decimal, ROUND_HALF_UP

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object

        # =========================
        # 🔹 Load Signature
        # =========================
        signature_path = os.path.join(
            settings.BASE_DIR,
            "proforma_invoice",
            "assets",
            "sujal_signature_base64.txt",
        )
        with open(signature_path, "r") as f:
            context["signature_base64"] = f.read().strip()

        items_qs = invoice.items.select_related("product")
        context["items"] = items_qs

        # =========================
        # 🔹 Price Alteration
        # =========================
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
        # 🔹 Product Calculation
        # =========================
        recalculated_items = []
        subtotal_excl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            # Price altered or normal
            if str(item.id) in altered_prices:
                unit_price_incl = Decimal(str(altered_prices[str(item.id)]))
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

            # Rate excl rounded 2 decimals
            divisor = Decimal("1.00") + (gst_rate / Decimal("100"))
            unit_price_excl = (unit_price_incl / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Taxable value
            taxable_value = (unit_price_excl * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # GST per product (rounded 2 decimals)
            product_gst = (taxable_value * gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Amount incl = taxable + gst
            amount_incl = (taxable_value + product_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            subtotal_excl += taxable_value
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
        # 🔹 Courier Charges + GST (Tally-style)
        # =========================
        # if approved_request and approved_request.requested_courier_charge:
        if approved_request and approved_request.requested_courier_charge is not None:
            courier_charge = Decimal(str(approved_request.requested_courier_charge))
        else:
            courier_charge = Decimal(str(invoice.courier_charge() or 0))

        # Combined GST % (Tally-style)
        if subtotal_excl > 0:
            combined_gst_rate = (total_product_gst / subtotal_excl * Decimal("100")).quantize(Decimal("0.01"),
                                                                                              rounding=ROUND_HALF_UP)
        else:
            combined_gst_rate = Decimal("0.00")

        # Courier GST using combined rate
        courier_gst = (courier_charge * combined_gst_rate / Decimal("100")).quantize(Decimal("0.01"),
                                                                                     rounding=ROUND_HALF_UP)

        total_gst = (total_product_gst + courier_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # =========================
        # 🔹 Grand Total
        # =========================
        gross_total = (subtotal_excl + courier_charge + total_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Tally-style rounding to nearest ₹1
        rounded_total = gross_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = (rounded_total - gross_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_total = rounded_total

        # =========================
        # 🔹 GST Split
        # =========================
        if invoice.is_intra_state():
            cgst = (total_gst / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            utgst = total_gst - cgst
            igst = Decimal("0.00")
        else:
            igst = total_gst
            cgst = Decimal("0.00")
            utgst = Decimal("0.00")

        # Amount in words
            final_total = rounded_total
        amount_in_words = num2words(final_total , lang="en_IN").title() + " Rupees Only"
        # amount_in_words = num2words(final_total, lang="en_IN").title() + " Rupees Only"

        # =========================
        # 🔹 Context Update
        # =========================
        context.update({
            "recalculated_items": recalculated_items,
            "recalculated_subtotal": subtotal_excl,
            "courier_charge": courier_charge,
            "combined_gst_rate": combined_gst_rate,
            "igst": igst,
            "cgst": cgst,
            "utgst": utgst,
            "total_gst": total_gst,
            "gross_total": gross_total,
            "round_off": round_off,
            "recalculated_grand_total": final_total,
            "amount_in_words": amount_in_words,
            "gst_type": invoice.gst_type(),
            "approved_request" : approved_request,
            "recalculated_igst": total_gst,

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
        # Build invoice URL properly
        invoice_url = request.build_absolute_uri(
            reverse("proforma_detail", kwargs={"pk": invoice.id})
        )

        # ---------------- EMAIL NOTIFICATION (APPROVED) ----------------

        to_email = [price_request.requested_by.email]

        cc_emails = [
            "abhijay.obluhc@gmail.com",
            "swasti.obluhc@gmail.com",
            "nitin.a@obluhc.com",
        ]

        # Add reviewer email dynamically
        if price_request.reviewed_by and price_request.reviewed_by.email:
            cc_emails.append(price_request.reviewed_by.email)

        email_context = {
            "request_obj": price_request,
            "invoice": invoice,
            "user": price_request.requested_by,
            "status": "approved",
            "invoice_url": invoice_url,
        }

        html_content = render_to_string(
            "proforma_invoice/price_change_request_status_email.html",
            email_context
        )

        subject = f"✅ Price Change Approved (Proforma #{invoice.id})"
        from_email = "proforma@oblutools.com"

        msg = EmailMultiAlternatives(
            subject,
            "",
            from_email,
            to_email,
            cc=list(set(cc_emails))
        )

        msg.attach_alternative(html_content, "text/html")

        try:
            msg.send()
        except Exception as e:
            print("Email failed:", e)

        # --------------------------------------------------------------
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

        invoice = price_request.invoice

        invoice_url = request.build_absolute_uri(
            reverse("proforma_detail", kwargs={"pk": invoice.id})
        )

        # ---------------- EMAIL NOTIFICATION (REJECTED) ----------------

        to_email = [price_request.requested_by.email]

        cc_emails = [
            "abhijay.obluhc@gmail.com",
            "swasti.obluhc@gmail.com",
            "nitin.a@obluhc.com",
        ]

        if price_request.reviewed_by and price_request.reviewed_by.email:
            cc_emails.append(price_request.reviewed_by.email)

        email_context = {
            "request_obj": price_request,
            "invoice": invoice,
            "user": price_request.requested_by,
            "status": "rejected",
            "invoice_url": invoice_url,
        }

        html_content = render_to_string(
            "proforma_invoice/price_change_request_status_email.html",
            email_context
        )

        subject = f"❌ Price Change Rejected (Proforma #{invoice.id})"
        from_email = "proforma@oblutools.com"

        msg = EmailMultiAlternatives(
            subject,
            "",
            from_email,
            to_email,
            cc=list(set(cc_emails))
        )

        msg.attach_alternative(html_content, "text/html")

        try:
            msg.send()
        except Exception as e:
            print("Email failed:", e)

        return redirect("proforma_price_change_requests")
