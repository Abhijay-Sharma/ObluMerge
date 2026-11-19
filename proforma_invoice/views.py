from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem , ProformaPriceChangeRequest,ProformaStockShortageRequest,ProformaRemark, CourierMode, CourierCharge,QuotationMakerItem,DocumentEditLog
from .models import ApprovedPriceMemory, ProformaPriceChangeRequest, CreditPeriodOverdueByPassRequest # Ensure these are imported

from .forms import ProformaInvoiceForm, ProformaItemFormSet, ProformaPriceChangeRequestForm,NewProformaCustomerForm
from datetime import timedelta
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
from decimal import Decimal, InvalidOperation
from decimal import Decimal, ROUND_HALF_UP
from num2words import num2words
from customer_dashboard.models import SalesPerson, Customer
from django.core.exceptions import PermissionDenied
import json
from django.views.generic import TemplateView
from django.views import View
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from decimal import Decimal
from .models import CourierChargeTier
from django.db import transaction
from django.views.generic.edit import CreateView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView
from django.views import View
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.contrib import messages
from customer_dashboard.models import CustomerVoucherStatus
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
import logging
from collections import defaultdict
from tally_voucher.models import VoucherEmiPaymentAllocation
from .forms import ProformaInvoiceForm, ProformaItemFormSet, ProformaPriceChangeRequestForm,NewProformaCustomerForm,QuotationMakerForm,QuotationMakerItemFormSet,QuotationMakerItemForm
from django.http import Http404
from access_control.mixins import RBACViewMixin
from access_control.services import can, scope_queryset, get_user_effective_permissions

logger = logging.getLogger(__name__)


DISABLED_PROFORMA_PRODUCT_IDS = [
2708,2709,2722,2727,2728,2729,2730,2763,2769,2782,
2787,2797,2803,2805,2821,2824,2835,2837,2838,2841,
2842,2843,2844,2851,2855,2859,2860,2862,2871,2872,
2874,2882,2884,2887,2888,2896,2909,2916,2932,
2933,2943,2956,2957,2958,2961,2963,2964,2965,2966,
2974,2980,2981,2982,2984,2985,2986,2987,2989,2998,
3016,3030,3031,3075,3078,3079,3080,3087,3088,
3090,3104,3127,3131,3132,3133,3134,3135,3140,3141,
3149,3160,3161,3162,3163,3165,3170,3174,3175,3181,
3241,3242,3243,3244,3245,3246,3266,3268,3295,3307,
3308,3309,3310,3311,3312,3313,3314,3315,3316,3317,
2712, 2796, 2810, 2833, 2901, 2908, 2950, 3000,
3001, 3035, 3065, 3066, 3070, 3094, 3099, 3102,
3154, 3157, 3167, 3168, 3171, 3182, 3221, 3233, 3254,
3259, 3261, 3265, 3274, 3275, 3281, 3302, 3303, 3304, 3319,
3182,2997,

]


# ---5th ✅
from django.db import transaction
from django.shortcuts import render, redirect
from django.contrib import messages
from decimal import Decimal

#Legacy view now

#API
@login_required
def customer_purchase_history_api(request, customer_id):
    # This looks through previous Proforma Items for this customer
    purchased_product_ids = ProformaInvoiceItem.objects.filter(
        invoice__customer_id=customer_id
    ).values_list('product_id', flat=True).distinct()

    return JsonResponse({
        'purchased_ids': list(purchased_product_ids)
    })

@login_required
def check_customer_credit_api(request, customer_id):
    """
    Checks if a customer is blocked based on the CustomerVoucherStatus model.
    No more hardcoded 30-day math.
    """
    from customer_dashboard.models import CustomerVoucherStatus  # Ensure import

    # 1. Directly find records where credit period is crossed and money is still owed
    overdue_records = CustomerVoucherStatus.objects.filter(
        customer_id=customer_id,
        is_credit_period_crossed=True
    ).filter(
        Q(is_unpaid=True) | Q(is_partially_paid=True)
    ).select_related('voucher')

    is_blocked = overdue_records.exists()

    overdue_list = []
    for rec in overdue_records:
        overdue_list.append({
            "pi_id": rec.voucher.voucher_number,
            "date": rec.voucher_date.strftime('%d-%m-%Y'),
        })

    return JsonResponse({
        "is_blocked": is_blocked,
        "overdue_invoices": overdue_list,
        "customer_name": overdue_records.first().customer.name if is_blocked else ""
    })

@login_required
def notify_admin_credit_api(request, customer_id):
    """
    Triggered when the user clicks 'Notify Admin' on the frontend.
    Since no new model is created, this serves as a hook for notifications.
    """
    if request.method == "POST":
        customer = get_object_or_404(Customer, id=customer_id)

        # LOGIC: You can uncomment the code below to send actual emails
        # from django.core.mail import send_mail
        # send_mail(
        #     subject=f"🚨 Credit Approval Required: {customer.name}",
        #     message=f"User {request.user.username} is requesting to create a Proforma for {customer.name}.\n\n"
        #             f"Reason: Customer has overdue invoices in Tally.\n"
        #             f"Action: Please review and clear the credit block in Tally.",
        #     from_email="system@oblutools.com",
        #     recipient_list=["admin@oblutools.com", "accounts@oblutools.com"]
        # )

        return JsonResponse({
            "status": "success",
            "message": f"Admin notified regarding {customer.name}."
        })


def check_customer_credit_api(request, customer_id):
    from customer_dashboard.models import CustomerVoucherStatus

    # 1. Get all overdue/unpaid records
    all_overdue = CustomerVoucherStatus.objects.filter(
        customer_id=customer_id,
        is_credit_period_crossed=True
    ).filter(
        Q(is_unpaid=True) | Q(is_partially_paid=True)
    ).select_related('voucher')

    # 2. Identify which ones have EMI running
    overdue_ids = all_overdue.values_list('voucher_id', flat=True)
    vouchers_with_emi = VoucherEmiPaymentAllocation.objects.filter(
        voucher__voucher_id__in=overdue_ids
    ).values_list('voucher__voucher_id', flat=True).distinct()

    # 3. Separate them
    # Hard Block: Overdue and NO EMI
    hard_overdue = all_overdue.exclude(voucher_id__in=vouchers_with_emi)
    # Bypassed: Overdue but HAS EMI
    emi_covered = all_overdue.filter(voucher_id__in=vouchers_with_emi)

    # 4. Determine blocking status
    is_blocked = hard_overdue.exists()

    return JsonResponse({
        "is_blocked": is_blocked,
        "overdue_invoices": [
            {"pi_id": rec.voucher.voucher_number, "date": rec.voucher_date.strftime('%d-%m-%Y')}
            for rec in hard_overdue
        ],
        "emi_covered_invoices": [
            {"pi_id": rec.voucher.voucher_number, "date": rec.voucher_date.strftime('%d-%m-%Y')}
            for rec in emi_covered
        ],
    })


class CreateProformaInvoiceView(RBACViewMixin, View):
    rbac_permission = "proforma.create"

    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")

        items = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related("proforma_price__price_tiers", "courier_sheets")
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
            .order_by("name")
        )

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })

    def check_is_permitted(self, customer, product, requested_price, current_recommended):
        memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()
        if memory:
            if memory.base_price_at_approval == current_recommended:
                if requested_price >= memory.min_approved_price:
                    return True
        return False

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "save")
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)

        if 'created_by' in invoice_form.fields:
            invoice_form.fields['created_by'].required = False

        formset = ProformaItemFormSet(request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        customer_id = request.POST.get("customer", "")
        selected_customer = Customer.objects.filter(id=customer_id).first() if customer_id.isdigit() else None
        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(
            id=shipping_id).first() if shipping_id.isdigit() else selected_customer

        if not selected_customer:
            invoice_form.add_error(None, "Please select a valid customer.")
            return self._render_error(request, invoice_form, formset, selected_customer)

        # Security check: User must be authorized to access this customer
        if not can(request.user, "customer.view", selected_customer):
            invoice_form.add_error(None, "You do not have permission to create an invoice for this customer.")
            return self._render_error(request, invoice_form, formset, selected_customer)

        if invoice_form.is_valid() and formset.is_valid():
            valid_forms = [f for f in formset if f.cleaned_data and f.cleaned_data.get("product")]

            if not valid_forms:
                invoice_form.add_error(None, "❌ Please add at least one product.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            courier_mode = request.POST.get("courier_mode", "surface")
            RESTRICTED_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]
            restricted_qty = 0
            has_resin = False
            has_stock_issue = False
            shortage_details = []

            for f in valid_forms:
                p = f.cleaned_data['product']
                qty = f.cleaned_data['quantity']

                pricing_config = ProductPrice.objects.filter(product=p).first()
                if pricing_config:
                    min_required = pricing_config.min_requirement
                    if qty < min_required:
                        invoice_form.add_error(None, f"❌ '{p.name}' requires a minimum quantity of {min_required}.")
                        return self._render_error(request, invoice_form, formset, selected_customer)

                cat_name = p.category.name.upper() if p.category else ""
                if cat_name in RESTRICTED_CATEGORIES:
                    restricted_qty += qty
                if "RESIN" in cat_name:
                    has_resin = True

                fresh_product = InventoryItem.objects.filter(id=p.id).values('quantity', 'name').first()
                available = fresh_product['quantity'] if fresh_product and fresh_product['quantity'] is not None else 0

                if qty > available:
                    has_stock_issue = True
                    shortage_details.append({
                        'product_obj': p,
                        'name': p.name,
                        'requested': qty,
                        'available': available
                    })

            if courier_mode == "surface" and 0 < restricted_qty < 200:
                invoice_form.add_error(None, "❌ Surface shipping rejected for Thermoforming/Bay Material below 200.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            if courier_mode == "air" and has_resin:
                invoice_form.add_error(None, "❌ Air shipping rejected: Resin products cannot be sent by Air.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # Credit Check
            real_overdue_records = []
            has_credit_issue = False

            if not request.user.is_superuser:
                all_overdue_records = CustomerVoucherStatus.objects.filter(
                    customer=selected_customer,
                    is_credit_period_crossed=True
                ).filter(
                    Q(is_unpaid=True) | Q(is_partially_paid=True)
                ).select_related('voucher')

                if all_overdue_records.exists():
                    overdue_voucher_ids = all_overdue_records.values_list('voucher_id', flat=True)
                    vouchers_with_emi = VoucherEmiPaymentAllocation.objects.filter(
                        voucher__voucher_id__in=overdue_voucher_ids
                    ).values_list('voucher__voucher_id', flat=True).distinct()

                    real_overdue_records = all_overdue_records.exclude(voucher_id__in=vouchers_with_emi)
                    if real_overdue_records.exists():
                        has_credit_issue = True

            try:
                with transaction.atomic():
                    invoice = invoice_form.save(commit=False)
                    invoice.customer = selected_customer
                    invoice.shipping_customer = shipping_customer
                    invoice.courier_mode = courier_mode
                    invoice.created_by = request.user.username
                    invoice.save()

                    has_price_issue = False
                    any_under_msrp = False

                    req_prices_list = request.POST.getlist("requested_unit_price")
                    req_row_reasons = request.POST.getlist("requested_price_reason")
                    req_courier = request.POST.get("requested_courier_charge", "").strip()
                    req_reason = request.POST.get("request_reason", "").strip()

                    price_change_requests_for_email = []

                    for index, f in enumerate(valid_forms):
                        product_obj = f.cleaned_data.get('product')
                        qty = f.cleaned_data.get('quantity')

                        item = f.save(commit=False)
                        item.invoice = invoice
                        item.quantity = qty

                        pricing = getattr(product_obj, "proforma_price", None)
                        standard_price = pricing.price if pricing else Decimal("0.00")
                        msrp = pricing.msrp or Decimal("0.00")

                        if pricing and pricing.has_dynamic_price:
                            tier = pricing.price_tiers.filter(min_quantity__lte=qty).order_by("-min_quantity").first()
                            if tier:
                                standard_price = tier.unit_price

                        user_val = standard_price
                        if index < len(req_prices_list):
                            u_val = req_prices_list[index].strip()
                            if u_val:
                                user_val = Decimal(u_val)

                        is_permitted = self.check_is_permitted(selected_customer, product_obj, user_val, standard_price)
                        current_row_reason = req_row_reasons[index].strip() if index < len(req_row_reasons) else ""

                        if user_val < standard_price:
                            if not is_permitted:
                                has_price_issue = True
                                is_under_msrp = user_val < msrp
                                if is_under_msrp:
                                    any_under_msrp = True

                                ProformaPriceChangeRequest.objects.create(
                                    invoice=invoice, customer=selected_customer, product=product_obj,
                                    requested_by=request.user, is_product_request=True, requested_price=user_val,
                                    recommended_price=standard_price, msrp_snapshot=msrp, is_under_msrp=is_under_msrp,
                                    reason=current_row_reason, status="pending"
                                )
                                item.current_price = standard_price
                                price_change_requests_for_email.append({
                                    "product": product_obj, "quantity": qty, "requested_price": user_val,
                                    "recommended_price": standard_price, "msrp": msrp,
                                    "is_under_msrp": is_under_msrp, "reason": current_row_reason,
                                    "requested_courier_charge": None,
                                })
                            else:
                                item.current_price = user_val
                        else:
                            item.current_price = standard_price
                        item.save()

                    has_courier_issue = False
                    parsed_courier = None
                    if req_courier != "" and not request.user.is_superuser:
                        try:
                            parsed_courier = Decimal(req_courier)
                            curr_courier = invoice.courier_charge() if callable(invoice.courier_charge) else invoice.courier_charge

                            if parsed_courier != curr_courier:
                                has_courier_issue = True
                                ProformaPriceChangeRequest.objects.create(
                                    invoice=invoice,
                                    customer=selected_customer,
                                    requested_by=request.user,
                                    is_product_request=False,
                                    requested_courier_charge=parsed_courier,
                                    recommended_courier_charge=curr_courier,
                                    reason=req_reason,
                                    status="pending"
                                )
                        except Exception as e:
                            logger.error(f"Courier parse error: {e}")

                    # Price Change Notification
                    if price_change_requests_for_email or has_courier_issue:
                        try:
                            to_emails = ["bhavya@obluhc.com"]
                            cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                            if request.user.email:
                                cc_emails.append(request.user.email)
                            any_under_msrp_email = any(x["is_under_msrp"] for x in price_change_requests_for_email)
                            email_context = {
                                "doc_type": "Proforma",
                                "parent_obj": invoice,
                                "invoice": invoice,
                                "requested_by": request.user,
                                "customer": selected_customer,
                                "price_requests": price_change_requests_for_email,
                                "reason": req_reason,
                                "requested_courier_charge": parsed_courier if has_courier_issue else None,
                                "all_items": invoice.items.select_related("product"),
                                "any_under_msrp": any_under_msrp_email,
                                "review_url": "https://oblutools.com/proforma/price-change-requests/"
                            }
                            html_content = render_to_string("proforma_invoice/price_change_request_email_v2.html", email_context)
                            subject = f"💰 {'🚨 UNDER MSRP' if any_under_msrp_email else ''} Price Request (PI #{invoice.id})"
                            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                            msg.attach_alternative(html_content, "text/html")
                            msg.send(fail_silently=True)
                        except Exception as mail_err:
                            logger.error(f"Proforma Price Request Email Error: {mail_err}")

                    # Credit Bypass Request
                    if has_credit_issue:
                        bypass_req, _ = CreditPeriodOverdueByPassRequest.objects.get_or_create(
                            customer=selected_customer,
                            proforma_invoice=invoice,
                            requested_by=request.user,
                            defaults={'status': 'pending'}
                        )
                        try:
                            to_emails = ["nitin.a@obluhc.com"]
                            cc_emails = [request.user.email] if request.user.email else []
                            cc_emails.append("abhijay.obluhc@gmail.com")

                            context = {
                                "request_obj": bypass_req,
                                "overdue_invoices": real_overdue_records,
                                "customer": selected_customer,
                                "salesperson": request.user.get_full_name() or request.user.username,
                                "review_url": "https://oblutools.com/proforma/credit-bypass-requests/"
                            }
                            html_content = render_to_string("proforma_invoice/credit_bypass_request_mail.html", context)
                            subject = f"🚨 CREDIT BYPASS REQUIRED: {selected_customer.name} (PI #{invoice.id})"
                            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                            msg.attach_alternative(html_content, "text/html")
                            msg.send(fail_silently=True)
                        except Exception as e:
                            logger.error(f"Credit Request Email Error: {e}")

                    # Stock Shortage Request
                    if has_stock_issue:
                        for item in shortage_details:
                            ProformaStockShortageRequest.objects.create(
                                invoice=invoice,
                                requested_by=request.user,
                                status="pending",
                                product=item['product_obj'],
                                requested_quantity=item['requested'],
                                available_quantity=item['available'],
                            )
                        try:
                            to_emails = ["accounts@obluhc.com"]
                            cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                            if request.user.email:
                                cc_emails.append(request.user.email)
                            email_context = {
                                "invoice": invoice,
                                "requested_by": request.user,
                                "shortage_details": shortage_details,
                                "review_url": "https://oblutools.com/proforma/stock-requests/",
                            }
                            html_content = render_to_string("proforma_invoice/stock_request_email.html", email_context)
                            msg = EmailMultiAlternatives(f"📦 Stock Request (PI #{invoice.id})", "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                            msg.attach_alternative(html_content, "text/html")
                            msg.send(fail_silently=True)
                        except Exception as e:
                            logger.error(f"Stock Email Error: {e}")

                    needs_request = (has_stock_issue or has_price_issue or has_courier_issue or has_credit_issue)
                    if needs_request and not request.user.is_superuser:
                        invoice.is_price_altered = True
                        invoice.save()

                        alert_messages = []
                        if has_credit_issue: alert_messages.append("Credit Bypass (Overdue Invoices)")
                        if has_stock_issue: alert_messages.append("Stock Shortage")
                        if has_price_issue or has_courier_issue: alert_messages.append("Price/Courier Approval")

                        messages.warning(request, f"🔒 PI #{invoice.id} locked pending approvals: {', '.join(alert_messages)}.")
                        return redirect("proforma_list")

                    messages.success(request, "✅ Proforma created successfully.")
                    return redirect("proforma_detail", pk=invoice.pk)

            except Exception as e:
                invoice_form.add_error(None, f"An unexpected error occurred: {str(e)}")
                return self._render_error(request, invoice_form, formset, selected_customer)

        return self._render_error(request, invoice_form, formset, selected_customer)

    def _get_customers(self, request):
        """Scoper: Downline for ASM/RSM, full directory for Sales Head/Admin/Finance."""
        return scope_queryset(request.user, "customer.view", Customer.objects.all().order_by("name"))

    def _render_error(self, request, invoice_form, formset, selected_customer):
        requested_prices = request.POST.getlist("requested_unit_price")
        requested_reasons = request.POST.getlist("requested_price_reason")

        for i, form in enumerate(formset):
            if i < len(requested_prices):
                form.manual_price = requested_prices[i]
            if i < len(requested_reasons):
                form.manual_reason = requested_reasons[i]

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")
        items = (
            InventoryItem.objects.select_related("category", "proforma_price")
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
            .order_by("name")
        )

        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(id=shipping_id).first() if shipping_id.isdigit() else None

        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
            "selected_customer": selected_customer,
            "shipping_customer": shipping_customer,
            "requested_courier": request.POST.get("requested_courier_charge", ""),
            "request_reason": request.POST.get("request_reason", ""),
        })

class CreateQuotationMakerView(RBACViewMixin, View):
    rbac_permission = "proforma.create"
    def get(self, request, *args, **kwargs):
        quotation_form = QuotationMakerForm(user=request.user)
        formset = QuotationMakerItemFormSet(queryset=QuotationMakerItem.objects.none(), user=request.user)

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")

        items = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related("proforma_price__price_tiers", "courier_sheets")
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
            .order_by("name")
        )

        return render(request, "proforma_invoice/create_quotation_maker.html", {
            "quotation_form": quotation_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })

    def check_is_permitted(self, customer, product, requested_price, current_recommended):
        memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()
        if memory:
            if memory.base_price_at_approval == current_recommended:
                if requested_price >= memory.min_approved_price:
                    return True
        return False

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "save")
        quotation_form = QuotationMakerForm(request.POST, user=request.user)
        formset = QuotationMakerItemFormSet(request.POST, queryset=QuotationMakerItem.objects.none(), user=request.user)

        customer_id = request.POST.get("customer", "")
        selected_customer = Customer.objects.filter(id=customer_id).first() if customer_id.isdigit() else None
        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(
            id=shipping_id).first() if shipping_id.isdigit() else selected_customer

        if not selected_customer:
            quotation_form.add_error(None, "Please select a valid customer.")
            return self._render_error(request, quotation_form, formset, selected_customer)

        if not can(request.user, "customer.view", selected_customer):
            quotation_form.add_error(None, "You do not have permission to create a quote for this customer.")
            return self._render_error(request, quotation_form, formset, selected_customer)

        if quotation_form.is_valid() and formset.is_valid():
            valid_forms = [f for f in formset if f.cleaned_data and f.cleaned_data.get("product")]

            if not valid_forms:
                quotation_form.add_error(None, "❌ Please add at least one product.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            courier_mode = request.POST.get("courier_mode", "surface")
            RESTRICTED_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]
            restricted_qty = 0
            has_resin = False
            has_stock_warning = False
            shortage_details = {}

            for f in valid_forms:
                p = f.cleaned_data['product']
                qty = f.cleaned_data['quantity']

                pricing_config = ProductPrice.objects.filter(product=p).first()
                if pricing_config and qty < pricing_config.min_requirement:
                    quotation_form.add_error(None, f"❌ '{p.name}' requires min quantity {pricing_config.min_requirement}.")
                    return self._render_error(request, quotation_form, formset, selected_customer)

                cat_name = p.category.name.upper() if p.category else ""
                if cat_name in RESTRICTED_CATEGORIES: restricted_qty += qty
                if "RESIN" in cat_name: has_resin = True

                available = getattr(p, 'quantity', 0)
                if qty > available:
                    has_stock_warning = True
                    shortage_details[p.name] = f"Req: {qty}, Stock: {available}"

            if courier_mode == "surface" and 0 < restricted_qty < 200:
                quotation_form.add_error(None, "❌ Surface rejected: Sheets < 200 must be Air.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            if courier_mode == "air" and has_resin:
                quotation_form.add_error(None, "❌ Air rejected: Resin products cannot be sent by Air.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            try:
                with transaction.atomic():
                    proposal = quotation_form.save(commit=False)
                    proposal.customer = selected_customer
                    proposal.shipping_customer = shipping_customer
                    proposal.courier_mode = courier_mode
                    proposal.created_by = request.user.username
                    proposal.save()

                    has_price_issue = False
                    any_under_msrp = False
                    price_change_requests_for_email = []

                    req_prices_list = request.POST.getlist("requested_unit_price")
                    req_courier = request.POST.get("requested_courier_charge", "").strip()
                    req_reason = request.POST.get("request_reason", "").strip()

                    for index, f in enumerate(valid_forms):
                        product_obj = f.cleaned_data.get('product')
                        qty = f.cleaned_data.get('quantity')

                        item = f.save(commit=False)
                        item.quotation = proposal
                        item.save()

                        pricing = getattr(product_obj, "proforma_price", None)
                        standard_price = pricing.price if pricing else Decimal("0.00")
                        msrp = pricing.msrp or Decimal("0.00")

                        if pricing and pricing.has_dynamic_price:
                            tier = pricing.price_tiers.filter(min_quantity__lte=qty).order_by("-min_quantity").first()
                            if tier: standard_price = tier.unit_price

                        user_val = standard_price
                        if index < len(req_prices_list):
                            u_val = req_prices_list[index].strip()
                            if u_val: user_val = Decimal(u_val)

                        is_permitted = self.check_is_permitted(selected_customer, product_obj, user_val, standard_price)

                        if user_val < standard_price:
                            if is_permitted:
                                item.current_price = user_val
                            else:
                                has_price_issue = True
                                is_under_msrp = user_val < msrp
                                if is_under_msrp: any_under_msrp = True

                                ProformaPriceChangeRequest.objects.create(
                                    quotation=proposal,
                                    customer=selected_customer,
                                    product=product_obj,
                                    requested_by=request.user,
                                    is_product_request=True,
                                    quantity=qty,
                                    requested_price=user_val,
                                    recommended_price=standard_price,
                                    msrp_snapshot=msrp,
                                    is_under_msrp=is_under_msrp,
                                    reason=req_reason,
                                    status="pending"
                                )
                                item.current_price = standard_price
                                price_change_requests_for_email.append({
                                    "product": product_obj,
                                    "quantity": qty,
                                    "requested_price": user_val,
                                    "recommended_price": standard_price,
                                    "msrp": msrp,
                                    "msrp_snapshot": msrp,
                                    "is_under_msrp": is_under_msrp,
                                    "requested_courier_charge": None,
                                    "is_product_request": True
                                })
                        else:
                            item.current_price = standard_price
                        item.save()

                    has_courier_issue = False
                    if req_courier != "" and not request.user.is_superuser:
                        has_courier_issue = True
                        ProformaPriceChangeRequest.objects.create(
                            quotation=proposal,
                            customer=selected_customer,
                            requested_by=request.user,
                            is_product_request=False,
                            requested_courier_charge=Decimal(req_courier),
                            reason=req_reason,
                            status="pending"
                        )

                    if price_change_requests_for_email or has_courier_issue:
                        try:
                            to_emails = ["bhavya@obluhc.com"]
                            cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                            if request.user.email:
                                cc_emails.append(request.user.email)

                            any_under_msrp_email = any(x.get("is_under_msrp", False) for x in price_change_requests_for_email)

                            email_context = {
                                "doc_type": "Quotation",
                                "parent_obj": proposal,
                                "invoice": proposal,
                                "customer": selected_customer,
                                "requested_by": request.user,
                                "price_requests": price_change_requests_for_email,
                                "reason": req_reason,
                                "requested_courier_charge": req_courier if has_courier_issue else None,
                                "all_items": proposal.items.select_related("product"),
                                "any_under_msrp": any_under_msrp_email,
                                "review_url": "https://oblutools.com/proforma/price-change-requests/"
                            }

                            html_content = render_to_string("proforma_invoice/price_change_request_email_v2.html", email_context)
                            subject = f"💰 {'🚨 UNDER MSRP ' if any_under_msrp_email else ''}Price Request (Quotation #{proposal.id}) - {selected_customer.name}"
                            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                            msg.attach_alternative(html_content, "text/html")
                            msg.send(fail_silently=True)
                        except Exception as mail_err:
                            logger.error(f"Quotation Price Change Email Error: {mail_err}")

                    needs_approval = (has_price_issue or has_courier_issue)
                    if needs_approval and not request.user.is_superuser:
                        proposal.is_price_altered = True
                        proposal.save()

                        if any_under_msrp:
                            messages.warning(request, "⚠️ Contains items below MSRP. Super Admin approval required.")

                        messages.success(request, f"✅ Price approval request for Quotation #{proposal.id} sent.")
                        return redirect("quotation_list")

                    if has_stock_warning:
                        messages.warning(request, "✅ Quotation created, but some items are currently out of stock.")
                    else:
                        messages.success(request, "✅ Quotation created successfully.")

                    return redirect("quotation_detail", pk=proposal.pk)

            except Exception as e:
                quotation_form.add_error(None, f"Error: {str(e)}")
                return self._render_error(request, quotation_form, formset, selected_customer)

        return self._render_error(request, quotation_form, formset, selected_customer)

    def _get_customers(self, request):
        return scope_queryset(request.user, "customer.view", Customer.objects.all().order_by("name"))

    def _render_error(self, request, quotation_form, formset, selected_customer):
        requested_prices = request.POST.getlist("requested_unit_price")
        shipping_id = request.POST.get("shipping_customer", "")

        shipping_customer = None
        if shipping_id and shipping_id.isdigit():
            shipping_customer = Customer.objects.filter(id=shipping_id).first()

        for i, form in enumerate(formset):
            if i < len(requested_prices):
                form.manual_price = requested_prices[i]

        return render(request, "proforma_invoice/create_quotation_maker.html", {
            "quotation_form": quotation_form,
            "formset": formset,
            "customers": self._get_customers(request),
            "categories": Category.objects.all().order_by("name"),
            "items": InventoryItem.objects.filter(proforma_price__price__gt=0).order_by("name"),
            "selected_customer": selected_customer,
            "shipping_customer": shipping_customer,
            "requested_courier": request.POST.get("requested_courier_charge", ""),
            "request_reason": request.POST.get("request_reason", ""),
        })

# credit overdue bypass list
# ============================================================================
# CREDIT OVERDUE BYPASS LIST & APPROVAL (RBAC GATED)
# ============================================================================

class OverdueBypassListView(RBACViewMixin, ListView):
    model = CreditPeriodOverdueByPassRequest
    template_name = "proforma_invoice/credit_period_overdue_bypass_list.html"
    context_object_name = "requests"
    rbac_permission = "credit.overdue"  # <-- Only Super Admin and Sales Head

    def get_queryset(self):
        qs = CreditPeriodOverdueByPassRequest.objects.select_related(
            'customer', 'proforma_invoice', 'requested_by'
        ).order_by('-created_at')

        # Automatically scopes requests (Super Admin / Sales Head see all)
        return scope_queryset(self.request.user, "credit.overdue", qs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from customer_dashboard.models import CustomerVoucherStatus

        for req in context['requests']:
            req.overdue_history = CustomerVoucherStatus.objects.filter(
                customer=req.customer,
                is_credit_period_crossed=True
            ).filter(
                Q(is_unpaid=True) | Q(is_partially_paid=True)
            ).select_related('voucher').order_by('-voucher_date')

        return context


class ApproveOverdueBypassView(RBACViewMixin, View):
    rbac_permission = "credit.overdue"  # <-- Only Super Admin and Sales Head

    def post(self, request, pk):
        bypass_req = get_object_or_404(CreditPeriodOverdueByPassRequest, pk=pk)
        decision = request.POST.get('decision')

        if decision == 'yes':
            bypass_req.status = 'approved'
            bypass_req.approved_by = request.user
            bypass_req.reviewed_at = timezone.now()
            status_label = "APPROVED ✅"

            # Unlock the Proforma Invoice if this was the only blocker
            pi = bypass_req.proforma_invoice
            has_pending_stock = pi.stock_requests.filter(status="pending").exists()
            has_pending_price = pi.price_requests.filter(status="pending").exists()
            if not has_pending_stock and not has_pending_price:
                pi.is_price_altered = False
                pi.save()
        else:
            bypass_req.status = 'rejected'
            status_label = "REJECTED ❌"

        bypass_req.save()

        # Send notification email to the requester
        try:
            if bypass_req.requested_by.email:
                context = {
                    "request_obj": bypass_req,
                    "status_text": status_label,
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "pi_url": f"https://oblutools.com/proforma/{bypass_req.proforma_invoice.id}/"
                }
                html_content = render_to_string("proforma_invoice/credit_bypass_review_mail.html", context)
                subject = f"{status_label}: Credit Bypass Request for {bypass_req.customer.name}"

                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", [bypass_req.requested_by.email])
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Credit Status Email Error: {e}")

        messages.success(request, f"Request processed: {status_label}")
        return redirect('overdue_bypass_list')

# --- Custom Access Mixin ---
class AccountantRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        # Only allow Superusers or users with is_accountant=True
        return self.request.user.is_superuser or getattr(self.request.user, 'is_accountant', False)


# ============================================================================
# STOCK REQUESTS DASHBOARD & APPROVAL (ACCOUNTS & SUPERUSER ONLY)
# ============================================================================

class StockRequestDashboardView(RBACViewMixin, ListView):
    model = ProformaStockShortageRequest
    template_name = "proforma_invoice/stock_requests_list.html"
    context_object_name = "requests"
    rbac_permission = "stock.request"  # <-- Only Accounts & Superuser

    def get_queryset(self):
        qs = (
            ProformaStockShortageRequest.objects
            .select_related("invoice", "product", "requested_by", "reviewed_by")
            .order_by("-created_at")
        )
        return scope_queryset(self.request.user, "stock.request", qs)


class ApproveStockRequestView(RBACViewMixin, View):
    rbac_permission = "stock.request"  # <-- Only Accounts & Superuser

    def post(self, request, pk):
        req = get_object_or_404(ProformaStockShortageRequest, pk=pk)
        invoice = req.invoice
        action = request.POST.get("action")  # 'approve' or 'reject'

        product_name = req.product.name if req.product else f"Item (Req #{req.id})"

        # 1. Update individual item request status
        if action == "approve":
            req.status = "approved"
            messages.success(request, f"✅ Stock for '{product_name}' approved.")
        else:
            req.status = "rejected"
            messages.error(request, f"❌ Stock for '{product_name}' rejected.")

        # 2. Timer & Reviewer logic
        if not req.reviewed_at:
            req.reviewed_at = timezone.now()
            req.reviewed_by = request.user
        req.save()

        # 3. Check if all stock requests for this invoice are cleared
        all_stock_requests_approved = not invoice.stock_requests.exclude(status="approved").exists()

        # Check if there are any remaining pending price requests
        pending_prices = invoice.price_requests.filter(status="pending").exists()

        if all_stock_requests_approved and not pending_prices:
            invoice.is_price_altered = False
        else:
            invoice.is_price_altered = True
        invoice.save()

        # 4. Send Summary Email once all items for this invoice are reviewed
        remaining_pending = invoice.stock_requests.filter(status="pending").exists()
        if not remaining_pending and req.requested_by.email:
            try:
                dashboard_url = request.build_absolute_uri(reverse("proforma_list"))
                all_requests = invoice.stock_requests.all().select_related('product', 'reviewed_by')

                summary_subject = f"🔔 All Stock Requests Reviewed: Invoice #{invoice.id}"
                summary_context = {
                    "salesperson_name": req.requested_by.get_full_name() or req.requested_by.username,
                    "invoice": invoice,
                    "requests": all_requests,
                    "dashboard_url": dashboard_url,
                }

                summary_html = render_to_string("proforma_invoice/stock_summary_table_email.html", summary_context)
                msg_summary = EmailMultiAlternatives(
                    summary_subject,
                    "",
                    "proforma@oblutools.com",
                    [req.requested_by.email],
                    cc=["abhijay.obluhc@gmail.com"]
                )
                msg_summary.attach_alternative(summary_html, "text/html")
                msg_summary.send(fail_silently=True)
            except Exception as e:
                logger.error(f"Stock Summary Review Mail Failed: {e}")

        return redirect("stock_request_dashboard")

# ----------------------------------------------------------------------------------------------------
#legacy
class ProformaInvoiceDetailView(LoginRequiredMixin, DetailView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_detail.html"
    context_object_name = "invoice"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        invoice = self.object
        from django.contrib import messages
        from .models import ProformaStockShortageRequest

        # 1. Superuser Master Bypass
        if request.user.is_superuser or getattr(request.user, 'is_accountant', False):
            return super().get(request, *args, **kwargs)

        # 2. Data Gathering
        stock_req = ProformaStockShortageRequest.objects.filter(invoice=invoice).last()
        price_req = invoice.price_requests.all().order_by('-id').first()
        stock_status = stock_req.status if stock_req else "none"

        # =========================================================
        # 🔹 LOCKING LOGIC
        # =========================================================

        # RULE A: STOCK IS STILL PENDING -> ALWAYS LOCKED
        # Even if price is pending/approved, if warehouse hasn't cleared stock, nobody views it.
        if stock_status == "pending":
            messages.warning(request, "⏳ Warehouse (Stock) approval is still pending. Proforma locked.")
            return redirect("proforma_list")

        # RULE B: STOCK IS REJECTED AND A PRICE REQUEST EXISTS -> LOCKED
        if stock_status == "rejected" and price_req:
            messages.error(request, "❌ Stock request was rejected. Access denied.")
            return redirect("proforma_list")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        import os
        from decimal import Decimal, ROUND_HALF_UP
        from num2words import num2words
        from django.conf import settings
        from .models import ProformaPriceChangeRequest

        # =========================
        # 🔹 Load Signature
        # =========================
        signature_path = os.path.join(settings.BASE_DIR, "proforma_invoice", "assets", "sujal_signature_base64.txt")
        try:
            with open(signature_path, "r") as f:
                context["signature_base64"] = f.read().strip()
        except FileNotFoundError:
            context["signature_base64"] = ""

        items_qs = invoice.items.select_related("product__proforma_price").prefetch_related(
            "product__proforma_price__price_tiers")
        context["items"] = items_qs

        # =========================================================
        # 🔹 1. RESOLVE PRICE SOURCE (CRITICAL FIX)
        # =========================================================
        latest_price_req = invoice.price_requests.all().order_by("-id").first()

        altered_prices = {}  # Use this name everywhere
        # show_altered_template = False
        use_requested_values = False

        if latest_price_req:
            # Show the Draft layout for Pending/Approved
            if latest_price_req.status in ["approved", "pending"]:
                show_altered_template = True
                self.template_name = "proforma_invoice/proforma_detail_altered.html"

            # ONLY use requested numbers if status is officially 'approved'
            if latest_price_req:
                # Keep your template switching logic
                if latest_price_req.status in ["approved", "pending"]:
                    self.template_name = "proforma_invoice/proforma_detail_altered.html"

                # NEW LOGIC: Build the dictionary from individual approved requests
                use_requested_values = True
                approved_reqs = invoice.price_requests.filter(status="approved", is_product_request=True)
                for req in approved_reqs:
                    # Use Product ID as key to match your Section 2 loop
                    altered_prices[str(req.product.id)] = req.requested_price

        # =========================================================
        # 🔹 2. PRODUCT CALCULATION
        # =========================================================
        recalculated_items = []
        subtotal_excl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            # DETERMINE UNIT PRICE
            # FIX: Changed 'final_altered_prices' to 'altered_prices' to match Section 1
            if use_requested_values and str(item.product.id) in altered_prices:
                # Simply use the value we mapped in Section 1
                unit_price_incl = Decimal(str(altered_prices[str(item.product.id)]))

            # Choice B: Use the "Permitted" price snapshot saved during creation
            elif item.current_price:
                unit_price_incl = item.current_price

            # Choice C: Fallback to System Master Price
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

            # else:
            #     # FALLBACK to actual Master Price because request is still Pending
            #     master_pricing = getattr(item.product, "proforma_price", None)
            #     if master_pricing:
            #         unit_price_incl = master_pricing.price
            #         if master_pricing.has_dynamic_price:
            #             tier = master_pricing.price_tiers.filter(min_quantity__lte=qty).order_by(
            #                 "-min_quantity").first()
            #             if tier: unit_price_incl = tier.unit_price
            #     else:
            #         unit_price_incl = Decimal(str(item.unit_price()))
            #
            # Tally-style Tax Calculations
            divisor = Decimal("1.00") + (gst_rate / Decimal("100"))
            unit_price_excl = (unit_price_incl / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            taxable_value = (unit_price_excl * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            product_gst = (taxable_value * gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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

        # =========================================================
        # 🔹 3. COURIER CHARGES
        # =========================================================
        # We look for the specific approved request that contains a courier change
        # instead of just looking at the "latest" overall request.
        courier_req = invoice.price_requests.filter(
            requested_courier_charge__isnull=False,
            status="approved"
        ).first()

        if courier_req:
            # Use the approved value from the specific courier request object
            courier_charge = Decimal(str(courier_req.requested_courier_charge))
        else:
            # Fallback to the original invoice courier charge if no approved request exists
            raw_courier = invoice.courier_charge() if callable(invoice.courier_charge) else invoice.courier_charge
            courier_charge = Decimal(str(raw_courier or 0))

        if subtotal_excl > 0:
            combined_gst_rate = (total_product_gst / subtotal_excl * Decimal("100")).quantize(Decimal("0.01"),
                                                                                              rounding=ROUND_HALF_UP)
        else:
            combined_gst_rate = Decimal("0.00")

        courier_gst = (courier_charge * combined_gst_rate / Decimal("100")).quantize(Decimal("0.01"),
                                                                                     rounding=ROUND_HALF_UP)
        total_gst = (total_product_gst + courier_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # =========================
        # 🔹 4. TOTALS & WORDS
        # =========================
        gross_total = (subtotal_excl + courier_charge + total_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rounded_total = gross_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = (rounded_total - gross_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_total = rounded_total

        if invoice.is_intra_state():
            cgst = (total_gst / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            utgst = total_gst - cgst
            igst = Decimal("0.00")
        else:
            igst = total_gst
            cgst, utgst = Decimal("0.00"), Decimal("0.00")

        amount_in_words = num2words(final_total, lang="en_IN").title() + " Rupees Only"

        # =========================
        # 🔹 5. CONTEXT UPDATE
        # =========================
        context.update({
            "recalculated_items": recalculated_items,
            "recalculated_subtotal": subtotal_excl,
            "courier_charge": courier_charge,
            "combined_gst_rate": combined_gst_rate,
            "igst": igst, "cgst": cgst, "utgst": utgst,
            "total_gst": total_gst, "gross_total": gross_total, "round_off": round_off,
            "recalculated_grand_total": final_total,
            "amount_in_words": amount_in_words,
            "gst_type": invoice.gst_type(),
            "recalculated_igst": total_gst,
            "is_approved": use_requested_values,
        })
        return context



# ============================================================================
# PROFORMA INVOICE DETAIL VIEW (RBAC GATED & OBJECT SCOPED)
# ============================================================================

class ProformaInvoiceDetailView(RBACViewMixin, DetailView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_detail.html"
    context_object_name = "invoice"
    rbac_permission = "proforma.view"  # <-- Blocks Salespeople & HR (403)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        invoice = self.object

        # 1. OBJECT-LEVEL PERMISSION CHECK
        # - Sales Head / Admin / BM / Accounts: Allowed for all PIs
        # - RSM: Allowed ONLY if PI belongs to himself or his team
        # - ASM: Allowed ONLY if PI belongs to himself
        if not can(request.user, "proforma.view", invoice):
            raise PermissionDenied("You do not have permission to view this proforma invoice.")

        # 2. RESOLVE EXECUTIVE BYPASS FOR PENDING APPROVAL LOCKS
        emp = getattr(request.user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_executive = bool(
            request.user.is_superuser
            or getattr(request.user, "is_accountant", False)
            or any(r in user_roles for r in ["admin", "sales_head", "business", "accounts_operations"])
        )

        # Superusers, Sales Head, Business Manager, and Accounts bypass all locks immediately
        if is_executive:
            return super().get(request, *args, **kwargs)

        # 3. LOCKING LOGIC (For RSM / ASM)
        # If warehouse approval is pending, lock access for managers/sales staff
        if invoice.stock_requests.filter(status="pending").exists():
            messages.warning(request, "⏳ Warehouse approval is pending. Access locked.")
            return redirect("proforma_list")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object

        # =========================
        # 🔹 SIGNATURE LOADING
        # =========================
        signature_path = os.path.join(settings.BASE_DIR, "proforma_invoice", "assets", "sujal_signature_base64.txt")
        try:
            with open(signature_path, "r") as f:
                context["signature_base64"] = f.read().strip()
        except FileNotFoundError:
            context["signature_base64"] = ""

        # =========================================================
        # 🔹 FILTER REJECTED PRODUCTS
        # =========================================================
        rejected_ids = invoice.stock_requests.filter(status="rejected").values_list('product_id', flat=True)
        items_qs = invoice.items.select_related("product__proforma_price").prefetch_related(
            "product__proforma_price__price_tiers"
        )

        # =========================================================
        # 🔹 RESOLVE APPROVED PRICES
        # =========================================================
        latest_price_req = invoice.price_requests.all().order_by("-id").first()
        altered_prices = {}
        use_requested_values = False

        if latest_price_req and latest_price_req.status in ["approved", "pending"]:
            self.template_name = "proforma_invoice/proforma_detail_altered.html"
            if latest_price_req.status == "approved":
                use_requested_values = True
                approved_reqs = invoice.price_requests.filter(status="approved", is_product_request=True)
                for req in approved_reqs:
                    altered_prices[str(req.product.id)] = req.requested_price

        # =========================================================
        # 🔹 PRODUCT RECALCULATION (Skipping Rejected)
        # =========================================================
        recalculated_items = []
        subtotal_excl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            if item.product.id in rejected_ids:
                continue

            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            if use_requested_values and str(item.product.id) in altered_prices:
                unit_price_incl = Decimal(str(altered_prices[str(item.product.id)]))
            elif item.current_price:
                unit_price_incl = item.current_price
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

            divisor = Decimal("1.00") + (gst_rate / Decimal("100"))
            unit_price_excl = (unit_price_incl / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            taxable_value = (unit_price_excl * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            product_gst = (taxable_value * gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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

        # =========================================================
        # 🔹 COURIER CHARGES
        # =========================================================
        courier_req = invoice.price_requests.filter(
            requested_courier_charge__isnull=False,
            status="approved"
        ).order_by('-id').first()

        if courier_req:
            courier_charge = Decimal(str(courier_req.requested_courier_charge))
        else:
            raw_courier = invoice.courier_charge() if callable(invoice.courier_charge) else invoice.courier_charge
            courier_charge = Decimal(str(raw_courier or 0))

        # =========================
        # 🔹 TOTALS & WORDS
        # =========================
        if subtotal_excl > 0:
            combined_gst_rate = (total_product_gst / subtotal_excl * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            combined_gst_rate = Decimal("0.00")

        courier_gst = (courier_charge * combined_gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_gst = (total_product_gst + courier_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        gross_total = (subtotal_excl + courier_charge + total_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rounded_total = gross_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = (rounded_total - gross_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        final_total = rounded_total
        amount_in_words = num2words(final_total, lang="en_IN").title() + " Rupees Only"

        if invoice.is_intra_state():
            cgst = (total_gst / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            utgst = total_gst - cgst
            igst = Decimal("0.00")
        else:
            igst = total_gst
            cgst, utgst = Decimal("0.00"), Decimal("0.00")

        context.update({
            "recalculated_items": recalculated_items,
            "recalculated_subtotal": subtotal_excl,
            "courier_charge": courier_charge,
            "combined_gst_rate": combined_gst_rate,
            "igst": igst, "cgst": cgst, "utgst": utgst,
            "total_gst": total_gst, "gross_total": gross_total, "round_off": round_off,
            "recalculated_grand_total": final_total,
            "amount_in_words": amount_in_words,
            "gst_type": invoice.gst_type(),
            "recalculated_igst": total_gst,
            "is_approved": use_requested_values,
        })
        return context

@login_required
def get_inventory_by_category(request):
    """
    Fetches active inventory items with proforma pricing in a category.
    """
    category_id = request.GET.get("category_id")
    if not category_id:
        return JsonResponse({"products": []})

    items = (
        InventoryItem.objects
        .filter(category_id=category_id, proforma_price__isnull=False)
        .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
        .select_related("proforma_price")
        .values("id", "name")
    )

    return JsonResponse({"products": list(items)})


@login_required
def home(request):
    """
    Landing dashboard for the Proforma Invoice module.
    Only accessible to users with proforma capabilities.
    """
    has_proforma_access = bool(
        request.user.is_superuser
        or can(request.user, "proforma.view")
        or can(request.user, "proforma.create")
        or can(request.user, "proforma.dispatch")
    )

    if not has_proforma_access:
        raise PermissionDenied("You do not have permission to access the Proforma module.")

    return render(request, 'proforma_invoice/home.html')


class ProformaSPRemarkView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])

        # Security: SP can only reply to their own invoices
        if price_request.invoice.created_by != request.user.username:
            raise PermissionDenied("Unauthorized.")

        remark_text = request.POST.get('remark', '').strip()
        if remark_text:
            append_remark(price_request, request.user, remark_text)
            # Notify the Admin (Superuser)
            notify_remark_added(price_request, request.user)
            messages.success(request, "Reply sent to Admin.")

        return redirect("proforma_list")


@login_required
@csrf_protect
def update_proforma_price_remark(request):
    if request.method == 'POST':
        invoice_id = request.POST.get('invoice_id')
        remark_text = request.POST.get('remark', '').strip()

        if not remark_text or not invoice_id:
            return JsonResponse({'status': 'error', 'message': 'Missing data'}, status=400)

        invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        # RBAC Object Security: Verify caller is permitted to access this invoice
        if not can(request.user, "proforma.view", invoice):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        ProformaRemark.objects.create(
            invoice=invoice,
            user=request.user,
            remark=remark_text
        )

        all_remarks = invoice.remarks.select_related("user").order_by('created_at')

        remarks_data = []
        for r in all_remarks:
            emp = getattr(r.user, "employee_profile", None)
            u_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

            # Admin/Executive tag for the chat bubble styling:
            is_reviewer = bool(
                r.user.is_superuser
                or getattr(r.user, 'is_accountant', False)
                or any(role in u_roles for role in ["admin", "sales_head", "accounts_operations", "business"])
            )

            remarks_data.append({
                'user': r.user.get_full_name() or r.user.username,
                'text': r.remark,
                'time': timezone.localtime(r.created_at).strftime("%d %b, %H:%M"),
                'is_admin': is_reviewer
            })

        return JsonResponse({'status': 'ok', 'remarks': remarks_data})

    return JsonResponse({'status': 'error'}, status=400)


class ProformaSPRemarkView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(
            ProformaPriceChangeRequest.objects.select_related("invoice", "quotation"),
            id=kwargs["pk"]
        )
        parent_doc = price_request.invoice or price_request.quotation

        # RBAC Object Security: Only allow if user is authorized to view this document
        if not can(request.user, "proforma.view", parent_doc):
            raise PermissionDenied("Unauthorized to comment on this document.")

        remark_text = request.POST.get('remark', '').strip()
        if remark_text:
            append_remark(parent_doc, request.user, remark_text)
            notify_remark_added(price_request, request.user)
            messages.success(request, "Reply sent to Reviewer.")

        return redirect("proforma_list")


# ============================================================================
# PROFORMA INVOICE LIST VIEW (RBAC GATED)
# ============================================================================

class ProformaInvoiceListView(RBACViewMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list.html"
    context_object_name = "invoices"
    paginate_by = 40

    # 1. Blocks Salespeople, HR, and unauthorized roles with 403 Forbidden:
    rbac_permission = "proforma.view"

    def get_queryset(self):
        user = self.request.user

        # Base query with optimized joins
        qs = (
            ProformaInvoice.objects
            .select_related("customer")
            .prefetch_related("credit_approvals", "price_requests", "stock_requests")
        )

        # 2. RBAC AUTOMATIC DATA SCOPING:
        # - Superuser / Sales Head / BM / Accounts -> Sees everyone
        # - RSM -> Sees his own + his team's proformas
        # - ASM -> Sees only his own proformas
        qs = scope_queryset(user, "proforma.view", qs)

        qs = qs.annotate(
            pending_stock_count=Count('stock_requests', filter=Q(stock_requests__status='pending')),
            rejected_stock_count=Count('stock_requests', filter=Q(stock_requests__status='rejected')),
        ).prefetch_related('price_requests')

        # 3. Filters
        created_by = self.request.GET.get("created_by")
        customer = self.request.GET.get("customer")
        start_date = self.request.GET.get("start_date")
        sort_by = self.request.GET.get("sort_by")

        if created_by:
            qs = qs.filter(created_by=created_by)
        if customer:
            qs = qs.filter(customer__id=customer)
        if start_date:
            parsed_date = parse_date(start_date)
            if parsed_date:
                qs = qs.filter(date_created__date=parsed_date)

        # 4. Sorting Logic
        if sort_by == "date_asc":
            return qs.order_by("date_created")
        elif sort_by == "customer":
            return qs.order_by("customer__name")
        return qs.order_by("-date_created")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        User = get_user_model()
        user = self.request.user

        # Dropdown options are scoped to ONLY the records the user is allowed to see:
        scoped_pis = scope_queryset(user, "proforma.view", ProformaInvoice.objects.all())
        allowed_usernames = scoped_pis.values_list("created_by", flat=True).distinct()

        ctx["users"] = User.objects.filter(username__in=allowed_usernames, is_active=True).order_by("username")
        ctx["customers"] = (
            scoped_pis.select_related("customer")
            .values("customer__id", "customer__name")
            .distinct()
            .order_by("customer__name")
        )
        return ctx

# ---------------------------------------------
from django.contrib.auth import get_user_model


# ============================================================================
# DISPATCH REQUEST & STATUS MANAGEMENT (RBAC GATED)
# ============================================================================

@login_required
def request_dispatch(request, pk):
    """
    View for authorized users (creators/managers) to raise a dispatch request and notify Accounts.
    """
    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    # 1. RBAC Object Security: Verify caller is permitted to access this invoice
    if not can(request.user, "proforma.view", invoice):
        raise PermissionDenied("You do not have permission to request dispatch for this invoice.")

    # 2. Prevent duplicate requests
    if invoice.dispatch_status != 'processing':
        messages.warning(request, "Dispatch request has already been raised.")
        return redirect('proforma_list')

    # 3. Update dispatch status
    invoice.dispatch_status = 'requested'
    invoice.dispatch_requested_at = timezone.now()
    invoice.save()

    # 4. Email Notification to Accounts
    try:
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string

        accountant_emails = ["accounts@obluhc.com"]
        cc_emails = [
            "abhijay.obluhc@gmail.com",
            "swasti.obluhc@gmail.com",
            "nitin.a@obluhc.com",
            "akshay@obluhc.com",
            "operations@obluhc.com"
        ]

        if request.user.email:
            cc_emails.append(request.user.email)

        proforma_url = f"https://oblutools.com/proforma/{invoice.id}"
        subject = f"🚀 Dispatch Request: PI #{invoice.id} - {invoice.customer.name}"

        context = {
            "requested_by": request.user.get_full_name() or request.user.username,
            "invoice": invoice,
            "proforma_url": proforma_url,
        }

        html_content = render_to_string(
            "proforma_invoice/dispatch_request_admin_email.html",
            context
        )

        plain_message = f"""
Dispatch Request Raised

Proforma ID: #{invoice.id}
Customer: {invoice.customer.name}
Grand Total: ₹{invoice.calculate_final_total}

Requested By:
{request.user.get_full_name() or request.user.username}

Open Proforma:
{proforma_url}
"""

        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email="proforma@oblutools.com",
            to=accountant_emails,
            cc=cc_emails
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)

    except Exception as e:
        logger.error(f"Dispatch email failed for PI #{invoice.id}: {e}")

    messages.success(
        request,
        f"Dispatch request raised for Proforma #{invoice.id}. Accounts has been notified."
    )
    return redirect('proforma_list')


@login_required
def set_dispatch_status(request, pk, status):
    """
    Action to update dispatch status (YES to initiate / NO for hold).
    Strictly restricted to Accounts & Operations and Super Admin.
    """
    user = request.user
    emp = getattr(user, "employee_profile", None)
    user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

    is_authorized = bool(
        user.is_superuser
        or getattr(user, "is_accountant", False)
        or any(r in user_roles for r in ["admin", "accounts", "operations", "accounts_operations"])
    )

    if not is_authorized:
        raise PermissionDenied("Only Accounts & Operations and Super Admin can update dispatch status.")

    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    if invoice.dispatch_status == 'dispatched':
        messages.error(request, "This order is already completed and dispatched.")
        return redirect('proforma_invoice_dispatch')

    if status == 'yes':
        invoice.dispatch_status = 'initiated'
        invoice.save()

        # Create or fetch the record in DispatchRequest
        dispatch_req, created = DispatchRequest.objects.get_or_create(
            invoice=invoice,
            defaults={
                'requested_by': request.user,
                'status': 'initiated'
            }
        )

        DispatchStateHistory.objects.get_or_create(
            dispatch_request=dispatch_req,
            to_status="initiated",
            changed_by=request.user
        )

        status_label = "INITIATED"
        messages.success(request, f"Dispatch for Invoice #{invoice.id} initiated.")

    elif status == 'no':
        invoice.dispatch_status = 'pending'
        invoice.save()

        status_label = "PENDING / ON HOLD"
        messages.info(request, f"Invoice #{invoice.id} has been put on Pending (Hold).")
    else:
        return redirect('proforma_invoice_dispatch')

    # --- 📧 EMAIL NOTIFICATION TO SALESPERSON ---
    sp = invoice.customer.salesperson
    if sp and hasattr(sp, 'user') and sp.user and sp.user.email:
        try:
            subject = f"Status Update: Proforma #{invoice.id} - {status_label}"
            from_email = settings.DEFAULT_FROM_EMAIL
            to_email = [sp.user.email]
            context = {
                'sp_name': sp.user.get_full_name() or sp.user.username,
                'invoice': invoice,
                'status': invoice.dispatch_status,
                'site_url': "https://oblutools.com"
            }
            html_content = render_to_string('proforma_invoice/dispatch_email_notification.html', context)
            msg = EmailMultiAlternatives(subject, "", from_email, to_email)
            msg.attach_alternative(html_content, "text/html")
            import threading
            threading.Thread(target=msg.send, kwargs={"fail_silently": True}, daemon=True).start()
        except Exception as e:
            print(f"Email failed: {e}")

    return redirect('proforma_invoice_dispatch')


@login_required
def open_dispatch_detail(request, pk):
    """
    Finds or initializes the DispatchRequest for this invoice
    and redirects straight to its dispatch detail execution page.
    """
    if not can(request.user, "proforma.dispatch"):
        raise PermissionDenied("You do not have permission to access dispatch execution records.")

    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    dispatch_req, created = DispatchRequest.objects.get_or_create(
        invoice=invoice,
        defaults={
            'requested_by': request.user,
            'status': invoice.dispatch_status if invoice.dispatch_status in ['requested', 'dispatched'] else 'requested'
        }
    )
    return redirect('dispatch_detail', pk=dispatch_req.id)

# ============================================================================
# DISPATCH QUEUE LIST VIEW
# ============================================================================

class ProformaInvoiceListViewForDispatch(RBACViewMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list_dispatch.html"
    context_object_name = "invoices"
    rbac_permission = "proforma.dispatch"
    paginate_by = 40

    def dispatch(self, request, *args, **kwargs):
        """
        Strict Gate: Only Super Admin and Accounts Operations can access this view.
        """
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_allowed = bool(
            user.is_superuser
            or getattr(user, "is_accountant", False)
            or any(r in user_roles for r in ["admin", "accounts", "operations", "accounts_operations"])
        )

        if not is_allowed:
            raise PermissionDenied("Only Accounts Operations and Super Admin can access the dispatch queue.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # Filter for requested and pending dispatches with prefetching
        qs = (
            ProformaInvoice.objects.filter(dispatch_requested_at__isnull=False)
            .select_related("customer", "customer__salesperson")
            .prefetch_related(
                "items",
                "items__product",
                "items__product__proforma_price",
                "price_requests"
            )
        )

        f_id = self.request.GET.get('f_id')
        f_inv = self.request.GET.get('f_inv')
        f_user = self.request.GET.get('f_user')
        f_date = self.request.GET.get('f_date')
        f_customer = self.request.GET.get('customer', '').strip()
        sort_by = self.request.GET.get("sort_by")

        if f_id:
            qs = qs.filter(id__icontains=f_id.strip())
        if f_inv:
            qs = qs.filter(id__icontains=f_inv.strip())
        if f_user:
            qs = qs.filter(created_by__icontains=f_user.strip())
        if f_customer:
            if f_customer.isdigit():
                qs = qs.filter(customer_id=int(f_customer))
            else:
                qs = qs.filter(customer__name__icontains=f_customer)
        if f_date:
            qs = qs.filter(date_created__date=f_date)

        if sort_by == "date_asc":
            return qs.order_by("date_created")
        return qs.order_by("-dispatch_requested_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        unique_usernames = ProformaInvoice.objects.exclude(
            created_by__isnull=True
        ).values_list('created_by', flat=True).distinct()
        context['users'] = sorted(list(set(unique_usernames)))

        # Autocomplete suggestion list of customer names
        context['customers'] = (
            ProformaInvoice.objects.filter(customer__isnull=False)
            .values_list('customer__name', flat=True)
            .distinct()
            .order_by('customer__name')
        )

        return context



class ProformaProductListView(RBACViewMixin, ListView):
    model = ProductPrice
    template_name = "proforma_invoice/product_list.html"
    context_object_name = "products"

    # 1. Enforces that only roles with product_price.view can access:
    rbac_permission = "product_price.view"

    def dispatch(self, request, *args, **kwargs):
        # 2. Explicit hard gate against HR (unless superuser)
        emp = getattr(request.user, "employee_profile", None)
        user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

        if "hr" in user_roles and not request.user.is_superuser:
            raise PermissionDenied("HR department is not authorized to view product prices.")

        return super().dispatch(request, *args, **kwargs)

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

def check_price_needs_approval(user, product, requested_price):
    """
    Logic to determine if a price needs approval.
    Preserved exactly as original.
    """
    pricing = getattr(product, 'proforma_price', None)

    if not pricing:
        return True, False

    recommended_price = Decimal(str(pricing.price or 0))
    msrp = Decimal(str(pricing.msrp or 0))

    needs_req = False
    needs_acc = False

    # If requested price is lower than recommended, it needs review
    if requested_price < recommended_price:
        needs_req = True

    # If requested price is lower than MSRP (MSP), it needs Super User approval
    if requested_price < msrp:
        needs_acc = True

    return needs_req, needs_acc




class ProformaPriceChangeRequestCreateView(RBACViewMixin, View):
    template_name = "proforma_invoice/request_price_change.html"
    rbac_permission = "proforma.view"

    def dispatch(self, request, *args, **kwargs):
        self.invoice_id = self.kwargs.get("invoice_id")
        self.quotation_id = self.kwargs.get("quotation_id")

        if self.invoice_id:
            self.parent_obj = get_object_or_404(ProformaInvoice, id=self.invoice_id)
            self.doc_type = "Proforma"
            self.has_pending = ProformaPriceChangeRequest.objects.filter(invoice=self.parent_obj, status="pending").exists()
            self.redirect_url_name = "proforma_detail"
        elif self.quotation_id:
            self.parent_obj = get_object_or_404(QuotationMaker, id=self.quotation_id)
            self.doc_type = "Quotation"
            self.has_pending = ProformaPriceChangeRequest.objects.filter(quotation=self.parent_obj, status="pending").exists()
            self.redirect_url_name = "quotation_detail"
        else:
            raise Http404("Document reference not found.")

        # RBAC Object Security: Verify caller is permitted to view/manage this document
        if not can(request.user, "proforma.view", self.parent_obj):
            raise PermissionDenied("You do not have permission to request price changes on this document.")

        if request.user.is_superuser:
            messages.error(request, "Super users cannot request price changes.")
            return redirect(self.redirect_url_name, pk=self.parent_obj.id)

        if self.has_pending:
            messages.warning(request, f"There is already a pending request for this {self.doc_type}.")
            return redirect(self.redirect_url_name, pk=self.parent_obj.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self):
        items = self.parent_obj.items.select_related("product__proforma_price")

        if self.doc_type == "Proforma":
            all_past_reqs = ProformaPriceChangeRequest.objects.filter(invoice=self.parent_obj).order_by("-created_at")
        else:
            all_past_reqs = ProformaPriceChangeRequest.objects.filter(quotation=self.parent_obj).order_by("-created_at")

        for item in items:
            item_reqs = [r for r in all_past_reqs if r.is_product_request and r.product_id == item.product_id]
            item.last_processed_req = item_reqs[0] if item_reqs else None
            item.price_history = item_reqs

        courier_reqs = [r for r in all_past_reqs if not r.is_product_request]

        return {
            "parent_obj": self.parent_obj,
            "doc_type": self.doc_type,
            "invoice": self.parent_obj,
            "items": items,
            "courier_status_history": courier_reqs[0] if courier_reqs else None,
            "courier_history": courier_reqs,
        }

    def get(self, request, *args, **kwargs):
        context = self.get_context_data()
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        items = self.parent_obj.items.select_related("product__proforma_price")
        any_needs_accountant = False
        general_reason = request.POST.get('reason', '').strip()
        created_requests = []

        # 1. Product Price Change Loop
        for item in items:
            raw_val = request.POST.get(f"new_price_{item.id}")
            item_reason = request.POST.get(f"reason_{item.id}", "").strip()
            final_reason = item_reason if item_reason else general_reason

            if raw_val and str(raw_val).strip() != "":
                try:
                    requested_price = Decimal(str(raw_val).strip())
                except Exception:
                    continue

                needs_req, needs_acc = check_price_needs_approval(request.user, item.product, requested_price)

                if needs_req:
                    pricing = getattr(item.product, 'proforma_price', None)
                    standard_price = pricing.price if pricing else Decimal("0.00")
                    msrp = pricing.msrp if pricing else Decimal("0.00")

                    p_req = ProformaPriceChangeRequest.objects.create(
                        invoice=self.parent_obj if self.doc_type == "Proforma" else None,
                        quotation=self.parent_obj if self.doc_type == "Quotation" else None,
                        customer=self.parent_obj.customer,
                        requested_by=request.user,
                        product=item.product,
                        is_product_request=True,
                        quantity=item.quantity,
                        requested_price=requested_price,
                        recommended_price=standard_price,
                        msrp_snapshot=msrp,
                        is_under_msrp=needs_acc,
                        reason=final_reason,
                        status="pending"
                    )
                    created_requests.append({
                        "product": item.product,
                        "quantity": item.quantity,
                        "requested_price": requested_price,
                        "recommended_price": standard_price,
                        "msrp": msrp,
                        "msrp_snapshot": msrp,
                        "is_under_msrp": needs_acc,
                        "is_product_request": True,
                        "requested_courier_charge": None
                    })

                    if needs_acc:
                        any_needs_accountant = True

        # 2. Courier Price Change
        requested_courier_charge = request.POST.get("new_courier_charge")
        parsed_courier = None

        if requested_courier_charge and str(requested_courier_charge).strip() != "":
            try:
                parsed_courier = Decimal(str(requested_courier_charge).strip())
                curr_courier = self.parent_obj.courier_charge() if callable(self.parent_obj.courier_charge) else self.parent_obj.courier_charge

                if parsed_courier != curr_courier:
                    c_req = ProformaPriceChangeRequest.objects.create(
                        invoice=self.parent_obj if self.doc_type == "Proforma" else None,
                        quotation=self.parent_obj if self.doc_type == "Quotation" else None,
                        customer=self.parent_obj.customer,
                        requested_by=request.user,
                        is_product_request=False,
                        requested_courier_charge=parsed_courier,
                        recommended_courier_charge=curr_courier,
                        reason=general_reason,
                        status="pending"
                    )
                    created_requests.append({
                        "product": None,
                        "quantity": None,
                        "requested_price": None,
                        "recommended_price": curr_courier,
                        "msrp": None,
                        "msrp_snapshot": None,
                        "is_under_msrp": False,
                        "is_product_request": False,
                        "requested_courier_charge": parsed_courier
                    })
            except Exception as e:
                print(f"Courier parse error: {e}")

        # 3. Email Submission
        if created_requests:
            if any_needs_accountant:
                to_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]
                subject_prefix = f"🚨 DEEP DISCOUNT ({self.doc_type})"
            else:
                to_emails = ["bhavya@obluhc.com"]
                subject_prefix = f"🔔 Price Request ({self.doc_type})"

            cc_emails = ["abhijay.obluhc@gmail.com"]
            if request.user.email:
                cc_emails.append(request.user.email)

            try:
                email_context = {
                    "doc_type": self.doc_type,
                    "parent_obj": self.parent_obj,
                    "invoice": self.parent_obj,
                    "customer": self.parent_obj.customer,
                    "requested_by": request.user,
                    "reason": general_reason,
                    "price_requests": created_requests,
                    "any_under_msrp": any_needs_accountant,
                    "requested_courier_charge": parsed_courier,
                    "review_url": "https://oblutools.com/proforma/price-change-requests/"
                }
                html_content = render_to_string("proforma_invoice/price_change_request_email_v2.html", email_context)
                subject = f"{subject_prefix} #{self.parent_obj.id} - {self.parent_obj.customer.name}"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=False)
            except Exception as e:
                logger.error(f"Re-request Email Error: {e}")

            self.parent_obj.is_price_altered = True
            self.parent_obj.save()
            messages.success(request, f"✅ Price change request for {self.doc_type} #{self.parent_obj.id} submitted.")
        else:
            messages.warning(request, "⚠️ No price change entered. Please enter a new price to submit.")

        return redirect(self.redirect_url_name, pk=self.parent_obj.id)



class ProformaPriceChangeRequestListView(RBACViewMixin, ListView):
    template_name = "proforma_invoice/price_change_request_list.html"
    context_object_name = "grouped_requests"
    paginate_by = 25
    rbac_permission = "proforma.price_approval"  # <-- Requires price approval capability

    def dispatch(self, request, *args, **kwargs):
        """
        Hard gate: Only Sales Head and Super Admin can open this review dashboard.
        """
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_authorized = bool(
            user.is_superuser
            or "admin" in user_roles
            or "sales_head" in user_roles
        )

        if not is_authorized:
            raise PermissionDenied("Only the Sales Head and Super Admin can access the price change dashboard.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = ProformaPriceChangeRequest.objects.select_related(
            "invoice", "quotation", "requested_by", "reviewed_by", "customer", "product"
        ).prefetch_related(
            "invoice__items", "quotation__items",
            "invoice__remarks__user", "quotation__remarks__user"
        ).order_by("-created_at")

        f_id = self.request.GET.get('f_id')
        f_inv = self.request.GET.get('f_inv')
        f_user = self.request.GET.get('f_user')
        f_status = self.request.GET.get('f_status')
        f_date = self.request.GET.get('f_date')

        if f_id:
            queryset = queryset.filter(id__icontains=f_id)
        if f_inv:
            queryset = queryset.filter(Q(invoice__id__icontains=f_inv) | Q(quotation__id__icontains=f_inv))
        if f_user:
            queryset = queryset.filter(requested_by__username__icontains=f_user)
        if f_status:
            queryset = queryset.filter(status=f_status)
        if f_date:
            queryset = queryset.filter(created_at__date=f_date)

        grouped_data = {}
        for req in queryset:
            if req.invoice:
                group_id = f"INV-{req.invoice.id}"
                parent_obj = req.invoice
                d_type = "Proforma"
            elif req.quotation:
                group_id = f"QUO-{req.quotation.id}"
                parent_obj = req.quotation
                d_type = "Quotation"
            else:
                continue

            if group_id not in grouped_data:
                grouped_data[group_id] = {
                    'invoice': parent_obj,
                    'requests': [],
                    'display_type': d_type,
                    'all_reviewers': [],
                    'is_pending': False,
                    'start_time': req.created_at,
                    'end_time': None,
                }

            group = grouped_data[group_id]
            group['requests'].append(req)

            if req.reviewed_by:
                group['all_reviewers'].append(req.reviewed_by.username)

            if req.status == 'pending':
                group['is_pending'] = True

            if req.status != 'pending' and req.reviewed_at:
                if not group['end_time'] or req.reviewed_at > group['end_time']:
                    group['end_time'] = req.reviewed_at

        for group in grouped_data.values():
            group['unique_reviewers'] = list(dict.fromkeys(group['all_reviewers']))
            calc_end = group['end_time'] if (not group['is_pending'] and group['end_time']) else timezone.now()
            diff = calc_end - group['start_time']
            group['duration_display'] = f"{diff.days}d {diff.seconds // 3600}h {(diff.seconds // 60) % 60}m"
            group['is_running'] = group['is_pending']

        return list(grouped_data.values())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            query_params.pop('page')
        context['preserved_filters'] = f"&{query_params.urlencode()}" if query_params else ""
        return context



def can_user_approve_request(user, price_request):
    """
    RBAC Rules:
    1. Super Admin: Can approve ANY request (above, equal, or below MSRP).
    2. Sales Head: Can ONLY approve requests >= MSRP.
       If a request is below MSRP (is_under_msrp=True), Sales Head cannot approve directly.
    3. Other roles: False.
    """
    if not user.is_authenticated:
        return False

    # 1. Superuser can approve everything
    if user.is_superuser:
        return True

    emp = getattr(user, "employee_profile", None)
    user_roles = []
    if emp:
        for assignment in emp.role_assignments.filter(is_active=True):
            if assignment.is_currently_valid():
                user_roles.append(assignment.role.role_key)

    # 2. Super Admin role can approve everything
    if "admin" in user_roles:
        return True

    # 3. Sales Head can approve ONLY if NOT below MSRP (MSP)
    if "sales_head" in user_roles:
        return not price_request.is_under_msrp

    return False


# page does not reload functionality by appending ajax in post function
class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation

        #ajax implementation
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if price_request.status != "pending":
            messages.warning(request, "Request already processed.")
            # ajax implementation
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Already processed"})
            return redirect("proforma_price_change_requests")

        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        # --- 1. REJECT LOGIC ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.save()
            self.check_and_send_final_email(request, parent_obj, price_request)
            # ajax implementation
            if is_ajax:
                return JsonResponse({"status": "ok", "decision": "rejected"})
            return redirect("proforma_price_change_requests")

        # --- 2. STRICT ACCOUNTANT BLOCK (FOR COURIER AND MSRP) ---
        if decision == 'approved' and not request.user.is_superuser:

            # --- COURIER CHECK ---
            if not price_request.is_product_request:
                # Get values and ensure they are Decimals
                req_amt = Decimal(str(price_request.requested_courier_charge or 0))
                rec_amt = Decimal(str(price_request.recommended_courier_charge or 0))

                # If rec_amt is 0, try to get it from the parent object directly
                if rec_amt == 0:
                    rec_amt = Decimal(str(parent_obj.courier_charge()))


                # CALCULATE: Is 200 < (1800 / 2)?
                if rec_amt > 0 and req_amt < (rec_amt / 2):
                    # print("DEBUG: DEEP DISCOUNT DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request,
                                                           "DEEP COURIER DISCOUNT (>50%)")

            # --- PRODUCT MSRP CHECK ---
            elif price_request.is_product_request:
                if price_request.is_under_msrp:
                    # print("DEBUG: UNDER MSRP DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request, "BELOW MSRP")

        # --- 3. FINAL PROCESSING (Only reached if Admin or Safe Discount) ---
        with transaction.atomic():
            if price_request.is_product_request:
                item = parent_obj.items.filter(product=price_request.product).first()
                if item:
                    item.requested_price = price_request.requested_price
                    if hasattr(item, 'current_price'):
                        item.current_price = price_request.requested_price
                    item.save()

            price_request.status = 'approved'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()
            parent_obj.is_price_altered = True
            parent_obj.save()

        self.check_and_send_final_email(request, parent_obj, price_request)
        # ajax implementation
        if is_ajax:
            return JsonResponse({"status": "ok", "decision": "approved"})
        messages.success(request, "Request approved successfully.")
        return redirect("proforma_price_change_requests")

    # HELPER METHOD TO SEND TO NITIN SIR
    def trigger_admin_notification(self, request, parent_obj, price_request, violation_type):
        try:
            to_emails = ["abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]  # Nitin Sir
            email_context = {
                "invoice": parent_obj,
                "price_request": price_request,
                "accountant_name": request.user.username,
                "violation_type": violation_type,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)
            subject = f"🚨 Admin Review Required ({violation_type}): #{parent_obj.id}"

            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            price_request.accountant_approved = True
            price_request.is_under_msrp = True  # Mark it true so template shows "Sent to Admin"
            price_request.save()
            # ajax implementation
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "admin_notified", "violation": violation_type})
            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
            # ajax implementation
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "error", "message": str(e)})
            messages.error(request, f"Error notifying Admin: {str(e)}")


        return redirect("proforma_price_change_requests")


    def check_and_send_final_email(self, request, parent_obj, price_request):
        """ Indented correctly inside the class """
        any_pending = parent_obj.price_requests.filter(status='pending').exists()
        if not any_pending:
            try:
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com"]
                all_requests = parent_obj.price_requests.select_related('product').all()
                email_context = {
                    "invoice": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.username,
                    "reviewed_by": request.user.username,
                    "requests": all_requests,
                    "proforma_url": f"https://oblutools.com/proforma/{parent_obj.id}/",
                }
                html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
                subject = f"✅ Reviewed: #{parent_obj.id} ({parent_obj.customer.name})"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Summary Email Error: {e}")
        pass


#swasti's version - 1. Holds Approved memory value 2. fixed courier charge of re-requested altered file

class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation
        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        #ajax implementation
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if price_request.status != "pending":
            messages.warning(request, "Request already processed.")
            # ajax implementation
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Already processed"})
            return redirect("proforma_price_change_requests")

        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        # --- 1. REJECT LOGIC ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.save()
            self.check_and_send_final_email(request, parent_obj, price_request)
            # ajax implementation
            if is_ajax:
                return JsonResponse({"status": "ok", "decision": "rejected"})
            return redirect("proforma_price_change_requests")

        # --- 2. STRICT ACCOUNTANT BLOCK (FOR COURIER AND MSRP) ---
        if decision == 'approved' and not request.user.is_superuser:

            # --- COURIER CHECK ---
            if not price_request.is_product_request:
                # Get values and ensure they are Decimals
                req_amt = Decimal(str(price_request.requested_courier_charge or 0))
                rec_amt = Decimal(str(price_request.recommended_courier_charge or 0))

                # If rec_amt is 0, try to get it from the parent object directly
                if rec_amt == 0:
                    rec_amt = Decimal(str(parent_obj.courier_charge()))


                # CALCULATE: Is 200 < (1800 / 2)?
                if rec_amt > 0 and req_amt < (rec_amt / 2):
                    # print("DEBUG: DEEP DISCOUNT DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request,
                                                           "DEEP COURIER DISCOUNT (>50%)")

            # --- PRODUCT MSRP CHECK ---
            elif price_request.is_product_request:
                if price_request.is_under_msrp:
                    # print("DEBUG: UNDER MSRP DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request, "BELOW MSRP")

        # --- 3. FINAL PROCESSING (Only reached if Admin or Safe Discount) ---
        with transaction.atomic():
            if price_request.is_product_request and price_request.product:
                item = parent_obj.items.filter(product=price_request.product).first()
                if item and decision == 'approved':
                # Get decision from POST (matches name="status_{{req.id}}")
                # Note: We use the price_request.id because the model is now per-item
                # item_decision = request.POST.get(f'status_{price_request.id}', 'approved')

                        final_price = price_request.requested_price
                        rec_p = price_request.recommended_price or Decimal(0)

                        # UPDATE MEMORY
                        memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                            customer=parent_obj.customer,
                            product=item.product,
                            defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                        )
                        # If memory exists, update it if the new approved price is lower
                        if not created and memory_obj.base_price_at_approval == rec_p:
                            if final_price < memory_obj.min_approved_price:
                                memory_obj.min_approved_price = final_price
                                memory_obj.save()

                        # Apply price to current proforma item
                        item.current_price = final_price
                        item.save()


                # CASE B: Courier Charge Approval
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    # ✅ FIXED: Force update the Main Invoice Courier field
                    parent_obj.courier_charge = price_request.requested_courier_charge
                    # Some models use a function or property, but we must save to field
                    price_request.courier_status = 'approved'

            price_request.status = 'approved'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()
            parent_obj.is_price_altered = True
            parent_obj.save()


        self.check_and_send_final_email(request, parent_obj, price_request)
        # ajax implementation
        if is_ajax:
            return JsonResponse({"status": "ok", "decision": "approved"})
        messages.success(request, "Request approved successfully.")
        return redirect("proforma_price_change_requests")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice
        items = self.invoice.items.select_related("product")

        # 1. Fetch all processed requests (Approved/Rejected) for this invoice
        # We exclude 'pending' because we only want to show historical results
        item_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('id')

        # 2. Map product_id to its latest status
        status_map = {}
        for req in item_requests:
            status_map[req.product_id] = req.status  # stores "approved" or "rejected"

        # 3. Attach status to each item
        for item in items:
            item.last_processed_status = status_map.get(item.product.id)

        context["items"] = items

        # 4. Same for Courier
        context["courier_status"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").last()

        return context
    # HELPER METHOD TO SEND TO NITIN SIR
    def trigger_admin_notification(self, request, parent_obj, price_request, violation_type):
        try:
            to_emails = ["abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]  # Nitin Sir
            email_context = {
                "invoice": parent_obj,
                "price_request": price_request,
                "accountant_name": request.user.username,
                "violation_type": violation_type,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)
            subject = f"🚨 Admin Review Required ({violation_type}): #{parent_obj.id}"

            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            price_request.accountant_approved = True
            price_request.is_under_msrp = True  # Mark it true so template shows "Sent to Admin"
            price_request.save()
            # ajax implementation
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "admin_notified", "violation": violation_type})
            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
            # ajax implementation
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "error", "message": str(e)})
            messages.error(request, f"Error notifying Admin: {str(e)}")


        return redirect("proforma_price_change_requests")


    def check_and_send_final_email(self, request, parent_obj, price_request):
        """ Indented correctly inside the class """
        any_pending = parent_obj.price_requests.filter(status='pending').exists()
        if not any_pending:
            try:
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com"]
                all_requests = parent_obj.price_requests.select_related('product').all()
                email_context = {
                    "invoice": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.username,
                    "reviewed_by": request.user.username,
                    "requests": all_requests,
                    "proforma_url": f"https://oblutools.com/proforma/{parent_obj.id}/",
                }
                html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
                subject = f"✅ Reviewed: #{parent_obj.id} ({parent_obj.customer.name})"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Summary Email Error: {e}")
        pass


# quotation + proforma merge
class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation
        # --- NEW: Check for AJAX ---
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if price_request.status != "pending":
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Already processed"})
            messages.warning(request, "Request already processed.")
            return redirect("proforma_price_change_requests")

        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        # --- 1. REJECT LOGIC ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.save()
            self.check_and_send_final_email(request, parent_obj, price_request)
            if is_ajax:
                return JsonResponse({"status": "ok", "decision": "rejected"})
            return redirect("proforma_price_change_requests")


        # --- 2. STRICT ACCOUNTANT BLOCK (FOR COURIER AND MSRP) ---
        if decision == 'approved' and not request.user.is_superuser:

            # --- COURIER CHECK ---
            if not price_request.is_product_request:
                # Get values and ensure they are Decimals
                req_amt = Decimal(str(price_request.requested_courier_charge or 0))
                rec_amt = Decimal(str(price_request.recommended_courier_charge or 0))

                # If rec_amt is 0, try to get it from the parent object directly
                if rec_amt == 0:
                    rec_amt = Decimal(str(parent_obj.courier_charge()))


                # CALCULATE: Is 200 < (1800 / 2)?
                if rec_amt > 0 and req_amt < (rec_amt / 2):
                    # print("DEBUG: DEEP DISCOUNT DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request,
                                                           "DEEP COURIER DISCOUNT (>50%)")

            # --- PRODUCT MSRP CHECK ---
            elif price_request.is_product_request:
                if price_request.is_under_msrp:
                    # print("DEBUG: UNDER MSRP DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request, "BELOW MSRP")

        # --- 3. FINAL PROCESSING (Only reached if Admin or Safe Discount) ---
        with transaction.atomic():
            if price_request.is_product_request and price_request.product:
                item = parent_obj.items.filter(product=price_request.product).first()
                if item and decision == 'approved':
                # Get decision from POST (matches name="status_{{req.id}}")
                # Note: We use the price_request.id because the model is now per-item
                # item_decision = request.POST.get(f'status_{price_request.id}', 'approved')

                        final_price = price_request.requested_price
                        rec_p = price_request.recommended_price or Decimal(0)

                        # UPDATE MEMORY
                        memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                            customer=parent_obj.customer,
                            product=item.product,
                            defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                        )
                        # If memory exists, update it if the new approved price is lower
                        if not created and memory_obj.base_price_at_approval == rec_p:
                            if final_price < memory_obj.min_approved_price:
                                memory_obj.min_approved_price = final_price
                                memory_obj.save()

                        # Apply price to current proforma item
                        item.current_price = final_price
                        item.save()


                # CASE B: Courier Charge Approval
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    # ✅ FIXED: Force update the Main Invoice Courier field
                    parent_obj.courier_charge = price_request.requested_courier_charge
                    # Some models use a function or property, but we must save to field
                    price_request.courier_status = 'approved'

            price_request.status = 'approved'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()
            parent_obj.is_price_altered = True
            parent_obj.save()


        self.check_and_send_final_email(request, parent_obj, price_request)
        if is_ajax: return JsonResponse({"status": "ok", "decision": "approved"})

        messages.success(request, "Request approved successfully.")
        return redirect("proforma_price_change_requests")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice
        items = self.invoice.items.select_related("product")

        # 1. Fetch all processed requests (Approved/Rejected) for this invoice
        # We exclude 'pending' because we only want to show historical results
        item_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('id')

        # 2. Map product_id to its latest status
        status_map = {}
        for req in item_requests:
            status_map[req.product_id] = req.status  # stores "approved" or "rejected"

        # 3. Attach status to each item
        for item in items:
            item.last_processed_status = status_map.get(item.product.id)

        context["items"] = items

        # 4. Same for Courier
        context["courier_status"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").last()

        return context


    # HELPER METHOD TO SEND TO NITIN SIR
    def trigger_admin_notification(self, request, parent_obj, price_request, violation_type):
        try:
            to_emails = ["abhijay.obluhc@gmail.com"]  # Nitin Sir
            email_context = {
                "invoice": parent_obj,
                "price_request": price_request,
                "accountant_name": request.user.username,
                "violation_type": violation_type,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)
            subject = f"🚨 Admin Review Required ({violation_type}): #{parent_obj.id}"

            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            price_request.accountant_approved = True
            price_request.is_under_msrp = True  # Mark it true so template shows "Sent to Admin"
            price_request.save()
            # --- NEW: AJAX Return for Admin Notification ---
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "admin_notified", "violation": violation_type})



            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "error", "message": str(e)})
            messages.error(request, f"Error notifying Admin: {str(e)}")

        return redirect("proforma_price_change_requests")


    def check_and_send_final_email(self, request, parent_obj, price_request):
        """ Indented correctly inside the class """
        any_pending = parent_obj.price_requests.filter(status='pending').exists()
        if not any_pending:
            try:
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]
                all_requests = parent_obj.price_requests.select_related('product').all()
                email_context = {
                    "invoice": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.username,
                    "reviewed_by": request.user.username,
                    "requests": all_requests,
                    "proforma_url": f"https://oblutools.com/proforma/{parent_obj.id}/",
                }
                html_content = render_to_string("proforma_invoice/price_review_decision_email.html", email_context)
                subject = f"✅ Reviewed: #{parent_obj.id} ({parent_obj.customer.name})"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Summary Email Error: {e}")
        pass

class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation
        # --- NEW: Check for AJAX ---
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if price_request.status != "pending":
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Already processed"})
            messages.warning(request, "Request already processed.")
            return redirect("proforma_price_change_requests")

        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        # --- 1. REJECT LOGIC ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.save()
            self.check_and_send_final_email(request, parent_obj, price_request)
            if is_ajax:
                return JsonResponse({"status": "ok", "decision": "rejected"})
            return redirect("proforma_price_change_requests")


        # --- 2. STRICT ACCOUNTANT BLOCK (FOR COURIER AND MSRP) ---
        if decision == 'approved' and not request.user.is_superuser:

            # --- COURIER CHECK ---
            if not price_request.is_product_request:
                # Get values and ensure they are Decimals
                req_amt = Decimal(str(price_request.requested_courier_charge or 0))
                rec_amt = Decimal(str(price_request.recommended_courier_charge or 0))

                # If rec_amt is 0, try to get it from the parent object directly
                if rec_amt == 0:
                    rec_amt = Decimal(str(parent_obj.courier_charge()))


                # CALCULATE: Is 200 < (1800 / 2)?
                if rec_amt > 0 and req_amt < (rec_amt / 2):
                    # print("DEBUG: DEEP DISCOUNT DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request,
                                                           "DEEP COURIER DISCOUNT (>50%)")

            # --- PRODUCT MSRP CHECK ---
            elif price_request.is_product_request:
                if price_request.is_under_msrp:
                    # print("DEBUG: UNDER MSRP DETECTED - FORCING ADMIN NOTIFICATION")
                    return self.trigger_admin_notification(request, parent_obj, price_request, "BELOW MSRP")

        # --- 3. FINAL PROCESSING (Only reached if Admin or Safe Discount) ---
        with transaction.atomic():
            if price_request.is_product_request and price_request.product:
                item = parent_obj.items.filter(product=price_request.product).first()
                if item and decision == 'approved':
                # Get decision from POST (matches name="status_{{req.id}}")
                # Note: We use the price_request.id because the model is now per-item
                # item_decision = request.POST.get(f'status_{price_request.id}', 'approved')

                        final_price = price_request.requested_price
                        rec_p = price_request.recommended_price or Decimal(0)

                        # UPDATE MEMORY
                        memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                            customer=parent_obj.customer,
                            product=item.product,
                            defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                        )
                        # If memory exists, update it if the new approved price is lower
                        if not created and memory_obj.base_price_at_approval == rec_p:
                            if final_price < memory_obj.min_approved_price:
                                memory_obj.min_approved_price = final_price
                                memory_obj.save()

                        # Apply price to current proforma item
                        item.current_price = final_price
                        item.save()


                # CASE B: Courier Charge Approval
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    # ✅ FIXED: Force update the Main Invoice Courier field
                    parent_obj.courier_charge = price_request.requested_courier_charge
                    # Some models use a function or property, but we must save to field
                    price_request.courier_status = 'approved'

            price_request.status = 'approved'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()
            parent_obj.is_price_altered = True
            parent_obj.save()


        self.check_and_send_final_email(request, parent_obj, price_request)
        if is_ajax: return JsonResponse({"status": "ok", "decision": "approved"})

        messages.success(request, "Request approved successfully.")
        return redirect("proforma_price_change_requests")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice
        items = self.invoice.items.select_related("product")

        # 1. Fetch all processed requests (Approved/Rejected) for this invoice
        # We exclude 'pending' because we only want to show historical results
        item_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('id')

        # 2. Map product_id to its latest status
        status_map = {}
        for req in item_requests:
            status_map[req.product_id] = req.status  # stores "approved" or "rejected"

        # 3. Attach status to each item
        for item in items:
            item.last_processed_status = status_map.get(item.product.id)

        context["items"] = items

        # 4. Same for Courier
        context["courier_status"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").last()

        return context


    # HELPER METHOD TO SEND TO NITIN SIR
    def trigger_admin_notification(self, request, parent_obj, price_request, violation_type):
        try:
            to_emails = ["abhijay.obluhc@gmail.com"]  # Nitin Sir
            email_context = {
                "invoice": parent_obj,
                "price_request": price_request,
                "accountant_name": request.user.username,
                "violation_type": violation_type,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)
            subject = f"🚨 Admin Review Required ({violation_type}): #{parent_obj.id}"

            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            price_request.accountant_approved = True
            price_request.is_under_msrp = True  # Mark it true so template shows "Sent to Admin"
            price_request.save()
            # --- NEW: AJAX Return for Admin Notification ---
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "admin_notified", "violation": violation_type})



            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "error", "message": str(e)})
            messages.error(request, f"Error notifying Admin: {str(e)}")

        return redirect("proforma_price_change_requests")


    def check_and_send_final_email(self, request, parent_obj, price_request):
        """ Indented correctly inside the class """
        any_pending = parent_obj.price_requests.filter(status='pending').exists()
        if not any_pending:
            try:
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]
                all_requests = parent_obj.price_requests.select_related('product').all()
                email_context = {
                    "invoice": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.username,
                    "reviewed_by": request.user.username,
                    "requests": all_requests,
                    "proforma_url": f"https://oblutools.com/proforma/{parent_obj.id}/",
                }
                html_content = render_to_string("proforma_invoice/price_review_decision_email.html", email_context)
                subject = f"✅ Reviewed: #{parent_obj.id} ({parent_obj.customer.name})"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Summary Email Error: {e}")
        pass

    def check_and_send_final_email(self, request, parent_obj, price_request):
        """Sends the summary review email once all sibling requests are resolved."""
        pending_count = parent_obj.price_requests.filter(status='pending').count()
        print(f"DEBUG REVIEW: Pending requests remaining on #{parent_obj.id}: {pending_count}")

        if pending_count == 0:
            if not price_request.requested_by.email:
                print(f"DEBUG REVIEW: No email found for {price_request.requested_by.username}. Skipped.")
                return

            try:
                d_type = "Quotation" if getattr(price_request, 'quotation', None) else "Proforma"
                target_url = f"https://oblutools.com/proforma/{'quotations' if d_type == 'Quotation' else 'proformas'}/{parent_obj.id}/"

                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]

                email_context = {
                    "doc_type": d_type,
                    "parent_obj": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.get_full_name() or price_request.requested_by.username,
                    "reviewed_by": request.user.get_full_name() or request.user.username,
                    "requests": parent_obj.price_requests.select_related('product').all(),
                    "view_url": target_url,
                }

                html_content = render_to_string("proforma_invoice/price_review_decision_email.html", email_context)
                subject = f"✅ Price Review Completed: {d_type} #{parent_obj.id} ({parent_obj.customer.name})"

                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=False)
                print(f"DEBUG REVIEW: Final review decision email sent for {d_type} #{parent_obj.id}!")
            except Exception as e:
                logger.error(f"Decision Review Email Error: {e}")


# ============================================================================
# PRICE CHANGE REQUEST APPROVE VIEW (RBAC GATED)
# ============================================================================

class ProformaPriceChangeRequestApproveView(RBACViewMixin, View):
    rbac_permission = "proforma.price_approval"  # <-- Only Sales Head & Super Admin

    def dispatch(self, request, *args, **kwargs):
        """
        RBAC Gate: Strictly allows only Super Admin and Sales Head.
        """
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_authorized = bool(
            user.is_superuser
            or "admin" in user_roles
            or "sales_head" in user_roles
        )

        if not is_authorized:
            raise PermissionDenied("Only the Sales Head and Super Admin can approve price requests.")

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if price_request.status != "pending":
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Already processed"})
            messages.warning(request, "Request already processed.")
            return redirect("proforma_price_change_requests")

        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        # --- 1. REJECT LOGIC ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.save()
            self.check_and_send_final_email(request, parent_obj, price_request)
            if is_ajax:
                return JsonResponse({"status": "ok", "decision": "rejected"})
            return redirect("proforma_price_change_requests")

        # --- 2. STRICT NON-SUPERUSER BLOCK (COURIER & MSRP) ---
        # If Sales Head approves an item below MSRP or courier discount >50%, notify Super Admin (Nitin Sir)
        if decision == 'approved' and not request.user.is_superuser:
            if not price_request.is_product_request:
                req_amt = Decimal(str(price_request.requested_courier_charge or 0))
                rec_amt = Decimal(str(price_request.recommended_courier_charge or 0))

                # Safe fallback if recommended is 0
                if rec_amt == 0 and hasattr(parent_obj, 'courier_charge'):
                    raw_val = parent_obj.courier_charge() if callable(getattr(parent_obj, 'courier_charge')) else getattr(parent_obj, 'courier_charge', 0)
                    rec_amt = Decimal(str(raw_val or 0))

                # Only block if recommended was actually > 0 and discount is >50%
                if rec_amt > 0 and req_amt < (rec_amt / 2):
                    return self.trigger_admin_notification(
                        request, parent_obj, price_request, "DEEP COURIER DISCOUNT (>50%)"
                    )

            elif price_request.is_product_request:
                if price_request.is_under_msrp:
                    return self.trigger_admin_notification(
                        request, parent_obj, price_request, "BELOW MSRP"
                    )

        # --- 3. FINAL APPROVAL PROCESSING ---
        with transaction.atomic():
            if price_request.is_product_request and price_request.product:
                item = parent_obj.items.filter(product=price_request.product).first()
                if item and decision == 'approved':
                    final_price = price_request.requested_price
                    rec_p = price_request.recommended_price or Decimal("0.00")

                    memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                        customer=parent_obj.customer,
                        product=item.product,
                        defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                    )
                    if not created and memory_obj.base_price_at_approval == rec_p:
                        if final_price < memory_obj.min_approved_price:
                            memory_obj.min_approved_price = final_price
                            memory_obj.save()

                    item.current_price = final_price
                    item.save()

            # Safe Courier Update (Handles method vs field)
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    if hasattr(parent_obj, 'courier_charge') and not callable(getattr(parent_obj, 'courier_charge')):
                        parent_obj.courier_charge = price_request.requested_courier_charge
                    price_request.courier_status = 'approved'

            price_request.status = 'approved'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()
            parent_obj.is_price_altered = True
            parent_obj.save()

        self.check_and_send_final_email(request, parent_obj, price_request)
        if is_ajax:
            return JsonResponse({"status": "ok", "decision": "approved"})

        messages.success(request, "Request approved successfully.")
        return redirect("proforma_price_change_requests")

    def trigger_admin_notification(self, request, parent_obj, price_request, violation_type):
        try:
            to_emails = ["abhijay.obluhc@gmail.com"]
            email_context = {
                "invoice": parent_obj,
                "price_request": price_request,
                "accountant_name": request.user.username,
                "violation_type": violation_type,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)
            subject = f"🚨 Admin Review Required ({violation_type}): #{parent_obj.id}"

            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=True)

            price_request.accountant_approved = True
            price_request.is_under_msrp = True
            price_request.save()

            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "admin_notified", "violation": violation_type})

            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"status": "error", "message": str(e)})
            messages.error(request, f"Error notifying Admin: {str(e)}")

        return redirect("proforma_price_change_requests")

    def check_and_send_final_email(self, request, parent_obj, price_request):
        pending_count = parent_obj.price_requests.filter(status='pending').count()
        if pending_count == 0 and price_request.requested_by.email:
            try:
                d_type = "Quotation" if getattr(price_request, 'quotation', None) else "Proforma"
                target_url = f"https://oblutools.com/proforma/{'quotations' if d_type == 'Quotation' else 'proformas'}/{parent_obj.id}/"
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]

                email_context = {
                    "doc_type": d_type,
                    "parent_obj": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.get_full_name() or price_request.requested_by.username,
                    "reviewed_by": request.user.get_full_name() or request.user.username,
                    "requests": parent_obj.price_requests.select_related('product').all(),
                    "view_url": target_url,
                }
                html_content = render_to_string("proforma_invoice/price_review_decision_email.html", email_context)
                subject = f"✅ Price Review Completed: {d_type} #{parent_obj.id} ({parent_obj.customer.name})"

                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=True)
            except Exception as e:
                logger.error(f"Decision Review Email Error: {e}")




class ProformaPriceChangeRequestRejectView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"], status="pending")

        # 1. Grab and Append Remark
        remark_text = request.POST.get('review_remark', '')
        append_remark(price_request.invoice, request.user, f"REJECTED: {remark_text}")

        # 2. Finalize rejection
        price_request.status = "rejected"
        price_request.reviewed_by = request.user
        price_request.reviewed_at = timezone.now()  # Triggers the 'duration' property
        price_request.save()

        # 3. ---------------- EMAIL NOTIFICATION ----------------
        try:
            invoice_url = "https://oblutools.com/proforma/" + str(price_request.invoice.id)
            email_context = {
                "request_obj": price_request,
                "invoice": price_request.invoice,
                "user": price_request.requested_by,
                "status": "rejected",
                "remark": remark_text,  # Only send the LATEST remark in the email
                "invoice_url": invoice_url,
            }
            html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
            subject = f"❌ Price Change Rejected (Proforma #{price_request.invoice.id})"
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", [price_request.requested_by.email])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Email failed: {e}")

        messages.info(request, f"Request #{price_request.id} has been rejected.")
        return redirect("proforma_price_change_requests")

# ============================================================================
# PRICE CHANGE REQUEST REJECT VIEW (RBAC GATED)
# ============================================================================

class ProformaPriceChangeRequestRejectView(RBACViewMixin, View):
    rbac_permission = "proforma.price_approval"  # <-- Only Sales Head & Super Admin

    def dispatch(self, request, *args, **kwargs):
        """
        RBAC Gate: Strictly allows only Super Admin and Sales Head.
        """
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_authorized = bool(
            user.is_superuser
            or "admin" in user_roles
            or "sales_head" in user_roles
        )

        if not is_authorized:
            raise PermissionDenied("Only the Sales Head and Super Admin can reject price requests.")

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"], status="pending")
        parent_obj = price_request.invoice or price_request.quotation

        remark_text = request.POST.get('review_remark', '')
        append_remark(parent_obj, request.user, f"REJECTED: {remark_text}")

        price_request.status = "rejected"
        price_request.reviewed_by = request.user
        price_request.reviewed_at = timezone.now()
        price_request.save()

        # Send review summary mail if everything is reviewed
        self.check_and_send_final_email(request, parent_obj, price_request)

        messages.info(request, f"Request #{price_request.id} has been rejected.")
        return redirect("proforma_price_change_requests")

    def check_and_send_final_email(self, request, parent_obj, price_request):
        """Dispatches summary email when all requests for the quotation/invoice are resolved."""
        any_pending = parent_obj.price_requests.filter(status='pending').exists()
        if not any_pending and price_request.requested_by.email:
            try:
                d_type = "Quotation" if price_request.quotation else "Proforma"
                target_url = f"https://oblutools.com/proforma/{'quotations' if d_type == 'Quotation' else 'proformas'}/{parent_obj.id}/"

                context = {
                    "doc_type": d_type,
                    "parent_obj": parent_obj,
                    "customer_name": parent_obj.customer.name,
                    "requested_by": price_request.requested_by.get_full_name() or price_request.requested_by.username,
                    "reviewed_by": request.user.get_full_name() or request.user.username,
                    "requests": parent_obj.price_requests.select_related('product').all(),
                    "view_url": target_url,
                }
                html_content = render_to_string("proforma_invoice/price_review_decision_email.html", context)
                subject = f"✅ Reviewed: {d_type} #{parent_obj.id} ({parent_obj.customer.name})"

                msg = EmailMultiAlternatives(
                    subject,
                    "",
                    "proforma@oblutools.com",
                    [price_request.requested_by.email],
                    cc=["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.obluhc@gmail.com"]
                )
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=True)
            except Exception as e:
                logger.error(f"Error sending decision review mail: {e}")

def notify_remark_added(request_obj, author):
    """
    Bi-directional notification system.
    If Reviewer (Superuser / Sales Head) adds remark -> Requester gets email.
    If Requester adds remark -> Reviewers get email.
    """
    invoice = getattr(request_obj, "invoice", None) or getattr(request_obj, "quotation", None)
    if not invoice:
        return

    User = get_user_model()

    emp = getattr(author, "employee_profile", None)
    author_roles = []
    if emp:
        for assignment in emp.role_assignments.filter(is_active=True):
            if assignment.is_currently_valid():
                author_roles.append(assignment.role.role_key)

    # Reviewer check: Superuser, Admin, or Sales Head
    is_admin_author = bool(
        author.is_superuser
        or "admin" in author_roles
        or "sales_head" in author_roles
    )

    # 1. Logic for REVIEWER -> SALESPERSON
    if is_admin_author:
        try:
            sp_user = User.objects.get(username=invoice.created_by)
            recipient_emails = [sp_user.email] if sp_user.email else []
            subject = f"🔔 Admin Remark: #{invoice.id}"
            headline = f"{author.get_full_name() or author.username} added a remark on your price change request. Review it."
            action_url = "https://oblutools.com/proforma/proformas/"
        except User.DoesNotExist:
            return

    # 2. Logic for SALESPERSON -> REVIEWER
    else:
        recipient_emails = list(User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(email="").values_list('email', flat=True))

        subject = f"💬 SP Response: #{invoice.id}"
        headline = f"Salesperson {author.get_full_name() or author.username} has replied to your remark."
        action_url = "https://oblutools.com/proforma/price-change-requests/"

    if not recipient_emails:
        return

    newest_remark = request_obj.review_remark.split('\n')[0] if hasattr(request_obj, 'review_remark') and request_obj.review_remark else "New note added."

    context = {
        'headline': headline,
        'invoice': invoice,
        'author': author,
        'remark': newest_remark,
        'action_url': action_url
    }

    try:
        html_content = render_to_string("proforma_invoice/remark_notification.html", context)
        msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", recipient_emails)
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Remark Email Error: {e}")


def append_remark(invoice_obj, user, new_text):
    """
    Stores communication in ProformaRemark (supports Invoice or Quotation).
    """
    if not new_text or not new_text.strip():
        return None

    if hasattr(invoice_obj, "dispatch_status"):
        return ProformaRemark.objects.create(
            invoice=invoice_obj,
            quotation=None,
            user=user,
            remark=new_text
        )
    else:
        return ProformaRemark.objects.create(
            invoice=None,
            quotation=invoice_obj,
            user=user,
            remark=new_text
        )


class ProformaPriceChangeRequestRemarkView(RBACViewMixin, View):
    rbac_permission = "proforma.price_approval"  # <-- Only Reviewers (Sales Head & Admin)

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_authorized = bool(
            user.is_superuser
            or "admin" in user_roles
            or "sales_head" in user_roles
        )

        if not is_authorized:
            raise PermissionDenied("Only the Sales Head and Super Admin can add review remarks.")

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(
            ProformaPriceChangeRequest.objects.select_related("invoice", "quotation"),
            id=kwargs["pk"]
        )
        parent_doc = price_request.invoice or price_request.quotation

        remark_text = request.POST.get('review_remark', '').strip()

        if remark_text:
            append_remark(parent_doc, request.user, remark_text)

            try:
                to_email = [price_request.requested_by.email] if price_request.requested_by and price_request.requested_by.email else []
                cc_emails = ["kashish.obluhc@gmail.com", "swasti.obluhc@gmail.com"]
                if request.user.email:
                    cc_emails.append(request.user.email)

                if to_email:
                    email_context = {
                        "request_obj": price_request,
                        "invoice": parent_doc,
                        "user": request.user,
                        "status": "remark_added",
                        "remark": remark_text,
                    }

                    html_content = render_to_string(
                        "proforma_invoice/price_change_request_status_email.html",
                        email_context
                    )
                    subject = f"💬 New Remark on Price Request (Doc #{parent_doc.id})"

                    msg = EmailMultiAlternatives(
                        subject, "", "proforma@oblutools.com",
                        to_email, cc=list(set(cc_emails))
                    )
                    msg.attach_alternative(html_content, "text/html")
                    msg.send(fail_silently=True)
            except Exception as e:
                logger.error(f"Remark Notification Email failed: {e}")

            messages.success(request, "Remark added and notification sent.")
        else:
            messages.warning(request, "Remark was empty and not saved.")

        return redirect("proforma_price_change_requests")



class CourierPricingView(RBACViewMixin, TemplateView):
    template_name = "proforma_invoice/courier_editor.html"
    rbac_permission = "product_tier.manage"  # <-- Accounts Operations & Admin

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. GET FILTERS FROM URL
        search_query = self.request.GET.get('q', '').strip()
        category_id = self.request.GET.get('category', '').strip()

        # 2. FETCH ALL INVENTORY PRODUCTS
        product_qs = InventoryItem.objects.all()

        if search_query:
            product_qs = product_qs.filter(name__icontains=search_query)

        if category_id:
            product_qs = product_qs.filter(category_id=category_id)

        found_product_ids = product_qs.values_list('id', flat=True)

        # 3. SYNC LOGIC: Ensure rows exist for these found products
        with transaction.atomic():
            for p_id in found_product_ids:
                for mode_code, _ in CourierMode.choices:
                    charge_obj, _ = CourierCharge.objects.get_or_create(
                        product_id=p_id,
                        mode=mode_code
                    )
                    if not charge_obj.tiers.exists():
                        CourierChargeTier.objects.create(
                            courier_product=charge_obj,
                            min_quantity=1,
                            max_quantity=None,
                            charge=Decimal("0.00")
                        )

        # 4. FETCH TIERS
        tiers = CourierChargeTier.objects.select_related(
            'courier_product__product__category'
        ).filter(
            courier_product__product_id__in=found_product_ids
        ).order_by('courier_product__product__name', 'courier_product__mode', 'min_quantity')

        # 5. PREPARE JSON
        courier_data = []
        for t in tiers:
            p_name = t.courier_product.product.name
            cat_id = str(t.courier_product.product.category_id or "")
            mode = t.courier_product.get_mode_display()

            courier_data.append([
                int(t.id),
                str(p_name),
                str(mode),
                int(t.min_quantity),
                int(t.max_quantity) if t.max_quantity else "",
                float(t.charge),
                cat_id
            ])

        context['courier_json'] = json.dumps(courier_data)
        context['categories'] = Category.objects.all().order_by('name')
        return context


@method_decorator(csrf_exempt, name='dispatch')
class BulkUpdateCourierView(RBACViewMixin, View):
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def post(self, request, *args, **kwargs):
        payload = json.loads(request.body)
        cat_id = payload.get('category_id')
        mode = payload.get('mode')
        min_qty = int(payload.get('min_qty'))
        max_qty = payload.get('max_qty')
        max_qty = int(max_qty) if (max_qty and str(max_qty).strip() != "") else None
        new_price = Decimal(str(payload.get('price')))

        # 1. Find all products in this category
        from inventory.models import InventoryItem
        products = InventoryItem.objects.filter(category_id=cat_id)

        count = 0
        with transaction.atomic():
            for product in products:
                # 2. Get or create the CourierCharge header for this product/mode
                charge_obj, _ = CourierCharge.objects.get_or_create(
                    product=product,
                    mode=mode
                )

                # 3. Update or create the specific Slab (tier)
                tier, created = CourierChargeTier.objects.update_or_create(
                    courier_product=charge_obj,
                    min_quantity=min_qty,
                    defaults={
                        'max_quantity': max_qty,
                        'charge': new_price
                    }
                )
                count += 1

        return JsonResponse({
            "status": "success",
            "message": f"Successfully updated {count} products in this category."
        })




@method_decorator(csrf_exempt, name='dispatch')
class SaveCourierSlabsView(RBACViewMixin, View):
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            data = payload.get('data', [])

            # Use transaction to ensure either everything saves or nothing does
            with transaction.atomic():
                for index, row in enumerate(data):
                    try:
                        # row[0] is the ID (Hidden column)
                        tier_id = row[0]
                        tier = CourierChargeTier.objects.get(id=tier_id)

                        # Update values
                        tier.min_quantity = int(row[3])

                        # Handle Max Qty (can be None)
                        max_qty = row[4]
                        tier.max_quantity = int(max_qty) if (
                                    max_qty is not None and str(max_qty).strip() != "") else None

                        # Handle Charge
                        charge_val = str(row[5]).replace('₹', '').replace(',', '').strip()
                        tier.charge = Decimal(charge_val)

                        tier.save()

                    except CourierChargeTier.DoesNotExist:
                        continue
                    except Exception as row_err:
                        raise row_err  # Trigger rollback

            return JsonResponse({"status": "success", "message": "Changes saved to database."})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

# ---------------------------------------------------------------------------------------------

class CreateNewProformaCustomerView(LoginRequiredMixin, CreateView):
    model = Customer
    form_class = NewProformaCustomerForm
    template_name = "proforma_invoice/create_new_customer.html"
    success_url = reverse_lazy('create_proforma')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        customer = form.save(commit=False)
        customer.district = "N/A"
        customer.created_by = self.request.user  # The User object

        # Manual SP Assignment
        sp_to_assign = None
        if self.request.user.is_accountant:
            sp_to_assign = form.cleaned_data.get('sp_assigned')
        elif hasattr(self.request.user, "salesperson_profile"):
            sp_to_assign = self.request.user.salesperson_profile.first()

        if sp_to_assign:
            try:
                customer.salesperson = sp_to_assign
            except Exception:
                pass  # Fallback if field missing

        customer.save()
        # messages.success(self.request, f"New Lead '{customer.name}' created.")
        return super().form_valid(form)


def format_duration(td):
    if td is None or not isinstance(td, timedelta):
        return None

    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"



class ProformaTimeTrackerDashboardView(RBACViewMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_time_tracker.html"
    context_object_name = "all_invoices"
    rbac_permission = "proforma.view"

    def dispatch(self, request, *args, **kwargs):
        """
        RBAC Gate: Restricted to Super Admin, Accounts & Operations, Sales Head, and Business Manager.
        """
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        is_authorized = bool(
            user.is_superuser
            or getattr(user, "is_accountant", False)
            or any(r in user_roles for r in ["admin", "accounts_operations", "sales_head", "business"])
        )

        if not is_authorized:
            raise PermissionDenied("You do not have permission to access the time tracker dashboard.")

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # Start with all invoices and connect related data
        queryset = ProformaInvoice.objects.select_related('customer').prefetch_related(
            'price_requests',
            'stock_requests'
        )

        # Scoped via RBAC (all for executive/finance roles)
        queryset = scope_queryset(self.request.user, "proforma.view", queryset)

        # Get values from the URL filters
        filter_by_user = self.request.GET.get('f_user')
        filter_start_date = self.request.GET.get('f_start')
        filter_end_date = self.request.GET.get('f_end')

        # Apply the filters if they are selected
        if filter_by_user:
            queryset = queryset.filter(created_by=filter_by_user)

        if filter_start_date and filter_end_date:
            queryset = queryset.filter(date_created__date__range=[filter_start_date, filter_end_date])
        elif filter_start_date:
            queryset = queryset.filter(date_created__date__gte=filter_start_date)
        elif filter_end_date:
            queryset = queryset.filter(date_created__date__lte=filter_end_date)

        return queryset.order_by('-date_created')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dashboard_rows = []
        all_accounts_team_task_times = []
        all_price_approval_times = []

        total_time_taken_by_a_pi = []

        for invoice in context['all_invoices']:

            # --- 1. PRICE APPROVAL TIME ---
            last_price_request = invoice.price_requests.last()
            price_time_taken = None
            if last_price_request and last_price_request.reviewed_at:
                price_time_raw = last_price_request.reviewed_at - last_price_request.created_at
                price_time_taken = format_duration(price_time_raw)
                all_price_approval_times.append(price_time_raw)

            # --- 2. STOCK APPROVAL TIME ---
            current_stock_request = getattr(invoice, 'stock_requests', None)
            stock_time_raw = None
            stock_time_taken = None
            if current_stock_request and hasattr(current_stock_request, 'last'):
                stock_req_obj = current_stock_request.last()
                if stock_req_obj and stock_req_obj.reviewed_at:
                    stock_time_raw = stock_req_obj.reviewed_at - stock_req_obj.created_at
                    stock_time_taken = format_duration(stock_time_raw)
                    all_accounts_team_task_times.append(stock_time_raw)

            # --- 3. DISPATCH ACTION TIME ---
            dispatch_time_raw = None
            dispatch_time_taken = None
            if invoice.dispatch_requested_at and invoice.dispatched_at:
                dispatch_time_raw = invoice.dispatched_at - invoice.dispatch_requested_at
                dispatch_time_taken = format_duration(dispatch_time_raw)
                all_accounts_team_task_times.append(dispatch_time_raw)

            # --- 4. ACCOUNTS TEAM AVERAGE ---
            tasks_actually_done = [task_time for task_time in [stock_time_raw, dispatch_time_raw] if
                                   task_time is not None]
            row_accounts_average = "--"
            if tasks_actually_done:
                average_raw = sum(tasks_actually_done, timedelta(0)) / len(tasks_actually_done)
                row_accounts_average = format_duration(average_raw)

            # --- 5. FULL PROCESS TIME ---
            total_invoice_lifetime = None
            if invoice.dispatched_at:
                raw_lifetime = invoice.dispatched_at - invoice.date_created
                total_time_taken_by_a_pi.append(raw_lifetime)
                total_invoice_lifetime = format_duration(raw_lifetime)

            dashboard_rows.append({
                'invoice_data': invoice,
                'price_request_obj': last_price_request,
                'stock_request_obj': current_stock_request.last() if hasattr(current_stock_request, 'last') else None,
                'price_approval_time': price_time_taken,
                'stock_approval_time': stock_time_taken,
                'dispatch_action_time': dispatch_time_taken,
                'row_accounts_average': row_accounts_average,
                'total_invoice_lifetime': total_invoice_lifetime,
            })

        # --- FINAL CALCULATIONS ---
        final_time_taken_avg = "--"
        if total_time_taken_by_a_pi:
            total_time_avg_raw = sum(total_time_taken_by_a_pi, timedelta(0)) / len(total_time_taken_by_a_pi)
            final_time_taken_avg = format_duration(total_time_avg_raw)

        final_price_avg = "--"
        if all_price_approval_times:
            price_avg_raw = sum(all_price_approval_times, timedelta(0)) / len(all_price_approval_times)
            final_price_avg = format_duration(price_avg_raw)

        final_team_performance_avg = "--"
        if all_accounts_team_task_times:
            team_avg_raw = sum(all_accounts_team_task_times, timedelta(0)) / len(all_accounts_team_task_times)
            final_team_performance_avg = format_duration(team_avg_raw)

        context['dashboard_rows'] = dashboard_rows
        context['overall_team_avg'] = final_team_performance_avg
        context['overall_price_avg'] = final_price_avg
        context['overall_time_avg'] = final_time_taken_avg
        context['user_dropdown_list'] = ProformaInvoice.objects.values_list('created_by', flat=True).distinct()

        return context


# proforma_invoice/ check_is_permitted

def check_is_permitted(self, customer, product, requested_price, current_recommended):
    """
    Checks if this price (or lower) was already approved for this customer.
    Preserved exactly as original.
    """
    memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()

    if memory:
        req_p = Decimal(str(requested_price))
        rec_p = Decimal(str(current_recommended))

        if memory.base_price_at_approval == rec_p:
            if req_p >= memory.min_approved_price:
                return True
    return False



from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from .models import ProformaInvoice, ProformaRemark


class ManageInvoiceRemarkView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def get_object(self, pk, obj_type=None):
        if obj_type == "Quotation":
            return get_object_or_404(QuotationMaker, id=pk)
        return get_object_or_404(ProformaInvoice, id=pk)

    def get(self, request, pk, *args, **kwargs):
        obj_type = request.GET.get('obj_type')
        doc = self.get_object(pk, obj_type)

        # RBAC Object Check
        if not can(request.user, "proforma.view", doc):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        return self._get_remarks_response(doc)

    def post(self, request, pk, *args, **kwargs):
        invoice_id = request.POST.get('invoice_id') or pk
        text = request.POST.get('remark', '').strip()
        obj_type = request.POST.get('obj_type')

        if not text:
            return JsonResponse({'status': 'error', 'message': 'Empty message'}, status=400)

        doc = self.get_object(invoice_id, obj_type)

        # RBAC Object Check
        if not can(request.user, "proforma.view", doc):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        if obj_type == "Quotation":
            ProformaRemark.objects.create(
                user=request.user,
                remark=text,
                quotation=doc,
                invoice=None
            )
        else:
            ProformaRemark.objects.create(
                user=request.user,
                remark=text,
                invoice=doc,
                quotation=None
            )

        return self._get_remarks_response(doc)

    def _get_remarks_response(self, doc):
        remarks = []
        query = doc.remarks.select_related("user").all().order_by('created_at')

        for r in query:
            emp = getattr(r.user, "employee_profile", None)
            u_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

            # Determine role for CSS bubble classes (b-admin, b-accounts, b-sales)
            if r.user.is_superuser or "admin" in u_roles:
                role = 'admin'
            elif getattr(r.user, 'is_accountant', False) or any(rk in u_roles for rk in ['accounts_operations', 'business', 'sales_head']):
                role = 'accounts'
            else:
                role = 'sales'

            local_datetime = timezone.localtime(r.created_at)
            remarks.append({
                'user': r.user.get_full_name() or r.user.username,
                'text': r.remark,
                'time': local_datetime.strftime("%d %b, %H:%M"),
                'role': role
            })
        return JsonResponse({'status': 'ok', 'remarks': remarks})


class ProformaRequestDetailsApiView(LoginRequiredMixin, View):  # Renamed for clarity
    def get_value(self, obj, field_name):
        val = getattr(obj, field_name, 0)
        if callable(val):
            try:
                val = val()
            except:
                val = 0
        return float(val) if val is not None else 0

    def get(self, request, invoice_id, *args, **kwargs):
        invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        # 1. Fetch ALL requests (Pending, Approved, Rejected)
        all_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=invoice
        ).select_related('product', 'reviewed_by').order_by('-created_at')

        if not all_requests.exists():
            return JsonResponse({'status': 'error', 'message': 'No requests found.'}, status=404)

        product_list = []
        courier_requests = []  # Changed to list to show history of courier requests if multiple exist

        for req in all_requests:
            # 2. Handle Product Requests
            if req.is_product_request and req.product:
                product_list.append({
                    'name': req.product.name,
                    'requested_price': self.get_value(req, 'requested_price'),
                    'msrp': self.get_value(req, 'msrp_snapshot') or self.get_value(req, 'product.msrp'),
                    'status': req.status.upper(),
                    'reason': req.reason or "N/A",
                    'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending",
                    'reviewed_at': req.reviewed_at.strftime('%d-%m-%Y %H:%M') if req.reviewed_at else None
                })

            # 3. Handle Courier Requests
            if not req.is_product_request and req.requested_courier_charge is not None:
                courier_requests.append({
                    'original': self.get_value(invoice, 'courier_charge'),
                    'requested': self.get_value(req, 'requested_courier_charge'),
                    'status': req.status.upper(),
                    'reason': req.reason or "N/A",
                    'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending"
                })

        # 4. Final data structure
        data = {
            'invoice_id': invoice.id,
            'customer_name': invoice.customer.name if invoice.customer else "Unknown",
            'products': product_list,
            'courier_history': courier_requests,
            # Show overall summary status
            'is_fully_reviewed': not all_requests.filter(status='pending').exists()
        }

        return JsonResponse(data)

class ProformaRequestDetailsApiView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def get_value(self, obj, field_name):
        val = getattr(obj, field_name, 0)
        if callable(val):
            try:
                val = val()
            except Exception:
                val = 0
        return float(val) if val is not None else 0

    def get(self, request, invoice_id, *args, **kwargs):
        invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        # RBAC Object Security
        if not can(request.user, "proforma.view", invoice):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        all_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=invoice
        ).select_related('product', 'reviewed_by').order_by('-created_at')

        all_stock_reqs = invoice.stock_requests.all().select_related('product', 'reviewed_by').order_by('-created_at')

        product_list = []
        courier_requests = []
        stock_list = []

        for req in all_requests:
            if req.is_product_request and req.product:
                product_list.append({
                    'name': req.product.name,
                    'requested_price': self.get_value(req, 'requested_price'),
                    'msrp': self.get_value(req, 'msrp_snapshot') or self.get_value(req, 'product.msrp'),
                    'status': req.status.upper(),
                    'reason': req.reason or "N/A",
                    'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending",
                    'reviewed_at': req.reviewed_at.strftime('%d-%m-%Y %H:%M') if req.reviewed_at else None
                })

            if not req.is_product_request and req.requested_courier_charge is not None:
                courier_requests.append({
                    'original': self.get_value(invoice, 'courier_charge'),
                    'requested': self.get_value(req, 'requested_courier_charge'),
                    'status': req.status.upper(),
                    'reason': req.reason or "N/A",
                    'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending"
                })

        for s in all_stock_reqs:
            stock_list.append({
                'product_name': s.product.name if s.product else "General/Other",
                'requested_qty': s.requested_quantity,
                'available_qty': s.available_quantity,
                'status': s.status.upper(),
                'reviewed_by': s.reviewed_by.username if s.reviewed_by else "Warehouse",
                'duration': s.get_duration() or "Pending...",
                'created_at': s.created_at.strftime('%d-%m-%Y %H:%M')
            })

        data = {
            'invoice_id': invoice.id,
            'customer_name': invoice.customer.name if invoice.customer else "Unknown",
            'products': product_list,
            'courier_history': courier_requests,
            'stock_history': stock_list,
            'is_fully_reviewed': not all_requests.filter(status='pending').exists() and not all_stock_reqs.filter(status='pending').exists()
        }

        return JsonResponse(data)

from django.db.models import Avg, F, ExpressionWrapper, fields, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta

class ProformaAnalyticsDashboardView(RBACViewMixin, TemplateView):
    template_name = "proforma_invoice/analytics.html"
    rbac_permission = "proforma.view"

    def dispatch(self, request, *args, **kwargs):
        """Restricted to Executives, Accounts Operations, and Sales Management."""
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

        is_authorized = bool(
            user.is_superuser
            or getattr(user, "is_accountant", False)
            or any(r in user_roles for r in ["admin", "sales_head", "business", "accounts_operations", "rsm"])
        )
        if not is_authorized:
            raise PermissionDenied("You do not have access to the Proforma Analytics dashboard.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = timezone.now().date()
        default_start = today - timedelta(days=30)

        start_str = self.request.GET.get('start_date')
        end_str = self.request.GET.get('end_date')

        start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else default_start
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today

        pi_qs = ProformaInvoice.objects.filter(date_created__date__range=[start_date, end_date])
        # Scope PIs to user's hierarchy
        pi_qs = scope_queryset(self.request.user, "proforma.view", pi_qs)

        price_qs = ProformaPriceChangeRequest.objects.filter(created_at__date__range=[start_date, end_date])
        stock_qs = ProformaStockShortageRequest.objects.filter(created_at__date__range=[start_date, end_date])

        total_created = pi_qs.count()
        total_shipped = pi_qs.filter(dispatch_status='dispatched').count()

        delta = (end_date - start_date).days + 1
        avg_per_day = round(total_created / delta, 2) if total_created > 0 else 0

        salespeople = pi_qs.values_list('created_by', flat=True).distinct()
        cycle_expr = ExpressionWrapper(
            F('dispatched_at') - F('date_created'),
            output_field=fields.DurationField()
        )

        sales_leaderboard = []
        for name in salespeople:
            p_pi = pi_qs.filter(created_by=name)

            p_cycle = p_pi.filter(
                dispatch_status='dispatched',
                dispatched_at__isnull=False,
            ).annotate(cycle=cycle_expr).aggregate(Avg('cycle'))['cycle__avg']

            sales_leaderboard.append({
                'name': name,
                'invoices': p_pi.count(),
                'dispatches': p_pi.exclude(dispatch_requested_at__isnull=True).count(),
                'prices': price_qs.filter(requested_by__username=name).count(),
                'stocks': stock_qs.filter(requested_by__username=name).count(),
                'avg_cycle': self.format_td(p_cycle)
            })

        global_cycle_avg = pi_qs.filter(
            dispatch_status='dispatched',
            dispatched_at__isnull=False
        ).annotate(cycle=cycle_expr).aggregate(Avg('cycle'))['cycle__avg']

        sales_leaderboard = sorted(sales_leaderboard, key=lambda x: x['invoices'], reverse=True)

        duration_expr = ExpressionWrapper(F('reviewed_at') - F('created_at'), output_field=fields.DurationField())
        avg_price_time = price_qs.filter(status__in=['approved', 'rejected']).annotate(duration=duration_expr).aggregate(Avg('duration'))['duration__avg']
        avg_stock_time = stock_qs.filter(status__in=['approved', 'rejected']).annotate(duration=duration_expr).aggregate(Avg('duration'))['duration__avg']

        context.update({
            'sales_leaderboard': sales_leaderboard,
            'total_created': total_created,
            'total_shipped': total_shipped,
            'avg_per_day': avg_per_day,
            'avg_price_time': self.format_td(avg_price_time),
            'avg_stock_time': self.format_td(avg_stock_time),
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d'),
            'days_count': delta,
            'avg_cycle_time': self.format_td(global_cycle_avg),
        })
        return context

    def format_td(self, td):
        if not td:
            return "N/A"
        days, hours, minutes = td.days, td.seconds // 3600, (td.seconds // 60) % 60
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"



class ApprovedPriceListView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def get(self, request):
        user = request.user
        emp = getattr(user, "employee_profile", None)
        user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

        is_executive = bool(
            user.is_superuser
            or getattr(user, 'is_accountant', False)
            or any(r in user_roles for r in ["admin", "sales_head", "business", "accounts_operations"])
        )

        selected_sp_id = request.GET.get('sp_id')

        # Scope customers based on RBAC hierarchy
        scoped_custs = scope_queryset(user, "customer.view", Customer.objects.all())
        allowed_sp_ids = scoped_custs.values_list("salesperson_id", flat=True).distinct()

        salespeople = SalesPerson.objects.filter(id__in=allowed_sp_ids).order_by('name') if is_executive else None

        if is_executive:
            queryset = ApprovedPriceMemory.objects.select_related('customer', 'product', 'customer__salesperson').all()
            if selected_sp_id:
                queryset = queryset.filter(customer__salesperson_id=selected_sp_id)
        else:
            # Non-executives see only memories belonging to their permitted customers
            queryset = ApprovedPriceMemory.objects.filter(
                customer__in=scoped_custs
            ).select_related('customer', 'product', 'customer__salesperson')

        return render(request, "proforma_invoice/approved_price_list.html", {
            "memories": queryset,
            "salespeople": salespeople,
            "is_accountant": is_executive,
            "selected_sp_id": selected_sp_id,
        })

# ============================================================================
# DELETE APPROVED PRICE MEMORY (EXECUTIVE & ACCOUNTS ONLY)
# ============================================================================

@login_required
def delete_approved_price(request, pk):
    user = request.user
    emp = getattr(user, "employee_profile", None)
    user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

    # RBAC Executive Authorization check
    is_authorized = bool(
        user.is_superuser
        or getattr(user, 'is_accountant', False)
        or any(r in user_roles for r in ["admin", "sales_head", "business", "accounts_operations"])
    )

    if not is_authorized:
        messages.error(request, "You do not have permission to delete approved price records.")
        return redirect('approved_price_list')

    memory = get_object_or_404(ApprovedPriceMemory, pk=pk)
    memory.delete()
    messages.success(request, "Approved price memory deleted successfully.")
    return redirect('approved_price_list')



# ============================================================================
# CREATE QUOTATION MAKER VIEW (RBAC GATED)
# ============================================================================


class CreateQuotationMakerView(RBACViewMixin, View):
    rbac_permission = "proforma.create"

    def get(self, request, *args, **kwargs):
        quotation_form = QuotationMakerForm(user=request.user)
        formset = QuotationMakerItemFormSet(queryset=QuotationMakerItem.objects.none(), user=request.user)

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")

        items = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related("proforma_price__price_tiers", "courier_sheets")
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
            .order_by("name")
        )

        return render(request, "proforma_invoice/create_quotation_maker.html", {
            "quotation_form": quotation_form,
            "formset": formset,
            "customers": customers,
            "categories": categories,
            "items": items,
        })

    def check_is_permitted(self, customer, product, requested_price, current_recommended):
        """Checks if this price was already approved for this customer historically."""
        memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()
        if memory:
            if memory.base_price_at_approval == current_recommended:
                if requested_price >= memory.min_approved_price:
                    return True
        return False

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "save")
        quotation_form = QuotationMakerForm(request.POST, user=request.user)
        formset = QuotationMakerItemFormSet(request.POST, queryset=QuotationMakerItem.objects.none(), user=request.user)

        customer_id = request.POST.get("customer", "")
        selected_customer = Customer.objects.filter(id=customer_id).first() if customer_id.isdigit() else None
        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(
            id=shipping_id).first() if shipping_id.isdigit() else selected_customer

        if not selected_customer:
            quotation_form.add_error(None, "Please select a valid customer.")
            return self._render_error(request, quotation_form, formset, selected_customer)

        # RBAC Object Security: Verify caller is permitted to access this customer
        if not can(request.user, "customer.view", selected_customer):
            quotation_form.add_error(None, "You do not have permission to create a quote for this customer.")
            return self._render_error(request, quotation_form, formset, selected_customer)

        if quotation_form.is_valid() and formset.is_valid():
            valid_forms = [f for f in formset if f.cleaned_data and f.cleaned_data.get("product")]

            if not valid_forms:
                quotation_form.add_error(None, "❌ Please add at least one product.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            # ================= 1. DATA GATHERING & VALIDATION =================
            courier_mode = request.POST.get("courier_mode", "surface")
            RESTRICTED_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]

            restricted_qty = 0
            has_resin = False
            has_stock_warning = False
            shortage_details = {}

            for f in valid_forms:
                p = f.cleaned_data['product']
                qty = f.cleaned_data['quantity']

                pricing_config = ProductPrice.objects.filter(product=p).first()
                if pricing_config and qty < pricing_config.min_requirement:
                    quotation_form.add_error(None, f"❌ '{p.name}' requires min quantity {pricing_config.min_requirement}.")
                    return self._render_error(request, quotation_form, formset, selected_customer)

                cat_name = p.category.name.upper() if p.category else ""
                if cat_name in RESTRICTED_CATEGORIES:
                    restricted_qty += qty
                if "RESIN" in cat_name:
                    has_resin = True

                available = getattr(p, 'quantity', 0)
                if qty > available:
                    has_stock_warning = True
                    shortage_details[p.name] = f"Req: {qty}, Stock: {available}"

            # ================= 2. COURIER LOGIC RULES =================
            if courier_mode == "surface" and 0 < restricted_qty < 200:
                quotation_form.add_error(None, "❌ Surface rejected: Sheets < 200 must be Air.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            if courier_mode == "air" and has_resin:
                quotation_form.add_error(None, "❌ Air rejected: Resin products cannot be sent by Air.")
                return self._render_error(request, quotation_form, formset, selected_customer)

            # ================= 3. SAVE PROCESS =================
            try:
                with transaction.atomic():
                    proposal = quotation_form.save(commit=False)
                    proposal.customer = selected_customer
                    proposal.shipping_customer = shipping_customer
                    proposal.courier_mode = courier_mode
                    proposal.created_by = request.user.username
                    proposal.save()

                    has_price_issue = False
                    any_under_msrp = False
                    price_change_requests_for_email = []

                    req_prices_list = request.POST.getlist("requested_unit_price")
                    req_courier = request.POST.get("requested_courier_charge", "").strip()
                    req_reason = request.POST.get("request_reason", "").strip()

                    for index, f in enumerate(valid_forms):
                        product_obj = f.cleaned_data.get('product')
                        qty = f.cleaned_data.get('quantity')

                        item = f.save(commit=False)
                        item.quotation = proposal
                        item.save()

                        pricing = getattr(product_obj, "proforma_price", None)
                        standard_price = pricing.price if pricing else Decimal("0.00")
                        msrp = pricing.msrp or Decimal("0.00")

                        if pricing and pricing.has_dynamic_price:
                            tier = pricing.price_tiers.filter(min_quantity__lte=qty).order_by("-min_quantity").first()
                            if tier:
                                standard_price = tier.unit_price

                        user_val = standard_price
                        if index < len(req_prices_list):
                            u_val = req_prices_list[index].strip()
                            if u_val:
                                user_val = Decimal(u_val)

                        is_permitted = self.check_is_permitted(selected_customer, product_obj, user_val, standard_price)

                        if user_val < standard_price:
                            if is_permitted:
                                item.current_price = user_val
                            else:
                                has_price_issue = True
                                is_under_msrp = user_val < msrp
                                if is_under_msrp:
                                    any_under_msrp = True

                                ProformaPriceChangeRequest.objects.create(
                                    quotation=proposal,
                                    customer=selected_customer,
                                    product=product_obj,
                                    requested_by=request.user,
                                    is_product_request=True,
                                    quantity=qty,
                                    requested_price=user_val,
                                    recommended_price=standard_price,
                                    msrp_snapshot=msrp,
                                    is_under_msrp=is_under_msrp,
                                    reason=req_reason,
                                    status="pending"
                                )
                                item.current_price = standard_price
                                price_change_requests_for_email.append({
                                    "product": product_obj,
                                    "quantity": qty,
                                    "requested_price": user_val,
                                    "recommended_price": standard_price,
                                    "msrp": msrp,
                                    "msrp_snapshot": msrp,
                                    "is_under_msrp": is_under_msrp,
                                    "requested_courier_charge": None,
                                    "is_product_request": True
                                })
                        else:
                            item.current_price = standard_price

                        item.save()

                    # ================= 4. HANDLE COURIER REQUEST =================
                    has_courier_issue = False
                    if req_courier != "" and not request.user.is_superuser:
                        has_courier_issue = True
                        ProformaPriceChangeRequest.objects.create(
                            quotation=proposal,
                            customer=selected_customer,
                            requested_by=request.user,
                            is_product_request=False,
                            requested_courier_charge=Decimal(req_courier),
                            reason=req_reason,
                            status="pending"
                        )

                    # ================= 5. EMAIL DISPATCH =================
                    if price_change_requests_for_email or has_courier_issue:
                        try:
                            to_emails = ["bhavya@obluhc.com"]
                            cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                            if request.user.email:
                                cc_emails.append(request.user.email)

                            any_under_msrp_email = any(x.get("is_under_msrp", False) for x in price_change_requests_for_email)

                            email_context = {
                                "doc_type": "Quotation",
                                "parent_obj": proposal,
                                "invoice": proposal,
                                "customer": selected_customer,
                                "requested_by": request.user,
                                "price_requests": price_change_requests_for_email,
                                "reason": req_reason,
                                "requested_courier_charge": req_courier if has_courier_issue else None,
                                "all_items": proposal.items.select_related("product"),
                                "any_under_msrp": any_under_msrp_email,
                                "review_url": "https://oblutools.com/proforma/price-change-requests/"
                            }

                            html_content = render_to_string("proforma_invoice/price_change_request_email_v2.html", email_context)
                            subject = f"💰 {'🚨 UNDER MSRP ' if any_under_msrp_email else ''}Price Request (Quotation #{proposal.id}) - {selected_customer.name}"

                            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                            msg.attach_alternative(html_content, "text/html")
                            msg.send(fail_silently=True)
                        except Exception as mail_err:
                            logger.error(f"Quotation Price Change Email Error: {mail_err}")

                    needs_approval = (has_price_issue or has_courier_issue)

                    if needs_approval and not request.user.is_superuser:
                        proposal.is_price_altered = True
                        proposal.save()

                        if any_under_msrp:
                            messages.warning(request, "⚠️ Contains items below MSRP. Super Admin approval required.")

                        messages.success(request, f"✅ Price approval request for Quotation #{proposal.id} sent.")
                        return redirect("quotation_list")

                    if has_stock_warning:
                        messages.warning(request, "✅ Quotation created, but some items are currently out of stock.")
                    else:
                        messages.success(request, "✅ Quotation created successfully.")

                    return redirect("quotation_detail", pk=proposal.pk)

            except Exception as e:
                quotation_form.add_error(None, f"Error: {str(e)}")
                return self._render_error(request, quotation_form, formset, selected_customer)

        return self._render_error(request, quotation_form, formset, selected_customer)

    def _get_customers(self, request):
        """Dynamic RBAC: Downline for ASM/RSM, full directory for Sales Head/Admin/Finance."""
        return scope_queryset(request.user, "customer.view", Customer.objects.all().order_by("name"))

    def _render_error(self, request, quotation_form, formset, selected_customer):
        requested_prices = request.POST.getlist("requested_unit_price")
        shipping_id = request.POST.get("shipping_customer", "")

        shipping_customer = None
        if shipping_id and shipping_id.isdigit():
            shipping_customer = Customer.objects.filter(id=shipping_id).first()

        for i, form in enumerate(formset):
            if i < len(requested_prices):
                form.manual_price = requested_prices[i]

        return render(request, "proforma_invoice/create_quotation_maker.html", {
            "quotation_form": quotation_form,
            "formset": formset,
            "customers": self._get_customers(request),
            "categories": Category.objects.all().order_by("name"),
            "items": InventoryItem.objects.filter(proforma_price__price__gt=0).order_by("name"),
            "selected_customer": selected_customer,
            "shipping_customer": shipping_customer,
            "requested_courier": request.POST.get("requested_courier_charge", ""),
            "request_reason": request.POST.get("request_reason", ""),
        })

from django.views.generic import DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP
from num2words import num2words
import os

# Assuming your model names are QuotationMaker and QuotationMakerItem
from .models import QuotationMaker, ProformaPriceChangeRequest


# ============================================================================
# QUOTATION MAKER DETAIL VIEW (RBAC GATED & OBJECT SCOPED)
# ============================================================================

class QuotationMakerDetailView(RBACViewMixin, DetailView):
    model = QuotationMaker
    template_name = "proforma_invoice/quotation_maker_detail.html"
    context_object_name = "quotation"
    rbac_permission = "proforma.view"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        quotation = self.object

        # RBAC Object Security:
        # - Admin / Sales Head / BM / Accounts -> Can view all quotations
        # - RSM -> Can view own + team's quotations
        # - ASM -> Can view own quotations
        if not can(request.user, "proforma.view", quotation):
            raise PermissionDenied("You do not have permission to view this quotation.")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quotation = self.object

        # =========================
        # 🔹 Load Signature (Same as PI)
        # =========================
        signature_path = os.path.join(settings.BASE_DIR, "proforma_invoice", "assets", "sujal_signature_base64.txt")
        try:
            with open(signature_path, "r") as f:
                context["signature_base64"] = f.read().strip()
        except FileNotFoundError:
            context["signature_base64"] = ""

        items_qs = quotation.items.select_related("product__proforma_price").prefetch_related(
            "product__proforma_price__price_tiers"
        )
        context["items"] = items_qs

        # =========================================================
        # 🔹 1. RESOLVE PRICE SOURCE (Override Logic)
        # =========================================================
        latest_price_req = quotation.price_requests.all().order_by("-id").first()
        altered_prices = {}
        use_requested_values = False

        theme_param = self.request.GET.get("theme")
        is_college_theme = (theme_param == "college") or (
                    not theme_param and getattr(quotation, "template_theme", "standard") == "college")

        if is_college_theme:
            self.template_name = "proforma_invoice/quotation_detail_college.html"
        elif latest_price_req and latest_price_req.status in ["approved", "pending"]:
            self.template_name = "proforma_invoice/quotation_detail_altered.html"
        else:
            self.template_name = "proforma_invoice/quotation_maker_detail.html"

        if latest_price_req:
            if latest_price_req.status in ["approved", "pending"]:
                self.template_name = "proforma_invoice/quotation_detail_altered.html"

            if latest_price_req.status == "approved":
                use_requested_values = True
                approved_reqs = quotation.price_requests.filter(status="approved", is_product_request=True)
                for req in approved_reqs:
                    if req.product:
                        altered_prices[str(req.product.id)] = req.requested_price

        # =========================================================
        # 🔹 2. PRODUCT CALCULATION (GST & TAXABLE VALUE)
        # =========================================================
        recalculated_items = []
        subtotal_excl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            # Priority 1: Approved override
            if use_requested_values and str(item.product.id) in altered_prices:
                unit_price_incl = Decimal(str(altered_prices[str(item.product.id)]))
            # Priority 2: Permitted snapshot
            elif item.current_price:
                unit_price_incl = item.current_price
            # Priority 3: System master price
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

            divisor = Decimal("1.00") + (gst_rate / Decimal("100"))
            unit_price_excl = (unit_price_incl / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            taxable_value = (unit_price_excl * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            product_gst = (taxable_value * gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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

        # =========================================================
        # 🔹 3. COURIER CHARGES (Approved vs Actual)
        # =========================================================
        courier_req = quotation.price_requests.filter(
            requested_courier_charge__isnull=False,
            status="approved"
        ).first()

        if courier_req:
            courier_charge = Decimal(str(courier_req.requested_courier_charge))
        else:
            raw_courier = quotation.courier_charge() if callable(quotation.courier_charge) else quotation.courier_charge
            courier_charge = Decimal(str(raw_courier or 0))

        if subtotal_excl > 0:
            combined_gst_rate = (total_product_gst / subtotal_excl * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            combined_gst_rate = Decimal("0.00")

        courier_gst = (courier_charge * combined_gst_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_gst = (total_product_gst + courier_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # =========================
        # 🔹 4. TOTALS & ROUNDING
        # =========================
        gross_total = (subtotal_excl + courier_charge + total_gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rounded_total = gross_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = (rounded_total - gross_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        Amount_in_words = num2words(rounded_total, lang="en_IN").title() + " Rupees Only"

        if quotation.is_intra_state():
            cgst = (total_gst / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            sgst = total_gst - cgst
            igst = Decimal("0.00")
        else:
            igst = total_gst
            cgst, sgst = Decimal("0.00"), Decimal("0.00")

        context.update({
            "recalculated_items": recalculated_items,
            "recalculated_subtotal": subtotal_excl,
            "courier_charge": courier_charge,
            "igst": igst,
            "cgst": cgst,
            "sgst": sgst,
            "utgst": sgst,
            "total_gst": total_gst,
            "gross_total": gross_total,
            "round_off": round_off,
            "grand_total": rounded_total,
            "Amount_in_words": Amount_in_words,
            "gst_type": quotation.gst_type(),
        })
        return context


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import QuotationMaker, ProformaInvoice, ProformaInvoiceItem


# ============================================================================
# QUOTATION LIST VIEW (RBAC GATED & HIERARCHY SCOPED)
# ============================================================================

class QuotationListView(RBACViewMixin, ListView):
    model = QuotationMaker
    template_name = "proforma_invoice/quotation_list.html"
    context_object_name = "quotations"
    rbac_permission = "proforma.view"  # <-- Blocks Salespeople & HR (403)

    def get_queryset(self):
        user = self.request.user

        # 1. Base Query with optimized relations
        qs = (
            QuotationMaker.objects
            .select_related("customer")
            .prefetch_related("price_requests")
        )

        # 2. RBAC Dynamic Scoping (Self / Team Downline / All)
        qs = scope_queryset(user, "proforma.view", qs)

        # 3. Order newest first
        return qs.order_by("-date_created")

# ============================================================================
# CONVERT QUOTATION TO PI & REQUEST DETAILS API (RBAC GATED)
# ============================================================================

@login_required
def convert_quotation_to_pi(request, q_id):
    quotation = get_object_or_404(QuotationMaker, id=q_id)

    # 1. RBAC SECURITY CHECK
    # Only users with proforma.create (Admin, Sales Head, BM, Accounts, RSM, ASM)
    # AND authorized to view this quote can convert it. (Salespeople & HR are blocked).
    if not can(request.user, "proforma.create") or not can(request.user, "proforma.view", quotation):
        raise PermissionDenied("You do not have permission to convert this quotation to a Proforma Invoice.")

    selected_customer = quotation.customer

    with transaction.atomic():
        # 1. Header Creation
        pi = ProformaInvoice.objects.create(
            customer=quotation.customer,
            shipping_customer=quotation.shipping_customer,
            created_by=request.user.username,
            courier_mode=quotation.courier_mode,
        )
        quotation.price_requests.update(invoice=pi)

        # 2. Mandatory Credit Check
        actual_credit_req_created = False
        real_overdue_records = []

        if not request.user.is_superuser:
            all_overdue = CustomerVoucherStatus.objects.filter(
                customer=selected_customer,
                is_credit_period_crossed=True
            ).filter(Q(is_unpaid=True) | Q(is_partially_paid=True)).select_related('voucher')

            if all_overdue.exists():
                overdue_ids = all_overdue.values_list('voucher_id', flat=True)
                vouchers_with_emi = VoucherEmiPaymentAllocation.objects.filter(
                    voucher__voucher_id__in=overdue_ids
                ).values_list('voucher__voucher_id', flat=True).distinct()

                real_overdue_records = all_overdue.exclude(voucher_id__in=vouchers_with_emi)

                if real_overdue_records.exists():
                    bypass_req, _ = CreditPeriodOverdueByPassRequest.objects.get_or_create(
                        customer=selected_customer,
                        proforma_invoice=pi,
                        requested_by=request.user,
                        defaults={'status': 'pending'}
                    )
                    actual_credit_req_created = True

                    try:
                        to_emails = ["nitin.a@obluhc.com"]
                        cc_emails = [request.user.email] if request.user.email else []
                        cc_emails.append("abhijay.obluhc@gmail.com")

                        context = {
                            "request_obj": bypass_req,
                            "overdue_invoices": real_overdue_records,
                            "customer": selected_customer,
                            "salesperson": request.user.get_full_name() or request.user.username,
                            "review_url": "https://oblutools.com/proforma/credit-bypass-requests/"
                        }

                        html_content = render_to_string("proforma_invoice/credit_bypass_request_mail.html", context)
                        msg = EmailMultiAlternatives(
                            f"🚨 CREDIT BYPASS REQUIRED: {selected_customer.name} (PI #{pi.id})",
                            "", "proforma@oblutools.com", to_emails, cc=cc_emails
                        )
                        msg.attach_alternative(html_content, "text/html")
                        msg.send(fail_silently=True)
                    except Exception as e:
                        logger.error(f"Credit Alert Mail Error: {e}")

        # 3. Item Conversion & Stock Auditing
        shortage_found = False
        shortage_list = []

        for item in quotation.items.all():
            ProformaInvoiceItem.objects.create(
                invoice=pi,
                product=item.product,
                quantity=item.quantity,
                requested_price=item.requested_price,
                current_price=item.current_price,
            )

            fresh_item = InventoryItem.objects.filter(id=item.product_id).values('quantity').first()
            available_stock = fresh_item['quantity'] if fresh_item and fresh_item['quantity'] is not None else 0

            if item.quantity > available_stock:
                shortage_found = True
                shortage_list.append({
                    'product': item.product,
                    'requested': item.quantity,
                    'available': available_stock,
                })

        # 4. Mandatory Stock Shortage Request Generation
        if shortage_found:
            for shortage in shortage_list:
                ProformaStockShortageRequest.objects.create(
                    invoice=pi,
                    product=shortage['product'],
                    requested_quantity=shortage['requested'],
                    available_quantity=shortage['available'],
                    requested_by=request.user,
                    status='pending'
                )

            try:
                to_emails = ["accounts@obluhc.com"]
                cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                if request.user.email:
                    cc_emails.append(request.user.email)

                email_context = {
                    "invoice": pi,
                    "requested_by": request.user,
                    "shortage_details": [
                        {'product_obj': s['product'], 'name': s['product'].name, 'requested': s['requested'], 'available': s['available']}
                        for s in shortage_list
                    ],
                    "review_url": "https://oblutools.com/proforma/stock-requests/",
                }
                html_content = render_to_string("proforma_invoice/stock_request_email.html", email_context)
                msg = EmailMultiAlternatives(f"📦 Stock Shortage Request (PI #{pi.id})", "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=True)
            except Exception as e:
                logger.error(f"Stock Alert Mail Error: {e}")

        # 5. Conversion Wrap-up & Locking
        needs_lock = (shortage_found or actual_credit_req_created)
        if needs_lock and not request.user.is_superuser:
            pi.is_price_altered = True
            pi.save()

        quotation.is_converted_to_proforma = True
        quotation.converted_at = timezone.now()
        quotation.save()

    if actual_credit_req_created or shortage_found:
        reasons = []
        if actual_credit_req_created:
            reasons.append("Credit Overdue")
        if shortage_found:
            reasons.append("Stock Shortage")
        messages.warning(request, f"🔒 Converted to PI #{pi.id}, but LOCKED for required approvals: {', '.join(reasons)}.")
        return redirect('proforma_list')

    messages.success(request, f"✅ Quotation converted to PI #{pi.id} successfully.")
    return redirect('proforma_detail', pk=pi.pk)


class QuotationRequestDetailsApiView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def get_value(self, obj, field_name):
        val = getattr(obj, field_name, None)
        return float(val) if val is not None else 0.0

    def get(self, request, quotation_id, *args, **kwargs):
        quotation = get_object_or_404(QuotationMaker, id=quotation_id)

        # RBAC Object Security
        if not can(request.user, "proforma.view", quotation):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        all_requests = ProformaPriceChangeRequest.objects.filter(
            quotation=quotation
        ).select_related('product', 'reviewed_by').order_by('-created_at')

        product_list = []
        courier_list = []

        for req in all_requests:
            if req.is_product_request:
                if req.product:
                    product_list.append({
                        'name': req.product.name,
                        'requested_price': self.get_value(req, 'requested_price'),
                        'msrp': self.get_value(req, 'msrp_snapshot') or self.get_value(req.product, 'msrp'),
                        'status': req.status.upper(),
                        'reason': req.reason or "N/A",
                        'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending",
                    })
            else:
                courier_list.append({
                    'requested': self.get_value(req, 'requested_courier_charge'),
                    'recommended': self.get_value(req, 'recommended_courier_charge'),
                    'status': req.status.upper(),
                    'reason': req.reason or "N/A",
                    'reviewed_by': req.reviewed_by.username if req.reviewed_by else "Pending",
                    'date': req.created_at.strftime('%d %b %Y')
                })

        data = {
            'id': quotation.id,
            'customer_name': quotation.customer.name,
            'products': product_list,
            'courier_history': courier_list
        }

        return JsonResponse(data)

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils import timezone

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils.timezone import localtime


# ============================================================================
# DOCUMENT ITEM EDITS, REMARKS & MODAL APIS (RBAC ALIGNED)
# ============================================================================

class ManageInvoiceRemarkView(RBACViewMixin, View):
    rbac_permission = "proforma.view"

    def get_object(self, pk, obj_type):
        """Helper to get either Quotation or Proforma"""
        if obj_type == "Quotation":
            return get_object_or_404(QuotationMaker, id=pk)
        return get_object_or_404(ProformaInvoice, id=pk)

    def get(self, request, pk, *args, **kwargs):
        """Used to load the chat history"""
        obj_type = request.GET.get('obj_type')
        obj = self.get_object(pk, obj_type)

        # RBAC Object Security
        if not can(request.user, "proforma.view", obj):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        return self._get_remarks_response(obj)

    def post(self, request, pk, *args, **kwargs):
        obj_type = request.POST.get('obj_type')
        text = request.POST.get('remark')

        if not text:
            return JsonResponse({'status': 'error', 'message': 'Empty remark'}, status=400)

        obj = self.get_object(pk, obj_type)

        # RBAC Object Security
        if not can(request.user, "proforma.view", obj):
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

        from .models import ProformaRemark
        if obj_type == "Quotation":
            new_remark = ProformaRemark.objects.create(
                user=request.user,
                remark=text,
                quotation=obj,
                invoice=None
            )
        else:
            new_remark = ProformaRemark.objects.create(
                user=request.user,
                remark=text,
                invoice=obj,
                quotation=None
            )

        return JsonResponse({'status': 'ok'})

    def _get_remarks_response(self, obj):
        remarks_qs = obj.remarks.select_related("user").all().order_by('created_at')
        remarks_list = []

        for r in remarks_qs:
            emp = getattr(r.user, "employee_profile", None)
            u_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []

            # Dynamic CSS badge role resolution
            if r.user.is_superuser or "admin" in u_roles:
                role = "admin"
            elif getattr(r.user, 'is_accountant', False) or any(rk in u_roles for rk in ['accounts_operations', 'business', 'sales_head']):
                role = "accounts"
            else:
                role = "sales"

            remarks_list.append({
                'user': r.user.username,
                'text': r.remark,
                'role': role,
                'time': localtime(r.created_at).strftime('%d %b, %H:%M')
            })

        return JsonResponse({'remarks': remarks_list})


class DeleteDocumentItemView(RBACViewMixin, View):
    """
    Deletes an individual item row.
    Locks down immediately if PI is dispatched or Quote is converted.
    """
    rbac_permission = "proforma.create"  # <-- Only authorized creators/managers can edit items

    def post(self, request, doc_type, item_id, *args, **kwargs):
        if doc_type == "proforma":
            item = get_object_or_404(ProformaInvoiceItem.objects.select_related("invoice", "product"), id=item_id)
            parent = item.invoice
            if parent.dispatch_status == 'dispatched':
                messages.error(request, "❌ Cannot modify a dispatched Proforma Invoice.")
                return redirect("proforma_detail", pk=parent.id)
            redirect_url = "proforma_detail"

        elif doc_type == "quotation":
            item = get_object_or_404(QuotationMakerItem.objects.select_related("quotation", "product"), id=item_id)
            parent = item.quotation
            if parent.is_converted_to_proforma:
                messages.error(request, "❌ Cannot modify a converted Quotation.")
                return redirect("quotation_detail", pk=parent.id)
            redirect_url = "quotation_detail"
        else:
            messages.error(request, "Invalid document type.")
            return redirect("home")

        # RBAC Object Security
        if not can(request.user, "proforma.view", parent):
            messages.error(request, "Unauthorized to modify this document.")
            return redirect(redirect_url, pk=parent.id)

        if parent.items.count() <= 1:
            messages.error(request, "❌ You cannot delete the only item. The document must contain at least one item.")
            return redirect(redirect_url, pk=parent.id)

        product_name = item.product.name
        qty = item.quantity

        with transaction.atomic():
            if doc_type == "proforma":
                parent.price_requests.filter(product=item.product, status="pending").delete()
                parent.stock_requests.filter(product=item.product, status="pending").delete()
            else:
                parent.price_requests.filter(product=item.product, status="pending").delete()

            DocumentEditLog.objects.create(
                invoice=parent if doc_type == "proforma" else None,
                quotation=parent if doc_type == "quotation" else None,
                user=request.user,
                action="item_deleted",
                product_name=product_name,
                description=f"Removed item: '{product_name}' (Qty: {qty})"
            )

            parent.is_edited = True
            parent.last_edited_at = timezone.now()
            parent.save()

            item.delete()

        messages.success(request, f"Item '{product_name}' deleted successfully.")
        return redirect(redirect_url, pk=parent.id)


class AddItemToDocumentView(RBACViewMixin, View):
    rbac_permission = "proforma.create"  # <-- Only authorized creators/managers can add items

    def post(self, request, doc_type, doc_id, *args, **kwargs):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('is_ajax') == '1'

        def return_error(msg):
            if is_ajax:
                return JsonResponse({"status": "error", "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("proforma_list" if doc_type == "proforma" else "quotation_list")

        product_id = request.POST.get("product_id")
        try:
            quantity = int(request.POST.get("quantity", 1))
            if quantity <= 0:
                quantity = 1
        except (ValueError, TypeError):
            quantity = 1

        product = get_object_or_404(
            InventoryItem.objects.select_related("category", "proforma_price"),
            id=product_id
        )

        pricing_config = getattr(product, "proforma_price", None)
        if pricing_config and quantity < pricing_config.min_requirement:
            return return_error(
                f"❌ Minimum Order Requirement: '{product.name}' requires a minimum quantity of "
                f"{pricing_config.min_requirement} units (you entered {quantity})."
            )

        if doc_type == "proforma":
            parent = get_object_or_404(
                ProformaInvoice.objects.prefetch_related("items__product__category"),
                id=doc_id
            )
            if parent.dispatch_status == 'dispatched':
                return return_error("❌ This Proforma Invoice is already dispatched and cannot be modified.")

            # RBAC Object Security
            if not can(request.user, "proforma.view", parent):
                return return_error("Unauthorized to modify this document.")

            RESTRICTED_SHEET_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]
            existing_items = list(parent.items.all())

            current_sheet_qty = sum(
                item.quantity for item in existing_items
                if item.product.category and item.product.category.name.upper() in RESTRICTED_SHEET_CATEGORIES
            )
            cat_name = product.category.name.upper() if product.category else ""
            is_sheet_item = cat_name in RESTRICTED_SHEET_CATEGORIES
            projected_sheet_qty = current_sheet_qty + (quantity if is_sheet_item else 0)

            has_resin = any(
                "RESIN" in (item.product.category.name.upper() if item.product.category else "")
                for item in existing_items
            ) or ("RESIN" in cat_name)

            if parent.courier_mode == "surface" and 0 < projected_sheet_qty < 200:
                needed_qty = 200 - current_sheet_qty
                return return_error(
                    f"❌ Courier Rule Blocked (Surface Mode):\n"
                    f"Thermoforming Sheets / Bay Materials require at least 200 units for Surface shipping.\n"
                    f"Current Sheets: {current_sheet_qty}\n"
                    f"You must add at least {needed_qty} more sheet(s) to reach 200 units, or switch courier mode to Air."
                )

            if parent.courier_mode == "air" and has_resin:
                return return_error(
                    f"❌ Safety Rule Blocked (Air Mode):\n"
                    f"Resin products ('{product.name}') are flammable/hazardous and cannot be sent by Air.\n"
                    f"Please switch the shipping mode to Surface."
                )

            with transaction.atomic():
                fresh_product = InventoryItem.objects.select_for_update().get(id=product.id)
                available_stock = fresh_product.quantity if fresh_product.quantity is not None else 0
                has_stock_issue = quantity > available_stock

                has_credit_issue = False
                if not request.user.is_superuser:
                    all_overdue = CustomerVoucherStatus.objects.filter(
                        customer=parent.customer,
                        is_credit_period_crossed=True
                    ).filter(Q(is_unpaid=True) | Q(is_partially_paid=True))

                    if all_overdue.exists():
                        overdue_ids = all_overdue.values_list('voucher_id', flat=True)
                        vouchers_with_emi = VoucherEmiPaymentAllocation.objects.filter(
                            voucher__voucher_id__in=overdue_ids
                        ).values_list('voucher__voucher_id', flat=True).distinct()
                        if all_overdue.exclude(voucher_id__in=vouchers_with_emi).exists():
                            has_credit_issue = True

                item = ProformaInvoiceItem.objects.create(
                    invoice=parent,
                    product=product,
                    quantity=quantity
                )

                DocumentEditLog.objects.create(
                    invoice=parent,
                    quotation=None,
                    user=request.user,
                    action="item_added",
                    product_name=product.name,
                    description=f"Added item: '{product.name}' (Qty: {quantity})"
                )

                parent.is_edited = True
                parent.last_edited_at = timezone.now()

                if has_credit_issue:
                    CreditPeriodOverdueByPassRequest.objects.get_or_create(
                        customer=parent.customer,
                        proforma_invoice=parent,
                        requested_by=request.user,
                        defaults={'status': 'pending'}
                    )

                if has_stock_issue:
                    ProformaStockShortageRequest.objects.create(
                        invoice=parent,
                        product=product,
                        requested_quantity=quantity,
                        available_quantity=available_stock,
                        requested_by=request.user,
                        status="pending"
                    )

                    try:
                        to_emails = ["accounts@obluhc.com"]
                        cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                        if request.user.email:
                            cc_emails.append(request.user.email)

                        shortage_details = [{
                            'product_obj': product,
                            'name': product.name,
                            'requested': quantity,
                            'available': available_stock
                        }]

                        email_context = {
                            "invoice": parent,
                            "requested_by": request.user,
                            "shortage_details": shortage_details,
                            "review_url": "https://oblutools.com/proforma/stock-requests/",
                        }
                        html_content = render_to_string("proforma_invoice/stock_request_email.html", email_context)
                        msg = EmailMultiAlternatives(
                            f"📦 Stock Shortage Alert: PI #{parent.id} - {product.name}",
                            "",
                            "proforma@oblutools.com",
                            to_emails,
                            cc=cc_emails
                        )
                        msg.attach_alternative(html_content, "text/html")
                        msg.send(fail_silently=True)
                    except Exception as mail_err:
                        logger.error(f"Stock Shortage Alert Mail Error: {mail_err}")

                if (has_stock_issue or has_credit_issue) and not request.user.is_superuser:
                    parent.is_price_altered = True

                parent.save()

            reasons = []
            if has_stock_issue:
                reasons.append(f"Stock Shortage (Available: {available_stock})")
            if has_credit_issue:
                reasons.append("Customer Credit Overdue")

            if reasons:
                msg = f"⚠️ Added '{product.name}' (Qty: {quantity}), but PI locked for: {', '.join(reasons)}."
                if is_ajax:
                    return JsonResponse({"status": "ok", "message": msg, "locked": True})
                messages.warning(request, msg)
                return redirect("proforma_list")

            success_msg = f"✅ Added '{product.name}' (Qty: {quantity}) successfully."
            if is_ajax:
                return JsonResponse({"status": "ok", "message": success_msg, "locked": False})

            messages.success(request, success_msg)
            return redirect("proforma_detail", pk=parent.id)

        elif doc_type == "quotation":
            parent = get_object_or_404(
                QuotationMaker.objects.prefetch_related("items__product__category"),
                id=doc_id
            )
            if parent.is_converted_to_proforma:
                return return_error("❌ This Quotation has already been converted to a Proforma Invoice.")

            # RBAC Object Security
            if not can(request.user, "proforma.view", parent):
                return return_error("Unauthorized to modify this document.")

            RESTRICTED_SHEET_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]
            existing_items = list(parent.items.all())

            current_sheet_qty = sum(
                item.quantity for item in existing_items
                if item.product.category and item.product.category.name.upper() in RESTRICTED_SHEET_CATEGORIES
            )
            cat_name = product.category.name.upper() if product.category else ""
            is_sheet_item = cat_name in RESTRICTED_SHEET_CATEGORIES
            projected_sheet_qty = current_sheet_qty + (quantity if is_sheet_item else 0)

            has_resin = any(
                "RESIN" in (item.product.category.name.upper() if item.product.category else "")
                for item in existing_items
            ) or ("RESIN" in cat_name)

            if parent.courier_mode == "surface" and 0 < projected_sheet_qty < 200:
                needed_qty = 200 - current_sheet_qty
                return return_error(
                    f"❌ Courier Rule Blocked (Surface Mode):\n"
                    f"Thermoforming Sheets / Bay Materials require at least 200 units for Surface shipping.\n"
                    f"Current Sheets: {current_sheet_qty}\n"
                    f"You must add at least {needed_qty} more sheet(s) to reach 200 units, or switch courier mode to Air."
                )

            if parent.courier_mode == "air" and has_resin:
                return return_error(
                    f"❌ Safety Rule Blocked (Air Mode):\n"
                    f"Resin products ('{product.name}') cannot be transported by Air.\n"
                    f"Please switch the courier mode to Surface."
                )

            with transaction.atomic():
                QuotationMakerItem.objects.create(
                    quotation=parent,
                    product=product,
                    quantity=quantity
                )

                DocumentEditLog.objects.create(
                    invoice=None,
                    quotation=parent,
                    user=request.user,
                    action="item_added",
                    product_name=product.name,
                    description=f"Added item: '{product.name}' (Qty: {quantity})"
                )

                parent.is_edited = True
                parent.last_edited_at = timezone.now()
                parent.save()

            success_msg = f"✅ Added '{product.name}' (Qty: {quantity}) to Quotation."
            if is_ajax:
                return JsonResponse({"status": "ok", "message": success_msg})

            messages.success(request, success_msg)
            return redirect("quotation_detail", pk=parent.id)

        return redirect("home")


class DocumentEditHistoryApiView(RBACViewMixin, View):
    """Returns audit details for list view popups/modals."""
    rbac_permission = "proforma.view"

    def get(self, request, doc_type, doc_id, *args, **kwargs):
        if doc_type == "proforma":
            parent = get_object_or_404(ProformaInvoice, id=doc_id)
        elif doc_type == "quotation":
            parent = get_object_or_404(QuotationMaker, id=doc_id)
        else:
            return JsonResponse({"status": "error", "message": "Invalid type"}, status=400)

        # RBAC Object Security
        if not can(request.user, "proforma.view", parent):
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)

        logs = parent.edit_logs.select_related("user").all()
        data = [
            {
                "action": log.get_action_display(),
                "product_name": log.product_name,
                "description": log.description,
                "user": log.user.username,
                "timestamp": timezone.localtime(log.created_at).strftime("%d %b %Y, %H:%M")
            }
            for log in logs
        ]
        return JsonResponse({"status": "ok", "doc_id": doc_id, "logs": data})


class DocumentItemsApiView(RBACViewMixin, View):
    """Returns current active items of a document for modal editing."""
    rbac_permission = "proforma.view"

    def get(self, request, doc_type, doc_id, *args, **kwargs):
        if doc_type == "proforma":
            parent = get_object_or_404(ProformaInvoice, id=doc_id)
            is_locked = parent.dispatch_status == 'dispatched'
        elif doc_type == "quotation":
            parent = get_object_or_404(QuotationMaker, id=doc_id)
            is_locked = parent.is_converted_to_proforma
        else:
            return JsonResponse({"status": "error", "message": "Invalid type"}, status=400)

        # RBAC Object Security
        if not can(request.user, "proforma.view", parent):
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)

        items_data = []
        for item in parent.items.select_related("product").all():
            items_data.append({
                "item_id": item.id,
                "product_id": item.product.id,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "unit_price": float(item.get_unit_price_incl_tax()),
                "total_price": float(item.total_price())
            })

        return JsonResponse({
            "status": "ok",
            "doc_id": doc_id,
            "doc_type": doc_type,
            "is_locked": is_locked,
            "items": items_data
        })

# ============================================================================
# MODAL DOCUMENT ITEMS & PRODUCT SEARCH APIS (RBAC ALIGNED)
# ============================================================================

class DocumentItemsApiView(RBACViewMixin, View):
    """Returns current active items of a document for modal editing."""
    rbac_permission = "proforma.view"

    def get(self, request, doc_type, doc_id, *args, **kwargs):
        if doc_type == "proforma":
            parent = get_object_or_404(ProformaInvoice, id=doc_id)
            is_locked = parent.dispatch_status == 'dispatched'
        elif doc_type == "quotation":
            parent = get_object_or_404(QuotationMaker, id=doc_id)
            is_locked = parent.is_converted_to_proforma
        else:
            return JsonResponse({"status": "error", "message": "Invalid type"}, status=400)

        # RBAC Object Security: Verify user has access to view this document
        if not can(request.user, "proforma.view", parent):
            return JsonResponse({"status": "error", "message": "Unauthorized"}, status=403)

        items_data = []
        for item in parent.items.select_related("product").all():
            items_data.append({
                "item_id": item.id,
                "product_id": item.product.id,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "unit_price": float(item.get_unit_price_incl_tax()),
                "total_price": float(item.total_price())
            })

        return JsonResponse({
            "status": "ok",
            "doc_id": doc_id,
            "doc_type": doc_type,
            "is_locked": is_locked,
            "items": items_data
        })


class SearchInventoryProductApiView(RBACViewMixin, View):
    """Searches active products for the modal dropdown (Accessible to all except HR)."""
    rbac_permission = "product_price.view"

    def get(self, request, *args, **kwargs):
        query = request.GET.get('q', '').strip()
        items = (
            InventoryItem.objects
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
        )
        if query:
            items = items.filter(name__icontains=query)

        results = [
            {"id": item.id, "name": item.name}
            for item in items.order_by("name")[:30]
        ]
        return JsonResponse({"results": results})
# dispatch management views

from .models import (
DispatchRequest,
    ShipmentMethod,
    DispatchInvoice,
    DispatchRemark,
    DispatchStateHistory,
    DispatchPhoto,
    WarehouseDispatch,
    DocketPhoto
)

# ============================================================================
# DISPATCH PIPELINE & WAREHOUSE DASHBOARDS (RBAC ALIGNED)
# ============================================================================

from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import get_object_or_404, redirect
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from access_control.mixins import RBACViewMixin
from access_control.services import can

# =========================================================================
# RBAC ROLE RESOLUTION HELPERS
# =========================================================================
def is_accounts_user(user):
    if not user.is_authenticated:
        return False
    emp = getattr(user, "employee_profile", None)
    user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []
    return bool(
        user.is_superuser
        or getattr(user, "is_accountant", False)
        or any(r in user_roles for r in ["admin", "accounts", "operations",])
        or user.username.lower() in ["accounts", "accountant", "finance"]
        or can(user, "dispatch.queue")
    )

def is_warehouse_user(user):
    if not user.is_authenticated:
        return False
    emp = getattr(user, "employee_profile", None)
    user_roles = [a.role.role_key for a in emp.role_assignments.filter(is_active=True)] if emp else []
    return bool(
        user.is_superuser
        or "warehouse" in user_roles
        or user.username.lower() == "warehouse"
    )


# =========================================================================
# STATE HELPER
# =========================================================================
def move_dispatch_status(dispatch, new_status, user):
    old_status = dispatch.status
    dispatch.status = new_status
    dispatch.save()

    DispatchStateHistory.objects.create(
        dispatch_request=dispatch,
        from_status=old_status,
        to_status=new_status,
        changed_by=user
    )


# =========================================================================
# DASHBOARDS & DETAIL VIEWS (PURE RBAC)
# =========================================================================
class AccountsDispatchDashboardView(RBACViewMixin, TemplateView):
    template_name = "dispatch/accounts_dashboard.html"
    rbac_permission = "dispatch.queue"

    def dispatch(self, request, *args, **kwargs):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can access this dashboard.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dispatches = (
            DispatchRequest.objects
            .select_related("invoice", "invoice__customer", "shipment_method")
            .order_by("-created_at")
        )

        for dispatch in dispatches:
            latest_history = dispatch.history.order_by("-changed_at").first()
            if latest_history:
                dispatch.time_in_stage = timezone.now() - latest_history.changed_at
            else:
                dispatch.time_in_stage = timezone.now() - dispatch.created_at

        context["dispatches"] = dispatches
        context["is_accountant"] = is_accounts_user(self.request.user)
        return context


class WarehouseDispatchDashboardView(RBACViewMixin, TemplateView):
    template_name = "dispatch/warehouse_dashboard.html"
    rbac_permission = "proforma.dispatch"

    def dispatch(self, request, *args, **kwargs):
        if not (is_warehouse_user(request.user) or is_accounts_user(request.user)):
            raise PermissionDenied("Only Warehouse, Accounts, and Super Admin can access this dashboard.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dispatches = (
            DispatchRequest.objects
            .filter(status__in=["waiting_for_packing", "packing_approved"])
            .select_related("invoice", "invoice__customer", "shipment_method")
            .order_by("-created_at")
        )

        for dispatch in dispatches:
            latest_history = dispatch.history.order_by("-changed_at").first()
            if latest_history:
                dispatch.time_in_stage = timezone.now() - latest_history.changed_at
            else:
                dispatch.time_in_stage = timezone.now() - dispatch.created_at

        context["dispatches"] = dispatches
        context["is_warehouse"] = is_warehouse_user(self.request.user)
        return context


class DispatchDetailView(RBACViewMixin, TemplateView):
    template_name = "dispatch/detail.html"
    rbac_permission = "proforma.dispatch"

    def dispatch(self, request, *args, **kwargs):
        if not (is_accounts_user(request.user) or is_warehouse_user(request.user)):
            raise PermissionDenied("You do not have permission to view this dispatch detail.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dispatch = get_object_or_404(
            DispatchRequest.objects.select_related(
                "invoice",
                "invoice__customer",
                "shipment_method"
            ),
            pk=self.kwargs["pk"]
        )

        context["dispatch"] = dispatch
        context["photos"] = dispatch.photos.all().order_by("-uploaded_at")
        context["remarks"] = dispatch.remarks.all()
        context["history"] = dispatch.history.all()

        try:
            context["invoice_file"] = dispatch.invoice_file
        except:
            context["invoice_file"] = None

        try:
            context["warehouse_dispatch"] = dispatch.warehousedispatch
        except:
            context["warehouse_dispatch"] = None

        if context["warehouse_dispatch"]:
            context["docket_photos"] = (
                DocketPhoto.objects
                .filter(warehouse_dispatch=context["warehouse_dispatch"])
                .order_by("-uploaded_at")
            )
        else:
            context["docket_photos"] = []

        context["shipment_methods"] = (
            ShipmentMethod.objects
            .filter(is_active=True)
            .order_by("name")
        )

        # Dynamic RBAC roles passed to template
        context["is_accountant"] = is_accounts_user(self.request.user)
        context["is_warehouse"] = is_warehouse_user(self.request.user)
        return context


# =========================================================================
# WORKFLOW TRANSITION ACTIONS (PURE RBAC)
# =========================================================================

class NotifyWarehouseView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can notify the warehouse.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        # Supports both requested and initiated states
        if dispatch.status not in ["requested", "initiated"]:
            messages.error(request, "This dispatch is not in Requested or Initiated state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        invoice_number = request.POST.get("invoice_number")
        shipment_method_id = request.POST.get("shipment_method")
        invoice_pdf = request.FILES.get("invoice_pdf")

        if not invoice_number:
            messages.error(request, "Invoice Number is required.")
            return redirect("dispatch_detail", pk=dispatch.id)

        if not shipment_method_id:
            messages.error(request, "Shipment Method is required.")
            return redirect("dispatch_detail", pk=dispatch.id)

        if not invoice_pdf:
            messages.error(request, "Invoice PDF is required.")
            return redirect("dispatch_detail", pk=dispatch.id)

        shipment_method = get_object_or_404(ShipmentMethod, pk=shipment_method_id)

        dispatch.invoice_number = invoice_number
        dispatch.shipment_method = shipment_method
        dispatch.save()

        DispatchInvoice.objects.update_or_create(
            dispatch_request=dispatch,
            defaults={"pdf": invoice_pdf, "uploaded_by": request.user}
        )

        old_status = dispatch.status
        dispatch.status = "waiting_for_packing"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            from_status=old_status,
            to_status="waiting_for_packing",
            changed_by=request.user
        )

        try:
            warehouse_emails = ["warehouse@obluhc.com"]
            cc_emails = ["abhijay.obluhc@gmail.com", "operations@obluhc.com", "accounts@obluhc.com"]
            if request.user.email and request.user.email not in cc_emails:
                cc_emails.append(request.user.email)

            subject = f"📦 Packing Required (PI #{dispatch.invoice.id}) - {dispatch.invoice.customer.name}"
            html = render_to_string("dispatch/warehouse_notification.html", {"dispatch": dispatch})
            msg = EmailMultiAlternatives(
                subject=subject,
                body="Packing Required",
                from_email="proforma@oblutools.com",  # Fixed domain
                to=warehouse_emails,
                cc=cc_emails  # Added CC
            )
            msg.attach_alternative(html, "text/html")
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Warehouse notification email error: {e}")


class WarehousePackingSubmitView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_warehouse_user(request.user):
            raise PermissionDenied("Only Warehouse staff can submit packing photos.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "waiting_for_packing":
            messages.error(request, "Invalid dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        remark = request.POST.get("remark", "")
        photos = request.FILES.getlist("photos")

        if not photos:
            messages.error(request, "Please upload packing photos.")
            return redirect("dispatch_detail", pk=dispatch.id)

        for photo in photos:
            DispatchPhoto.objects.create(
                dispatch_request=dispatch,
                image=photo,
                uploaded_by=request.user
            )

        if remark:
            DispatchRemark.objects.create(
                dispatch_request=dispatch,
                user=request.user,
                message=remark
            )

        old_status = dispatch.status
        dispatch.status = "packed_awaiting_approval"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            from_status=old_status,
            to_status="packed_awaiting_approval",
            changed_by=request.user
        )

        # Send Notification to Accounts + CC
        try:
            to_emails = ["accounts@obluhc.com"]
            cc_emails = ["abhijay.obluhc@gmail.com", "operations@obluhc.com"]
            if request.user.email and request.user.email not in cc_emails:
                cc_emails.append(request.user.email)

            subject = f"📦 Packing Submitted for Review (PI #{dispatch.invoice.id}) - {dispatch.invoice.customer.name}"
            body_text = f"Packing photos have been submitted by the warehouse for PI #{dispatch.invoice.id}.\n\nPlease review and approve: https://oblutools.com/proforma/dispatch/{dispatch.id}/"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email="proforma@oblutools.com",
                to=to_emails,
                cc=cc_emails
            )
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Packing submission email error: {e}")

class ApprovePackingView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can approve packing.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "packed_awaiting_approval":
            messages.error(request, "Invalid dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        old_status = dispatch.status
        dispatch.status = "packing_approved"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            from_status=old_status,
            to_status="packing_approved",
            changed_by=request.user
        )

        # Send Approval Notification to Warehouse + CC
        try:
            to_emails = ["warehouse@obluhc.com"]
            cc_emails = ["abhijay.obluhc@gmail.com", "operations@obluhc.com",
                         "accounts@obluhc.com"]

            subject = f"✅ Packing Approved (PI #{dispatch.invoice.id}) - Ready for Carrier Dispatch"
            body_text = f"Packing has been approved for PI #{dispatch.invoice.id}.\nWarehouse may now hand over to carrier and enter docket number: https://oblutools.com/proforma/dispatch/{dispatch.id}/"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email="proforma@oblutools.com",
                to=to_emails,
                cc=cc_emails
            )
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Packing approved email error: {e}")

        messages.success(request, "Packing approved.")
        return redirect("dispatch_detail", pk=dispatch.id)


class RejectPackingView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can reject packing.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "packed_awaiting_approval":
            messages.error(request, "Invalid dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        rejection_remark = request.POST.get("rejection_remark", "")

        if rejection_remark:
            DispatchRemark.objects.create(
                dispatch_request=dispatch,
                user=request.user,
                message=f"PACKING REJECTED: {rejection_remark}"
            )

        old_status = dispatch.status
        dispatch.status = "waiting_for_packing"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            from_status=old_status,
            to_status="waiting_for_packing",
            changed_by=request.user
        )

        # Send Rejection Notification to Warehouse + CC
        try:
            to_emails = ["warehouse@obluhc.com"]
            cc_emails = ["abhijay.obluhc@gmail.com", "accounts@obluhc.com",
                         "operations@obluhc.com"]

            subject = f"❌ Packing Rejected (PI #{dispatch.invoice.id}) - Re-packing Required"
            body_text = f"Packing for PI #{dispatch.invoice.id} was rejected by Accounts.\n\nReason:\n{rejection_remark}\n\nPlease check: https://oblutools.com/proforma/dispatch/{dispatch.id}/"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email="proforma@oblutools.com",
                to=to_emails,
                cc=cc_emails
            )
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Packing rejection email error: {e}")

        messages.warning(request, "Packing rejected.")
        return redirect("dispatch_detail", pk=dispatch.id)


class WarehouseDispatchView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_warehouse_user(request.user):
            raise PermissionDenied("Only Warehouse staff can complete carrier dispatch.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "packing_approved":
            messages.error(request, "Invalid dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        docket_number = request.POST.get("docket_number", "").strip()
        docket_photos = request.FILES.getlist("docket_photos")

        if not docket_number:
            messages.error(request, "Docket number is required.")
            return redirect("dispatch_detail", pk=dispatch.id)

        if not docket_photos:
            messages.error(request, "Please upload at least one docket photo.")
            return redirect("dispatch_detail", pk=dispatch.id)

        warehouse_dispatch, created = WarehouseDispatch.objects.update_or_create(
            dispatch_request=dispatch,
            defaults={
                "docket_number": docket_number,
                "dispatched_at": timezone.now(),
                "dispatched_by": request.user,
            }
        )

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            event_type="docket",
            from_status="",
            to_status=docket_number,
            changed_by=request.user
        )

        for photo in docket_photos:
            DocketPhoto.objects.create(
                warehouse_dispatch=warehouse_dispatch,
                image=photo,
                uploaded_by=request.user
            )

        old_status = dispatch.status
        dispatch.status = "dispatched_by_warehouse"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            event_type="status",
            from_status=old_status,
            to_status="dispatched_by_warehouse",
            changed_by=request.user
        )

        # Send Carrier Handover Notification to Accounts + CC
        try:
            to_emails = ["accounts@obluhc.com"]
            cc_emails = ["abhijay.obluhc@gmail.com", "operations@obluhc.com"]

            subject = f"🚚 Order Dispatched by Warehouse (PI #{dispatch.invoice.id}) - Docket: {docket_number}"
            body_text = f"PI #{dispatch.invoice.id} has been handed over to carrier.\nDocket Number: {docket_number}\n\nView details: https://oblutools.com/proforma/dispatch/{dispatch.id}/"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email="proforma@oblutools.com",
                to=to_emails,
                cc=cc_emails
            )
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Dispatch marked email error: {e}")

        messages.success(request, "Dispatch marked successfully.")
        return redirect("dispatch_detail", pk=dispatch.id)


class EditDocketNumberView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can edit the docket number.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "dispatched_by_warehouse":
            messages.error(request, "Docket cannot be edited in the current dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        warehouse_dispatch = get_object_or_404(WarehouseDispatch, dispatch_request=dispatch)
        new_docket_number = request.POST.get("docket_number", "").strip()

        if not new_docket_number:
            messages.error(request, "Docket number cannot be empty.")
            return redirect("dispatch_detail", pk=dispatch.id)

        old_docket_number = warehouse_dispatch.docket_number
        if old_docket_number == new_docket_number:
            messages.info(request, "Docket number is unchanged.")
            return redirect("dispatch_detail", pk=dispatch.id)

        warehouse_dispatch.docket_number = new_docket_number
        warehouse_dispatch.save(update_fields=["docket_number"])

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            event_type="docket",
            from_status=old_docket_number,
            to_status=new_docket_number,
            changed_by=request.user
        )

        messages.success(request, "Docket number updated successfully.")
        return redirect("dispatch_detail", pk=dispatch.id)


class CompleteDispatchView(RBACViewMixin, View):
    rbac_permission = "proforma.dispatch"

    def post(self, request, pk):
        if not is_accounts_user(request.user):
            raise PermissionDenied("Only Accounts & Operations and Super Admin can complete dispatch.")

        dispatch = get_object_or_404(DispatchRequest, pk=pk)

        if dispatch.status != "dispatched_by_warehouse":
            messages.error(request, "Invalid dispatch state.")
            return redirect("dispatch_detail", pk=dispatch.id)

        old_status = dispatch.status
        dispatch.status = "completed"
        dispatch.save()

        DispatchStateHistory.objects.create(
            dispatch_request=dispatch,
            from_status=old_status,
            to_status="completed",
            changed_by=request.user
        )

        invoice = dispatch.invoice
        invoice.dispatch_status = "dispatched"
        invoice.dispatched_at = timezone.now()
        invoice.save()

        # Send Completion Notification to Salesperson + CC
        try:
            recipients = []
            if dispatch.requested_by and dispatch.requested_by.email:
                recipients.append(dispatch.requested_by.email)
            if not recipients:
                recipients = ["accounts@obluhc.com"]

            cc_emails = ["abhijay.obluhc@gmail.com","operations@obluhc.com",]

            subject = f"🎉 Order Fully Dispatched (PI #{invoice.id}) - {invoice.customer.name}"
            body_text = f"Your dispatch request for PI #{invoice.id} ({invoice.customer.name}) has been completed.\nDocket Number: {getattr(dispatch, 'warehouse_dispatch', None) and dispatch.warehousedispatch.docket_number or 'N/A'}"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body_text,
                from_email="proforma@oblutools.com",
                to=recipients,
                cc=cc_emails
            )
            msg.send(fail_silently=True)
        except Exception as e:
            logger.error(f"Complete dispatch email error: {e}")

        messages.success(request, "Dispatch completed successfully.")
        return redirect("dispatch_detail", pk=dispatch.id)

#price and courier tier management page
#price and courier tier management page

# ============================================================================
# MASTER PRODUCT PRICING & TIER MANAGEMENT (ACCOUNTS & SUPERUSER ONLY)
# ============================================================================

#price and courier tier management page
#price and courier tier management page

# ============================================================================
# MASTER PRODUCT PRICING & TIER MANAGEMENT (ACCOUNTS & SUPERUSER ONLY)
# ============================================================================

class AccountantProductTierManagerView(RBACViewMixin, ListView):
    model = InventoryItem
    template_name = "proforma_invoice/accountant_product_tier_manager.html"
    context_object_name = "items"
    paginate_by = 50
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def get_paginate_by(self, queryset):
        page_size = self.request.GET.get("page_size", "50").strip().lower()
        if page_size == "all":
            return None
        try:
            val = int(page_size)
            if val in [25, 50, 100, 200, 500]:
                return val
        except (ValueError, TypeError):
            pass
        return 50

    def get_queryset(self):
        qs = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related(
                Prefetch(
                    "proforma_price__price_tiers",
                    queryset=ProductPriceTier.objects.order_by("min_quantity")
                ),
                Prefetch(
                    "courier_sheets",
                    queryset=CourierCharge.objects.prefetch_related(
                        Prefetch("tiers", queryset=CourierChargeTier.objects.order_by("min_quantity"))
                    )
                )
            )
        )

        category_val = self.request.GET.get("category", "").strip()
        if category_val and category_val.lower() != "all":
            if category_val.isdigit():
                qs = qs.filter(category_id=int(category_val))
            else:
                qs = qs.filter(category__name__iexact=category_val)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(proforma_price__hsn__icontains=q))

        tier_status = self.request.GET.get("tier_status", "").strip()
        if tier_status == "has_tiers":
            qs = qs.filter(proforma_price__price_tiers__isnull=False).distinct()
        elif tier_status == "no_tiers":
            qs = qs.exclude(proforma_price__price_tiers__isnull=False)

        return qs.order_by("category__name", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = Category.objects.order_by("name")
        context["selected_category"] = self.request.GET.get("category", "all")
        context["search_query"] = self.request.GET.get("q", "")
        context["selected_tier_status"] = self.request.GET.get("tier_status", "all")
        context["page_size"] = self.request.GET.get("page_size", "50")

        items_on_page = context.get("items", [])
        products_data = {}
        for item in items_on_page:
            prod_price = getattr(item, "proforma_price", None)
            price_tiers = []
            if prod_price:
                for t in prod_price.price_tiers.all():
                    price_tiers.append({
                        "min_quantity": t.min_quantity,
                        "unit_price": float(t.unit_price) if t.unit_price is not None else 0.0,
                        "msrp": float(t.msrp) if t.msrp is not None else 0.0,
                    })

            surface_tiers = []
            air_tiers = []
            for s in item.courier_sheets.all():
                target = surface_tiers if s.mode == CourierMode.SURFACE else air_tiers
                for ct in s.tiers.all():
                    target.append({
                        "min_quantity": ct.min_quantity,
                        "max_quantity": ct.max_quantity,
                        "charge": float(ct.charge) if ct.charge is not None else 0.0,
                    })

            products_data[str(item.id)] = {
                "id": item.id,
                "name": item.name,
                "category": item.category.name if item.category else "Uncategorized",
                "categoryId": item.category_id,
                "basePrice": float(prod_price.price) if (prod_price and prod_price.price is not None) else 0.0,
                "msrp": float(prod_price.msrp) if (prod_price and prod_price.msrp is not None) else 0.0,
                "taxRate": float(prod_price.tax_rate) if (prod_price and prod_price.tax_rate is not None) else 0.0,
                "hsn": str(prod_price.hsn) if (prod_price and prod_price.hsn is not None) else "",
                "hasDynamic": bool(prod_price.has_dynamic_price) if prod_price else False,
                "priceTiers": price_tiers,
                "surfaceTiers": surface_tiers,
                "airTiers": air_tiers,
            }

        context["products_json"] = json.dumps(products_data)
        paginator = context.get("paginator")
        if paginator:
            context["total_matching_count"] = paginator.count
        else:
            context["total_matching_count"] = len(items_on_page)

        return context


class ProductTierDetailApiView(RBACViewMixin, View):
    """
    Returns single product pricing and courier tiers as JSON.
    """
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def get(self, request, product_id, *args, **kwargs):
        item = get_object_or_404(
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related(
                Prefetch("proforma_price__price_tiers", queryset=ProductPriceTier.objects.order_by("min_quantity")),
                Prefetch("courier_sheets", queryset=CourierCharge.objects.prefetch_related(
                    Prefetch("tiers", queryset=CourierChargeTier.objects.order_by("min_quantity"))
                ))
            ),
            id=product_id
        )

        prod_price = getattr(item, "proforma_price", None)
        price_tiers = []
        if prod_price:
            for t in prod_price.price_tiers.all():
                price_tiers.append({
                    "min_quantity": t.min_quantity,
                    "unit_price": float(t.unit_price) if t.unit_price is not None else 0.0,
                    "msrp": float(t.msrp) if t.msrp is not None else 0.0,
                })

        surface_tiers = []
        air_tiers = []
        for s in item.courier_sheets.all():
            target = surface_tiers if s.mode == CourierMode.SURFACE else air_tiers
            for ct in s.tiers.all():
                target.append({
                    "min_quantity": ct.min_quantity,
                    "max_quantity": ct.max_quantity,
                    "charge": float(ct.charge) if ct.charge is not None else 0.0,
                })

        data = {
            "id": item.id,
            "name": item.name,
            "category": item.category.name if item.category else "Uncategorized",
            "categoryId": item.category_id,
            "basePrice": float(prod_price.price) if (prod_price and prod_price.price is not None) else 0.0,
            "msrp": float(prod_price.msrp) if (prod_price and prod_price.msrp is not None) else 0.0,
            "taxRate": float(prod_price.tax_rate) if (prod_price and prod_price.tax_rate is not None) else 0.0,
            "hsn": str(prod_price.hsn) if (prod_price and prod_price.hsn is not None) else "",
            "hasDynamic": bool(prod_price.has_dynamic_price) if prod_price else False,
            "priceTiers": price_tiers,
            "surfaceTiers": surface_tiers,
            "airTiers": air_tiers,
        }
        return JsonResponse({"status": "success", "product": data})


class BulkUpdateProductTiersApiView(RBACViewMixin, View):
    """
    Bulk update/create product prices, tax, HSN, dynamic tiers, and courier slabs.
    """
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            category_id = payload.get("category_id")
            product_ids = payload.get("product_ids", [])
            apply_to_all_filtered = payload.get("apply_to_all_filtered", False)
            filter_category = payload.get("filter_category")
            filter_q = (payload.get("filter_q") or "").strip()
            filter_tier_status = (payload.get("filter_tier_status") or "").strip()

            base_price = payload.get("base_price")
            msrp = payload.get("msrp")
            tax_rate = payload.get("tax_rate")
            hsn = payload.get("hsn")
            tiers = payload.get("tiers", [])
            has_dynamic_price = payload.get("has_dynamic_price", True if tiers else False)

            if not category_id and not product_ids and not apply_to_all_filtered:
                return JsonResponse({"status": "error", "message": "Please select a Category, select products, or choose 'All Filtered'."}, status=400)

            qs = InventoryItem.objects.all()
            if apply_to_all_filtered:
                if filter_category and filter_category != "all":
                    if str(filter_category).isdigit():
                        qs = qs.filter(category_id=int(filter_category))
                    else:
                        qs = qs.filter(category__name__iexact=filter_category)
                if filter_q:
                    qs = qs.filter(Q(name__icontains=filter_q) | Q(proforma_price__hsn__icontains=filter_q))
                if filter_tier_status == "has_tiers":
                    qs = qs.filter(proforma_price__price_tiers__isnull=False).distinct()
                elif filter_tier_status == "no_tiers":
                    qs = qs.exclude(proforma_price__price_tiers__isnull=False)
            elif category_id:
                qs = qs.filter(category_id=category_id)
            elif product_ids:
                qs = qs.filter(id__in=product_ids)

            items_to_update = list(qs)
            if not items_to_update:
                return JsonResponse({"status": "error", "message": "No products matched the selection."}, status=404)

            count = 0
            with transaction.atomic():
                for item in items_to_update:
                    defaults = {"has_dynamic_price": has_dynamic_price}
                    if base_price is not None and str(base_price).strip() != "":
                        defaults["price"] = Decimal(str(base_price))
                    if msrp is not None and str(msrp).strip() != "":
                        defaults["msrp"] = Decimal(str(msrp))
                    if tax_rate is not None and str(tax_rate).strip() != "":
                        defaults["tax_rate"] = Decimal(str(tax_rate))
                    if hsn is not None and str(hsn).strip() != "":
                        defaults["hsn"] = Decimal(str(hsn))

                    prod_price, created = ProductPrice.objects.get_or_create(
                        product=item,
                        defaults={
                            "price": Decimal(str(base_price or 0)),
                            **defaults
                        }
                    )
                    if not created and defaults:
                        for k, v in defaults.items():
                            setattr(prod_price, k, v)
                        prod_price.save()

                    # Price tiers
                    if tiers:
                        prod_price.price_tiers.all().delete()
                        for t in tiers:
                            min_q = int(t.get("min_quantity", 1))
                            u_price = t.get("unit_price")
                            t_msrp = t.get("msrp")
                            tier_price = Decimal(str(u_price)) if (u_price is not None and str(u_price).strip() != "") else prod_price.price
                            tier_msrp = Decimal(str(t_msrp)) if (t_msrp is not None and str(t_msrp).strip() != "") else (prod_price.msrp or Decimal("0.00"))
                            ProductPriceTier.objects.create(
                                product=prod_price,
                                min_quantity=min_q,
                                unit_price=tier_price,
                                msrp=tier_msrp
                            )

                    # Courier charges
                    has_surface_data = any(t.get("surface_charge") is not None and str(t.get("surface_charge")).strip() != "" for t in tiers)
                    has_air_data = any(t.get("air_charge") is not None and str(t.get("air_charge")).strip() != "" for t in tiers)

                    if has_surface_data:
                        surface_charge, _ = CourierCharge.objects.get_or_create(
                            product=item,
                            mode=CourierMode.SURFACE
                        )
                        surface_charge.tiers.all().delete()
                        for t in tiers:
                            s_charge = t.get("surface_charge")
                            if s_charge is not None and str(s_charge).strip() != "":
                                min_q = int(t.get("min_quantity", 1))
                                max_q = t.get("max_quantity")
                                max_q_int = int(max_q) if (max_q is not None and str(max_q).strip() != "") else None
                                CourierChargeTier.objects.create(
                                    courier_product=surface_charge,
                                    min_quantity=min_q,
                                    max_quantity=max_q_int,
                                    charge=Decimal(str(s_charge))
                                )

                    if has_air_data:
                        air_charge, _ = CourierCharge.objects.get_or_create(
                            product=item,
                            mode=CourierMode.AIR
                        )
                        air_charge.tiers.all().delete()
                        for t in tiers:
                            a_charge = t.get("air_charge")
                            if a_charge is not None and str(a_charge).strip() != "":
                                min_q = int(t.get("min_quantity", 1))
                                max_q = t.get("max_quantity")
                                max_q_int = int(max_q) if (max_q is not None and str(max_q).strip() != "") else None
                                CourierChargeTier.objects.create(
                                    courier_product=air_charge,
                                    min_quantity=min_q,
                                    max_quantity=max_q_int,
                                    charge=Decimal(str(a_charge))
                                )

                    count += 1

            return JsonResponse({
                "status": "success",
                "message": f"Successfully updated {count} product(s) with new pricing and tier configurations!"
            })
        except Exception as e:
            logger.exception("Error in BulkUpdateProductTiersApiView")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)


class SaveSingleProductPricingApiView(RBACViewMixin, View):
    """
    Save/update single product pricing, tax, HSN, and individual tiers.
    """
    rbac_permission = "product_tier.manage"  # <-- Only Accounts & Super Admin

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            product_id = payload.get("product_id")
            if not product_id:
                return JsonResponse({"status": "error", "message": "Missing product_id"}, status=400)

            item = get_object_or_404(InventoryItem, id=product_id)

            base_price = payload.get("base_price", 0)
            msrp = payload.get("msrp", 0)
            tax_rate = payload.get("tax_rate", 0)
            hsn = payload.get("hsn")
            has_dynamic_price = payload.get("has_dynamic_price", False)
            tiers = payload.get("tiers", [])

            with transaction.atomic():
                prod_price, _ = ProductPrice.objects.get_or_create(
                    product=item,
                    defaults={"price": Decimal(str(base_price or 0))}
                )
                prod_price.price = Decimal(str(base_price or 0))
                prod_price.msrp = Decimal(str(msrp or 0)) if (msrp is not None and str(msrp).strip() != "") else Decimal("0.00")
                prod_price.tax_rate = Decimal(str(tax_rate or 0))
                prod_price.hsn = Decimal(str(hsn)) if (hsn is not None and str(hsn).strip() != "") else None
                prod_price.has_dynamic_price = bool(has_dynamic_price)
                prod_price.save()

                prod_price.price_tiers.all().delete()
                for t in tiers:
                    min_q = int(t.get("min_quantity", 1))
                    u_price = Decimal(str(t.get("unit_price", prod_price.price)))
                    t_msrp = Decimal(str(t.get("msrp", prod_price.msrp or 0))) if (t.get("msrp") is not None and str(t.get("msrp")).strip() != "") else Decimal("0.00")
                    ProductPriceTier.objects.create(
                        product=prod_price,
                        min_quantity=min_q,
                        unit_price=u_price,
                        msrp=t_msrp
                    )

                surface_tiers = payload.get("surface_tiers")
                if surface_tiers is not None:
                    surface_charge, _ = CourierCharge.objects.get_or_create(
                        product=item,
                        mode=CourierMode.SURFACE
                    )
                    surface_charge.tiers.all().delete()
                    for st in surface_tiers:
                        c_val = st.get("charge")
                        if c_val is not None and str(c_val).strip() != "":
                            min_q = int(st.get("min_quantity", 1))
                            max_q = st.get("max_quantity")
                            max_q_int = int(max_q) if (max_q is not None and str(max_q).strip() != "") else None
                            CourierChargeTier.objects.create(
                                courier_product=surface_charge,
                                min_quantity=min_q,
                                max_quantity=max_q_int,
                                charge=Decimal(str(c_val))
                            )

                air_tiers = payload.get("air_tiers")
                if air_tiers is not None:
                    air_charge, _ = CourierCharge.objects.get_or_create(
                        product=item,
                        mode=CourierMode.AIR
                    )
                    air_charge.tiers.all().delete()
                    for at in air_tiers:
                        c_val = at.get("charge")
                        if c_val is not None and str(c_val).strip() != "":
                            min_q = int(at.get("min_quantity", 1))
                            max_q = at.get("max_quantity")
                            max_q_int = int(max_q) if (max_q is not None and str(max_q).strip() != "") else None
                            CourierChargeTier.objects.create(
                                courier_product=air_charge,
                                min_quantity=min_q,
                                max_quantity=max_q_int,
                                charge=Decimal(str(c_val))
                            )

            return JsonResponse({
                "status": "success",
                "message": f"Saved pricing & tiers for {item.name}"
            })
        except Exception as e:
            logger.exception("Error in SaveSingleProductPricingApiView")
            return JsonResponse({"status": "error", "message": str(e)}, status=500)