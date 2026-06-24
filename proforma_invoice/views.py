from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem , ProformaPriceChangeRequest,ProformaStockShortageRequest,ProformaRemark, CourierMode, CourierCharge
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
logger = logging.getLogger(__name__)


DISABLED_PROFORMA_PRODUCT_IDS = [
2708,2709,2722,2727,2728,2729,2730,2763,2769,2782,
2787,2797,2803,2805,2821,2824,2835,2837,2838,2841,
2842,2843,2844,2851,2855,2859,2860,2862,2871,2872,
2874,2875,2882,2884,2887,2888,2896,2909,2916,2932,
2933,2943,2956,2957,2958,2961,2963,2964,2965,2966,
2974,2980,2981,2982,2984,2985,2986,2987,2989,2998,
3016,3030,3031,3075,3078,3079,3080,3087,3088,3089,
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
class CreateProformaInvoiceView(AccountantRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")

        # Filter out items with 0 price or no price record
        items = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related("proforma_price__price_tiers", "courier_sheets")
            .filter(proforma_price__price__gt=0)   #products whose prices are 0
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


    # --- NEW HELPER METHOD FOR IS_PERMITTED LOGIC ---
    def check_is_permitted(self, customer, product, requested_price, current_recommended):
        """
        Checks if this price was already approved for this customer.
        Returns True if:
        1. Memory exists for this Customer + Product.
        2. The Recommended price hasn't changed since approval.
        3. The new requested price is >= the previously approved minimum.
        """
        memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()
        if memory:
            # Only valid if the master price (recommended) hasn't changed
            if memory.base_price_at_approval == current_recommended:
                if requested_price >= memory.min_approved_price:
                    return True
        return False


    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "save")
        invoice_form = ProformaInvoiceForm(request.POST, user=request.user)

        # Allow programmatic setting of created_by
        if 'created_by' in invoice_form.fields:
            invoice_form.fields['created_by'].required = False

        formset = ProformaItemFormSet(request.POST, queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        # Customer resolution
        customer_id = request.POST.get("customer", "")
        selected_customer = Customer.objects.filter(id=customer_id).first() if customer_id.isdigit() else None
        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(
            id=shipping_id).first() if shipping_id.isdigit() else selected_customer

        if not selected_customer:
            invoice_form.add_error(None, "Please select a valid customer.")
            return self._render_error(request, invoice_form, formset, selected_customer)

        if invoice_form.is_valid() and formset.is_valid():
            valid_forms = [f for f in formset if f.cleaned_data and f.cleaned_data.get("product")]

            if not valid_forms:
                invoice_form.add_error(None, "❌ Please add at least one product.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 1. DATA GATHERING & STOCK VALIDATION =================
            courier_mode = request.POST.get("courier_mode", "surface")
            RESTRICTED_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]

            restricted_qty = 0
            has_resin = False
            has_stock_issue = False
            error_msg_parts = []
            shortage_details = {}

            for f in valid_forms:
                p = f.cleaned_data['product']
                qty = f.cleaned_data['quantity']
                # min qty check logic starts here
                pricing_config = ProductPrice.objects.filter(product=p).first()
                if pricing_config:
                    min_required = pricing_config.min_requirement
                    if qty < min_required:
                        # This adds the error to the top of the form
                        invoice_form.add_error(None,
                                               f"❌ '{p.name}' requires a minimum quantity of {min_required}. (You entered: {qty})")
                        # This 'return' is the most important part; it stops the code from reaching Section 4
                        return self._render_error(request, invoice_form, formset, selected_customer)
                # min qty check logic ends here
                cat_name = p.category.name.upper()

                if cat_name in RESTRICTED_CATEGORIES:
                    restricted_qty += qty
                if "RESIN" in cat_name:
                    has_resin = True

                # Stock Check logic
                available = getattr(p, 'quantity', 0)
                if qty > available:
                    has_stock_issue = True
                    shortage_details[p.name] = f"Requested: {qty}, Available: {available}"
                    error_msg_parts.append(f"{p.name} (Stock: {available})")

            # ================= 2. COURIER LOGIC RULES =================
            # Rule: Surface restricted (Thermoforming/Bay Materials < 200)
            if courier_mode == "surface" and 0 < restricted_qty < 200:
                invoice_form.add_error(None,
                                       f"❌ Surface shipping rejected: Total quantity for Thermoforming/Bay Material is {restricted_qty}. "
                                       "These categories cannot be sent via Surface below 200 sheets. Please change mode to Air.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # Rule: Air restricted (No Resin allowed)
            if courier_mode == "air" and has_resin:
                invoice_form.add_error(None,
                                       "❌ Air shipping rejected: Resin products cannot be sent by Air. Please change mode to Surface.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 3. STOCK SHORTAGE GATE =================
            if action == "save" and has_stock_issue and not request.user.is_superuser:
                detailed_msg = "❌ Stock Shortage: " + ", ".join(
                    error_msg_parts) + ". Use 'Send Request to Accounts' to proceed."
                invoice_form.add_error(None, detailed_msg)
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 4. SAVE PROCESS =================
            try:
                with transaction.atomic():
                    invoice = invoice_form.save(commit=False)
                    invoice.customer = selected_customer
                    invoice.shipping_customer = shipping_customer
                    invoice.courier_mode = courier_mode

                    if not request.user.is_accountant:
                        invoice.created_by = request.user.username
                    invoice.save()

                    # Handle Items & Price Overrides
                    # price_overrides = {}
                    has_price_issue = False
                    any_under_msrp = False  # <--- ADD THIS LINE HERE (Initialize)

                    req_prices_list = request.POST.getlist("requested_unit_price")
                    req_courier = request.POST.get("requested_courier_charge", "").strip()
                    req_reason = request.POST.get("request_reason", "").strip()
                    #abhijay code start
                    price_change_requests_for_email = []
                    #abhijay code end
                    for index, f in enumerate(valid_forms):
                        product_obj = f.cleaned_data.get('product')
                        qty = f.cleaned_data.get('quantity')

                        # 1. Create the item and link it to the invoice
                        item = f.save(commit=False)
                        item.invoice = invoice
                        item.quantity = qty

                        # 2. SAVE IMMEDIATELY to get an ID (very important for the dictionary below)
                        item.save()

                        # 3. Resolve Snapshots (Recommended Price & MSRP)
                        pricing = getattr(product_obj, "proforma_price", None)
                        standard_price = pricing.price if pricing else Decimal("0.00")
                        msrp = pricing.msrp or Decimal("0.00")

                        # Handle tiered pricing if applicable
                        if pricing and pricing.has_dynamic_price:
                            tier = pricing.price_tiers.filter(min_quantity__lte=qty).order_by("-min_quantity").first()
                            if tier: standard_price = tier.unit_price

                        # 4. Process User Input Price
                        user_val = standard_price  # Default
                        if index < len(req_prices_list):
                            u_val = req_prices_list[index].strip()
                            if u_val:
                                user_val = Decimal(u_val)

                        # 5. Check Memory (Auto-unlock)
                        is_permitted = self.check_is_permitted(selected_customer, product_obj, user_val, standard_price)

                        # 6. Apply Price Logic
                        # if user_val != standard_price:
                        if user_val < standard_price:

                            if is_permitted:
                                # Auto-approved: Save directly to the item snapshot fields
                                item.current_price = user_val
                                # item.requested_price = user_val
                                # item.save()  # Save the updated price
                            else:
                                # Needs Approval: Add to the Request dictionary
                                has_price_issue = True
                                is_under_msrp = user_val < msrp
                                if is_under_msrp:
                                    any_under_msrp = True

                                # 2. CREATE INDIVIDUAL ROW FOR THIS PRODUCT
                                ProformaPriceChangeRequest.objects.create(
                                    invoice=invoice,
                                    customer=selected_customer,
                                    product=product_obj,  # Specific product link
                                    requested_by=request.user,
                                    is_product_request=True,  # IDENTIFIER
                                    requested_price=user_val,
                                    recommended_price=standard_price,
                                    msrp_snapshot=msrp,
                                    is_under_msrp=is_under_msrp,
                                    reason=req_reason,
                                    status="pending"
                                )
                                # 3. Revert item price to standard until approved
                                item.current_price = standard_price
                                # Abhijay code starts
                                price_change_requests_for_email.append({
                                    "product": product_obj,
                                    "requested_price": user_val,
                                    "recommended_price": standard_price,
                                    "msrp": msrp,
                                    "is_under_msrp": is_under_msrp,
                                })
                                # Abhijay code ends


                        else:
                            # Standard price used: snapshot the system price
                            item.current_price = standard_price
                        item.save()

                    # ================= 5. HANDLE REQUEST CREATION =================

                    # --- CHANGE 2: ADD THIS COURIER BLOCK ---
                    has_courier_issue = False
                    if req_courier != "" and not request.user.is_superuser:
                        has_courier_issue = True
                        ProformaPriceChangeRequest.objects.create(
                            invoice=invoice,
                            customer=selected_customer,
                            requested_by=request.user,
                            is_product_request=False,  # IDENTIFIER FOR COURIER
                            requested_courier_charge=Decimal(req_courier),
                            reason=req_reason,
                            status="pending"
                        )
                    #abhijay code starts
                    # ================= PRICE CHANGE CONSOLIDATED EMAIL =================

                    if price_change_requests_for_email:

                        to_emails = ["bhavya@obluhc.com"]
                        cc_emails = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]
                        if request.user.email:
                            cc_emails.append(request.user.email)

                        all_items = invoice.items.select_related("product")

                        any_under_msrp_email = any(
                            x["is_under_msrp"] for x in price_change_requests_for_email
                        )

                        email_context = {
                            "invoice": invoice,
                            "requested_by": request.user,
                            "customer": selected_customer,
                            "price_requests": price_change_requests_for_email,
                            "reason": req_reason,
                            "all_items": all_items,
                            "any_under_msrp": any_under_msrp_email,
                            "review_url": "https://oblutools.com/proforma/price-change-requests/"
                        }

                        html_content = render_to_string(
                            "proforma_invoice/price_change_request_email_v2.html",
                            email_context
                        )

                        subject = f"💰 Price Change Request Submitted (Proforma #{invoice.id})"

                        if any_under_msrp_email:
                            subject = f"🚨 UNDER MSRP Price Request (Proforma #{invoice.id})"

                        from_email = "proforma@oblutools.com"

                        msg = EmailMultiAlternatives(
                            subject,
                            "",
                            from_email,
                            to_emails,
                            cc=cc_emails
                        )

                        msg.attach_alternative(html_content, "text/html")
                        msg.send()

                    # ===============================================================
                    # abhijay code ends
                    needs_request = (
                                action == "request_accounts" or has_stock_issue or has_price_issue or has_courier_issue)

                    if needs_request and not request.user.is_superuser:
                        invoice.is_price_altered = True
                        invoice.save()

                        if has_stock_issue:
                            ProformaStockShortageRequest.objects.create(
                                invoice=invoice, requested_by=request.user,
                                shortage_details=shortage_details, status="pending"
                            )
                            # Abhijay Chnage starts
                            # ---------------- STOCK REQUEST EMAIL ----------------
                            to_emails = ["accounts@obluhc.com"]
                            cc_emails = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]
                            if request.user.email:
                                cc_emails.append(request.user.email)

                            email_context = {
                                "invoice": invoice,
                                "requested_by": request.user,
                                "shortage_details": shortage_details,
                                "review_url": "https://oblutools.com/proforma/stock-requests/",
                            }

                            html_content = render_to_string(
                                "proforma_invoice/stock_request_email.html",
                                email_context
                            )

                            subject = f"📦 Stock Request Submitted (Proforma #{invoice.id})"
                            from_email = "proforma@oblutools.com"

                            msg = EmailMultiAlternatives(
                                subject,
                                "",
                                from_email,
                                to_emails,
                                cc=cc_emails
                            )

                            msg.attach_alternative(html_content, "text/html")
                            msg.send()
                            # -----------------------------------------------------
                            # Abhijay Chnage ends

                        # if has_price_issue or req_courier != "":
                        #     # ProformaPriceChangeRequest.objects.get_or_create(
                        #     ProformaPriceChangeRequest.objects.create(
                        #         invoice=invoice, requested_by=request.user, customer=selected_customer, # Added customer link
                        #         is_under_msrp=any_under_msrp, # Flag for Nitin's filter
                        #         requested_product_prices=price_overrides,
                        #         requested_courier_charge=req_courier if req_courier != "" else None,
                        #         reason=req_reason, status="pending"
                        #     )
                        if any_under_msrp:
                            messages.warning(request,
                                             "⚠️ Request contains items below MSRP. Only Super Admin can approve.")

                        messages.success(request, f"✅ Request for Proforma #{invoice.id} sent to Accounts.")
                        return redirect("proforma_list")

                    messages.success(request, "✅ Proforma created successfully.")
                    return redirect("proforma_detail", pk=invoice.pk)

            except Exception as e:
                invoice_form.add_error(None, f"An unexpected error occurred: {str(e)}")
                return self._render_error(request, invoice_form, formset, selected_customer)

        return self._render_error(request, invoice_form, formset, selected_customer)

    def _get_customers(self, request):
        if request.user.is_accountant or request.user.is_superuser:
            return Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp = request.user.salesperson_profile.first()
            return Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
        return Customer.objects.filter(proforma_invoices__created_by=request.user.username).distinct()

    def _render_error(self, request, invoice_form, formset, selected_customer):

        requested_prices = request.POST.getlist("requested_unit_price")
        requested_courier = request.POST.get("requested_courier_charge", "")
        request_reason = request.POST.get("request_reason", "")
        shipping_id = request.POST.get("shipping_customer", "")

        shipping_customer = Customer.objects.filter(id=shipping_id).first() if shipping_id.isdigit() else None

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")
        items = (
            InventoryItem.objects.select_related("category", "proforma_price")
            .filter(proforma_price__price__gt=0)
            .exclude(id__in=DISABLED_PROFORMA_PRODUCT_IDS)
            .order_by("name")
        )
        return render(request, "proforma_invoice/create_proforma.html", {
            "invoice_form": invoice_form, "formset": formset,
            "customers": customers, "categories": categories,
            "items": items, "selected_customer": selected_customer,
            "requested_prices": requested_prices,
            "requested_courier": requested_courier,
            "request_reason": request_reason,
            "shipping_customer": shipping_customer,

        })
    def _render_error(self, request, invoice_form, formset, selected_customer):
        # 1. Get the raw list of requested prices from POST
        requested_prices = request.POST.getlist("requested_unit_price")

        # 2. Manually attach the values to the form objects
        for i, form in enumerate(formset):
            if i < len(requested_prices):
                # We create a temporary attribute 'manual_price' on the form
                form.manual_price = requested_prices[i]

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")
        items = (
            InventoryItem.objects.select_related("category", "proforma_price")
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
            "selected_customer": selected_customer,
            # Pass these back too
            "requested_courier": request.POST.get("requested_courier_charge", ""),
            "request_reason": request.POST.get("request_reason", ""),
        })


#API
def customer_purchase_history_api(request, customer_id):
    # This looks through previous Proforma Items for this customer
    purchased_product_ids = ProformaInvoiceItem.objects.filter(
        invoice__customer_id=customer_id
    ).values_list('product_id', flat=True).distinct()

    return JsonResponse({
        'purchased_ids': list(purchased_product_ids)
    })

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

class CreateProformaInvoiceView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        invoice_form = ProformaInvoiceForm(user=request.user)
        formset = ProformaItemFormSet(queryset=ProformaInvoiceItem.objects.none(), user=request.user)

        customers = self._get_customers(request)
        categories = Category.objects.all().order_by("name")

        # Filter out items with 0 price or no price record
        items = (
            InventoryItem.objects
            .select_related("category", "proforma_price")
            .prefetch_related("proforma_price__price_tiers", "courier_sheets")
            .filter(proforma_price__price__gt=0)   #products whose prices are 0
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


    # --- NEW HELPER METHOD FOR IS_PERMITTED LOGIC ---
    def check_is_permitted(self, customer, product, requested_price, current_recommended):
        """
        Checks if this price was already approved for this customer.
        Returns True if:
        1. Memory exists for this Customer + Product.
        2. The Recommended price hasn't changed since approval.
        3. The new requested price is >= the previously approved minimum.
        """
        memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()
        if memory:
            # Only valid if the master price (recommended) hasn't changed
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

        # Customer resolution
        customer_id = request.POST.get("customer", "")
        selected_customer = Customer.objects.filter(id=customer_id).first() if customer_id.isdigit() else None
        shipping_id = request.POST.get("shipping_customer", "")
        shipping_customer = Customer.objects.filter(
            id=shipping_id).first() if shipping_id.isdigit() else selected_customer

        if not selected_customer:
            invoice_form.add_error(None, "Please select a valid customer.")
            return self._render_error(request, invoice_form, formset, selected_customer)

        if invoice_form.is_valid() and formset.is_valid():
            valid_forms = [f for f in formset if f.cleaned_data and f.cleaned_data.get("product")]

            if not valid_forms:
                invoice_form.add_error(None, "❌ Please add at least one product.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 1. DATA GATHERING & STOCK VALIDATION =================
            courier_mode = request.POST.get("courier_mode", "surface")
            RESTRICTED_CATEGORIES = ["THERMOFORMING SHEETS", "BAY MATERIALS", "COHERZ"]
            restricted_qty = 0
            has_resin = False
            has_stock_issue = False
            error_msg_parts = []
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

                cat_name = p.category.name.upper()
                if cat_name in RESTRICTED_CATEGORIES:
                    restricted_qty += qty
                if "RESIN" in cat_name:
                    has_resin = True

                available = getattr(p, 'quantity', 0)
                if qty > available:
                    has_stock_issue = True
                    shortage_details.append({
                        'product_obj': p,
                        'name': p.name,
                        'requested': qty,
                        'available': available
                    })
                    error_msg_parts.append(f"{p.name} (Stock: {available})")

            # ================= 2. COURIER LOGIC RULES =================
            if courier_mode == "surface" and 0 < restricted_qty < 200:
                invoice_form.add_error(None, "❌ Surface shipping rejected for Thermoforming/Bay Material below 200.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            if courier_mode == "air" and has_resin:
                invoice_form.add_error(None, "❌ Air shipping rejected: Resin products cannot be sent by Air.")
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 3. STOCK SHORTAGE GATE =================
            if action == "save" and has_stock_issue and not request.user.is_superuser:
                detailed_msg = "❌ Stock Shortage detected. Use 'Send Request to Accounts' to proceed."
                invoice_form.add_error(None, detailed_msg)
                return self._render_error(request, invoice_form, formset, selected_customer)

            # ================= 4. SAVE PROCESS =================
            try:
                with transaction.atomic():
                    invoice = invoice_form.save(commit=False)
                    invoice.customer = selected_customer
                    invoice.shipping_customer = shipping_customer
                    invoice.courier_mode = courier_mode
                    if not request.user.is_accountant:
                        invoice.created_by = request.user.username
                    invoice.save()

                    has_price_issue = False
                    any_under_msrp = False
                    has_credit_issue = (action == "request_credit")

                    req_prices_list = request.POST.getlist("requested_unit_price")
                    req_row_reasons = request.POST.getlist("requested_price_reason")
                    req_courier = request.POST.get("requested_courier_charge", "").strip()
                    req_reason = request.POST.get("request_reason", "").strip()

                    price_change_requests_for_email = []

                    # Process items loop (NO RETURNS INSIDE HERE)
                    for index, f in enumerate(valid_forms):
                        product_obj = f.cleaned_data.get('product')
                        qty = f.cleaned_data.get('quantity')

                        item = f.save(commit=False)
                        item.invoice = invoice
                        item.quantity = qty
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
                        current_row_reason = req_row_reasons[index].strip() if index < len(req_row_reasons) else ""

                        if user_val < standard_price:
                            if not is_permitted:
                                has_price_issue = True
                                is_under_msrp = user_val < msrp
                                if is_under_msrp: any_under_msrp = True

                                ProformaPriceChangeRequest.objects.create(
                                    invoice=invoice, customer=selected_customer, product=product_obj,
                                    requested_by=request.user, is_product_request=True, requested_price=user_val,
                                    recommended_price=standard_price, msrp_snapshot=msrp, is_under_msrp=is_under_msrp,
                                    reason=current_row_reason, status="pending"
                                )
                                item.current_price = standard_price
                                price_change_requests_for_email.append({
                                    "product": product_obj, "requested_price": user_val,
                                    "recommended_price": standard_price, "msrp": msrp,
                                    "is_under_msrp": is_under_msrp, "reason": current_row_reason,
                                })
                            else:
                                item.current_price = user_val
                        else:
                            item.current_price = standard_price
                        item.save()

                    # ================= 5. CONSOLIDATED REQUEST CREATION =================

                    # 5A. Courier Charge Request
                    has_courier_issue = False
                    if req_courier != "" and not request.user.is_superuser:
                        has_courier_issue = True
                        ProformaPriceChangeRequest.objects.create(
                            invoice=invoice, customer=selected_customer, requested_by=request.user,
                            is_product_request=False, requested_courier_charge=Decimal(req_courier),
                            reason=req_reason, status="pending"  # differnt  stock
                        )

                    # 5B. Price Change Emails
                    if price_change_requests_for_email:
                        to_emails = ["bhavya@obluhc.com"]
                        cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                        if request.user.email: cc_emails.append(request.user.email)
                        any_under_msrp_email = any(x["is_under_msrp"] for x in price_change_requests_for_email)
                        email_context = {
                            "invoice": invoice, "requested_by": request.user, "customer": selected_customer,
                            "price_requests": price_change_requests_for_email, "reason": req_reason,
                            "all_items": invoice.items.select_related("product"),
                            "any_under_msrp": any_under_msrp_email,
                            "review_url": "https://oblutools.com/proforma/price-change-requests/"
                        }
                        html_content = render_to_string("proforma_invoice/price_change_request_email_v2.html",
                                                        email_context)
                        subject = f"💰 {'🚨 UNDER MSRP' if any_under_msrp_email else ''} Price Request (PI #{invoice.id})"
                        msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                        msg.attach_alternative(html_content, "text/html")
                        msg.send()

                    # 5C. Credit Overdue Bypass Request
                    actual_credit_req_created = False
                    if has_credit_issue:
                        overdue_records = CustomerVoucherStatus.objects.filter(
                            customer=selected_customer, is_credit_period_crossed=True
                        ).filter(Q(is_unpaid=True) | Q(is_partially_paid=True)).select_related('voucher')

                        if overdue_records.exists():
                            bypass_req, created = CreditPeriodOverdueByPassRequest.objects.get_or_create(
                                customer=selected_customer, proforma_invoice=invoice,
                                requested_by=request.user, defaults={'status': 'pending'}
                            )
                            actual_credit_req_created = True

                            # 2. SEND EMAIL TO ADMIN/SUPERUSER
                            try:
                                to_emails = ["nitin.a@obluhc.com"]  # Replace with actual Admin emails
                                cc_emails = [request.user.email] if request.user.email else []
                                cc_emails.append("abhijay.obluhc@gmail.com")

                                context = {
                                    "request_obj": bypass_req,
                                    "overdue_invoices": overdue_records,
                                    "customer": selected_customer,
                                    "salesperson": request.user.get_full_name() or request.user.username,
                                    "review_url": "https://oblutools.com/proforma/credit-bypass-requests/"
                                }

                                html_content = render_to_string(
                                    "proforma_invoice/credit_bypass_request_mail.html", context)
                                subject = f"🚨 CREDIT BYPASS REQUIRED: {selected_customer.name} (PI #{invoice.id})"

                                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails,
                                                             cc=cc_emails)
                                msg.attach_alternative(html_content, "text/html")
                                msg.send()
                            except Exception as e:
                                print(f"Credit Request Email Error: {e}")

                    # 5D. Stock Shortage Request (Runs even if Credit issue exists)
                    if has_stock_issue:
                        for item in shortage_details:
                            ProformaStockShortageRequest.objects.create(
                                invoice=invoice, requested_by=request.user,status="pending",product=item['product_obj'],  # Individual product
                                requested_quantity=item['requested'],  #  product  quantity
                                available_quantity=item['available'],  # product stock

                        )
                        # Stock Email
                        to_emails = ["accounts@obluhc.com"]
                        cc_emails = ["swasti.obluhc@gmail.com", "abhijay.obluhc@gmail.com", "nitin.a@obluhc.com"]
                        if request.user.email: cc_emails.append(request.user.email)
                        email_context = {
                            "invoice": invoice, "requested_by": request.user, "shortage_details": shortage_details,
                            "review_url": "https://oblutools.com/proforma/stock-requests/",
                        }
                        html_content = render_to_string("proforma_invoice/stock_request_email.html", email_context)
                        msg = EmailMultiAlternatives(f"📦 Stock Request (PI #{invoice.id})", "",
                                                     "proforma@oblutools.com", to_emails, cc=cc_emails)
                        msg.attach_alternative(html_content, "text/html")
                        msg.send()

                    # Final Evaluation: Determine if redirect to list (locked) or detail (unlocked)
                    needs_request = (
                                has_stock_issue or has_price_issue or has_courier_issue or actual_credit_req_created)

                    if needs_request and not request.user.is_superuser:
                        invoice.is_price_altered = True  # This Locks the Proforma
                        invoice.save()

                        if any_under_msrp:
                            messages.warning(request, "⚠️ Below MSRP items require Admin approval.")

                        messages.success(request, f"✅ Request for PI #{invoice.id} sent for required approvals.")
                        return redirect("proforma_list")

                    messages.success(request, "✅ Proforma created successfully.")
                    return redirect("proforma_detail", pk=invoice.pk)

            except Exception as e:
                invoice_form.add_error(None, f"An unexpected error occurred: {str(e)}")
                return self._render_error(request, invoice_form, formset, selected_customer)

        return self._render_error(request, invoice_form, formset, selected_customer)

    def _get_customers(self, request):
        if request.user.is_accountant or request.user.is_superuser:
            return Customer.objects.all()
        elif hasattr(request.user, "salesperson_profile"):
            sp = request.user.salesperson_profile.first()
            return Customer.objects.filter(salesperson=sp) if sp else Customer.objects.none()
        return Customer.objects.filter(proforma_invoices__created_by=request.user.username).distinct()


    def _render_error(self, request, invoice_form, formset, selected_customer):
        # 1. Get the lists from POST
        requested_prices = request.POST.getlist("requested_unit_price")
        requested_reasons = request.POST.getlist("requested_price_reason")

        # 2. Attach values to the formset objects so the HTML can see them
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





class OverdueBypassListView(AccountantRequiredMixin, ListView):
    model = CreditPeriodOverdueByPassRequest
    template_name = "proforma_invoice/credit_period_overdue_bypass_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        return CreditPeriodOverdueByPassRequest.objects.select_related(
            'customer', 'proforma_invoice', 'requested_by'
        ).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Import the model here to avoid circular imports
        from proforma_invoice.models import CreditPeriodOverdueByPassRequest
        from customer_dashboard.models import CustomerVoucherStatus

        for req in context['requests']:
            # Fetch the actual overdue records from the status model
            # Note: We use .select_related('voucher') to get the Voucher Number later
            req.overdue_history = CustomerVoucherStatus.objects.filter(
                customer=req.customer,
                is_credit_period_crossed=True
            ).filter(
                Q(is_unpaid=True) | Q(is_partially_paid=True)
            ).select_related('voucher').order_by('-voucher_date')

        return context

class ApproveOverdueBypassView(AccountantRequiredMixin, View):
    def post(self, request, pk):
        bypass_req = get_object_or_404(CreditPeriodOverdueByPassRequest, pk=pk)
        decision = request.POST.get('decision')

        if decision == 'yes':
            bypass_req.status = 'approved'
            bypass_req.approved_by = request.user
            bypass_req.reviewed_at = timezone.now()
            status_label = "APPROVED ✅"
        else:
            bypass_req.status = 'rejected'
            status_label = "REJECTED ❌"

        bypass_req.save()

        # --- SEND EMAIL NOTIFICATION TO SALESPERSON ---
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
                msg.send()
        except Exception as e:
            print(f"Credit Status Email Error: {e}")

        messages.success(request, f"Request processed: {status_label}")
        return redirect('overdue_bypass_list')

# --- Custom Access Mixin ---
class AccountantRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        # Only allow Superusers or users with is_accountant=True
        return self.request.user.is_superuser or getattr(self.request.user, 'is_accountant', False)

# --- Dashboard View ---
class StockRequestDashboardView(LoginRequiredMixin, AccountantRequiredMixin, ListView):
    model = ProformaStockShortageRequest
    template_name = "proforma_invoice/stock_requests_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        # Removed 'status' from order_by to ensure Latest (Newest) is always on top
        return ProformaStockShortageRequest.objects.all().order_by('-created_at')

# --- Action View ---
class ApproveStockRequestView(LoginRequiredMixin, AccountantRequiredMixin, View):
    def post(self, request, pk):
        # 1. Get the specific individual product request
        req = get_object_or_404(ProformaStockShortageRequest, pk=pk)
        action = request.POST.get("action")  # 'approve' or 'reject'

        # Safely get product name to avoid 'NoneType' error
        product_name = req.product.name if req.product else f"Item (Req #{req.id})"

        # 2. Update status and set Email variables
        if action == "approve":
            req.status = "approved"
            email_template = "proforma_invoice/stock_request_approved_email.html"
            email_subject = f"✅ Stock Available - Request Approved (Inv #{req.invoice.id} - {product_name})"
            messages.success(request, f"✅ Stock for '{product_name}' (Inv #{req.invoice.id}) approved.")
        else:
            req.status = "rejected"
            email_template = "proforma_invoice/stock_request_rejected_email.html"
            email_subject = f"❌ Stock Unavailable - Request Rejected (Inv #{req.invoice.id} - {product_name})"
            messages.error(request, f"❌ Stock for '{product_name}' (Inv #{req.invoice.id}) rejected.")

        # 3. TIMER LOGIC: Only set reviewed_at the VERY FIRST time it is touched
        if not req.reviewed_at:
            req.reviewed_at = timezone.now()
            req.reviewed_by = request.user

        req.save()

        # 4. PROFORMA UNLOCK LOGIC
        # Check if EVERY stock request for this invoice is now 'approved'
        all_stock_requests_approved = not req.invoice.stock_requests.exclude(status="approved").exists()

        # Check for any pending Price Change requests
        from .models import ProformaPriceChangeRequest
        pending_prices = ProformaPriceChangeRequest.objects.filter(
            invoice=req.invoice,
            status="pending"
        ).exists()

        if all_stock_requests_approved and not pending_prices:
            req.invoice.is_price_altered = False
        else:
            req.invoice.is_price_altered = True

        req.invoice.save()

        # 5. EMAIL NOTIFICATION
        try:
            requester_email = req.requested_by.email
            if requester_email:
                email_context = {
                    "request_obj": req,
                    "invoice": req.invoice,
                    "reviewed_by": request.user,
                    "product_name": product_name,
                    "action": action,
                    "review_url": f"https://oblutools.com/proforma/{req.invoice.id}/"
                }

                html_content = render_to_string(email_template, email_context)

                msg = EmailMultiAlternatives(
                    email_subject,
                    "",
                    "proforma@oblutools.com",
                    [requester_email],
                    cc=["abhijay.obluhc@gmail.com"]
                )
                msg.attach_alternative(html_content, "text/html")
                msg.send()
        except Exception as e:
            print(f"Stock Approval Mail Failed: {e}")

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



class ProformaInvoiceDetailView(LoginRequiredMixin, DetailView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_detail.html"
    context_object_name = "invoice"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        invoice = self.object
        from django.contrib import messages

        # =========================================================
        # 🔹 1. MASTER BYPASS (Fixes "Not Opening" for you)
        # =========================================================
        # If user is Superuser or Accountant, bypass all locks immediately.
        if request.user.is_superuser or getattr(request.user, 'is_accountant', False):
            return super().get(request, *args, **kwargs)

        # =========================================================
        # 🔹 2. LOCKING LOGIC (For Salespeople only)
        # =========================================================
        # Check if ANY individual product request for this invoice is still 'pending'
        if invoice.stock_requests.filter(status="pending").exists():
            messages.warning(request, "⏳ Warehouse approval is pending. Access locked.")
            return redirect("proforma_list")

        # # RULE B: If ANY stock was rejected, salesperson is blocked (Access denied)
        # if invoice.stock_requests.filter(status="rejected").exists():
        #     messages.error(request, "❌ Some items in this request were unavailable. Access denied.")
        #     return redirect("proforma_list")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoice = self.object
        import os
        from decimal import Decimal, ROUND_HALF_UP
        from num2words import num2words
        from django.conf import settings

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
        # 🔹 3. FILTER REJECTED PRODUCTS (DATABASE DRIVEN)
        # =========================================================
        # Instead of old dictionary logic, we get IDs of products rejected on dashboard
        rejected_ids = invoice.stock_requests.filter(status="rejected").values_list('product_id', flat=True)

        items_qs = invoice.items.select_related("product__proforma_price").prefetch_related(
            "product__proforma_price__price_tiers")

        # =========================================================
        # 🔹 4. RESOLVE APPROVED PRICES
        # =========================================================
        latest_price_req = invoice.price_requests.all().order_by("-id").first()
        altered_prices = {}
        use_requested_values = False

        if latest_price_req and latest_price_req.status in ["approved", "pending"]:
            # Auto-switch to the Altered/Draft template
            self.template_name = "proforma_invoice/proforma_detail_altered.html"

            if latest_price_req.status == "approved":
                use_requested_values = True
                approved_reqs = invoice.price_requests.filter(status="approved", is_product_request=True)
                for req in approved_reqs:
                    altered_prices[str(req.product.id)] = req.requested_price

        # =========================================================
        # 🔹 5. PRODUCT RECALCULATION (Skipping Rejected)
        # =========================================================
        recalculated_items = []
        subtotal_excl = Decimal("0.00")
        total_product_gst = Decimal("0.00")

        for item in items_qs:
            # 🔥 CRITICAL: If product was rejected by Accountant, SKIP it.
            # It won't show in PI and its amount won't be added to the Grand Total.
            if item.product.id in rejected_ids:
                continue

            qty = Decimal(str(item.quantity or 0))
            gst_rate = Decimal(str(item.taxrate() or 0))

            # Resolve Unit Price
            if use_requested_values and str(item.product.id) in altered_prices:
                unit_price_incl = Decimal(str(altered_prices[str(item.product.id)]))
            elif item.current_price:
                unit_price_incl = item.current_price
            else:
                unit_price_incl = Decimal(str(item.unit_price()))

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
        # 🔹 6. COURIER CHARGES
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
        # 🔹 7. TOTALS & WORDS
        # =========================
        if subtotal_excl > 0:
            combined_gst_rate = (total_product_gst / subtotal_excl * Decimal("100")).quantize(Decimal("0.01"),
                                                                                              rounding=ROUND_HALF_UP)
        else:
            combined_gst_rate = Decimal("0.00")

        courier_gst = (courier_charge * combined_gst_rate / Decimal("100")).quantize(Decimal("0.01"),
                                                                                     rounding=ROUND_HALF_UP)
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

        # =========================
        # 🔹 8. CONTEXT UPDATE
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

@csrf_protect
def update_proforma_price_remark(request):
    if request.method == 'POST':
        invoice_id = request.POST.get('invoice_id')
        remark_text = request.POST.get('remark')

        invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        # Save to the new Model
        ProformaRemark.objects.create(
            invoice=invoice,
            user=request.user,
            remark=remark_text
        )

        # Fetch all remarks to refresh the chat window
        all_remarks = invoice.remarks.all().order_by('created_at')

        remarks_data = []
        for r in all_remarks:
            remarks_data.append({
                'user': r.user.username,
                'text': r.remark,
                'time': r.created_at.strftime("%d %b, %H:%M"),
                'is_admin': r.user.is_superuser or getattr(r.user, 'is_accountant', False)
            })

        return JsonResponse({'status': 'ok', 'remarks': remarks_data})
    return JsonResponse({'status': 'error'}, status=400)

#Legacy
class ProformaInvoiceListView(LoginRequiredMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list.html"
    context_object_name = "invoices"

    def get_queryset(self):
        user = self.request.user

        # 1. Role Based Access (Accountants see all, others see only their own)
        if user.is_accountant:
            qs = ProformaInvoice.objects.select_related("customer").all()
        else:
            qs = ProformaInvoice.objects.select_related("customer").filter(
                created_by=user.username
            )

        # 2. Apply Filters from GET parameters
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

        # 3. Sorting Logic
        if sort_by == "date_asc":
            qs = qs.order_by("date_created")
        elif sort_by == "customer":
            qs = qs.order_by("customer__name")
        else:
            # DEFAULT: Latest at the top (Newest first)
            qs = qs.order_by("-date_created")

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        User = get_user_model()

        # For filter dropdowns
        ctx["users"] = User.objects.filter(is_active=True) if self.request.user.is_accountant else []

        # Distinct list of customers who actually have invoices
        ctx["customers"] = (
            ProformaInvoice.objects.select_related("customer")
            .values("customer__id", "customer__name")
            .distinct()
        )
        return ctx



class ProformaInvoiceListView(LoginRequiredMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list.html"
    context_object_name = "invoices"
    paginate_by = 40

    def get_queryset(self):
        user = self.request.user

        # 1. Role Based Access (Accountants see all, others see only their own)
        if user.is_accountant:
            qs = ProformaInvoice.objects.select_related("customer").prefetch_related("credit_approvals","price_requests",
        "stock_requests").all()
        else:
            qs = ProformaInvoice.objects.select_related("customer").prefetch_related("credit_approvals","price_requests",
        "stock_requests").filter(
                created_by=user.username
            )

        qs = qs.annotate(
            pending_stock_count=Count('stock_requests', filter=Q(stock_requests__status='pending')),
            rejected_stock_count=Count('stock_requests', filter=Q(stock_requests__status='rejected')),
        ).prefetch_related('price_requests')

        # 2. Apply Filters from GET parameters
        created_by = self.request.GET.get("created_by")
        customer = self.request.GET.get("customer")
        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")
        sort_by = self.request.GET.get("sort_by")

        if created_by: qs = qs.filter(created_by=created_by)
        if customer: qs = qs.filter(customer__id=customer)

        # if start_date and end_date: qs = qs.filter(date_created__date__range=[start_date, end_date])
        if start_date:
            parsed_date = parse_date(start_date)
            if parsed_date:
                # Matches only the specific day selected (ignores time)
                qs = qs.filter(date_createddate=parsed_date)

        # 3. Sorting Logic
        if sort_by == "date_asc":
            qs = qs.order_by("date_created")
        elif sort_by == "customer":
            qs = qs.order_by("customer__name")
        else:
            # DEFAULT: Latest at the top (Newest first)
            qs = qs.order_by("-date_created")

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        User = get_user_model()

        # For filter dropdowns
        ctx["users"] = User.objects.filter(is_active=True) if self.request.user.is_accountant else []

        # Distinct list of customers who actually have invoices
        ctx["customers"] = (
            ProformaInvoice.objects.select_related("customer")
            .values("customer__id", "customer__name")
            .distinct()
        )
        return ctx


# ---------------------------------------------
@login_required
def request_dispatch(request, pk):
    """View for SP to raise request and notify Accounts"""
    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    if invoice.dispatch_status == 'processing':
        invoice.dispatch_status = 'requested'
        invoice.dispatch_requested_at = timezone.now()
        invoice.save()

        # --- 📧 NOTIFY ACCOUNTS TEAM ---
        User = get_user_model()
        # Find all accountants with an email address
        accountant_emails = list(User.objects.filter(
            is_accountant=True,
            is_active=True
        ).exclude(email="").values_list('email', flat=True))

        if accountant_emails:
            try:
                subject = f"🚀 New Dispatch Request: PI #{invoice.id} - {invoice.customer.name}"
                context = {
                    'requested_by': request.user.get_full_name() or request.user.username,
                    'invoice': invoice,
                    'site_url': "https://oblutools.com"  # Change to your domain
                }
                html_content = render_to_string('proforma_invoice/dispatch_request_admin_email.html', context)

                msg = EmailMultiAlternatives(
                    subject,
                    "",
                    "proforma@oblutools.com",
                    accountant_emails
                )
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Admin Email failed: {e}")

        messages.success(request, f"Dispatch request raised for Proforma #{invoice.id}. Accounts has been notified.")

    return redirect('proforma_list')

@login_required
def request_dispatch(request, pk):
    """View for SP to raise dispatch request and notify Accounts"""

    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    # Prevent duplicate requests
    if invoice.dispatch_status != 'processing':
        messages.warning(request, "Dispatch request already raised.")
        return redirect('proforma_list')

    # Update dispatch status
    invoice.dispatch_status = 'requested'
    invoice.dispatch_requested_at = timezone.now()
    invoice.save()

    # ================= EMAIL SECTION =================
    try:
        from django.contrib.auth import get_user_model
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        from django.urls import reverse

        User = get_user_model()

        # # Get all accountant emails
        # accountant_emails = list(
        #     User.objects.filter(
        #         is_accountant=True,
        #         is_active=True
        #     )
        #     .exclude(email="")
        #     .values_list("email", flat=True)
        # )
        #
        # # Fallback email
        # if not accountant_emails:
        #     accountant_emails = ["accounts@obluhc.com"]
        accountant_emails = ["accounts@obluhc.com"]

        # CC emails
        cc_emails = ["abhijay.obluhc@gmail.com","swasti.obluhc@gmail.com","nitin.a@obluhc.com","akshay@obluhc.com","operations@obluhc.com"]

        # Add requester email
        if request.user.email:
            cc_emails.append(request.user.email)

        # # Generate PI URL
        # proforma_url = request.build_absolute_uri(
        #     reverse("proforma_detail", args=[invoice.id])
        # )
        proforma_url="https://oblutools.com/proforma/"+str(invoice.id)

        subject = f"🚀 Dispatch Request: PI #{invoice.id} - {invoice.customer.name}"

        context = {
            "requested_by": request.user.get_full_name() or request.user.username,
            "invoice": invoice,
            "proforma_url": proforma_url,
        }

        # Render HTML template
        html_content = render_to_string(
            "proforma_invoice/dispatch_request_admin_email.html",
            context
        )

        # Plain text fallback
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

        # Create email
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email="proforma@oblutools.com",
            to=accountant_emails,
            cc=cc_emails
        )

        # Attach HTML
        msg.attach_alternative(html_content, "text/html")

        # Send email
        msg.send(fail_silently=False)

        print(f"✅ Dispatch email sent for PI #{invoice.id}")

    except Exception as e:
        print("❌ Dispatch email failed")
        print(str(e))

    # ================= SUCCESS MESSAGE =================
    messages.success(
        request,
        f"Dispatch request raised for Proforma #{invoice.id}. Accounts has been notified."
    )

    return redirect('proforma_list')

@login_required
def set_dispatch_status(request, pk, status):
    if not request.user.is_accountant:
        return redirect('home')

    invoice = get_object_or_404(ProformaInvoice, pk=pk)

    if invoice.dispatch_status == 'dispatched':
        messages.error(request, "Dispatched orders cannot be changed.")
        return redirect('proforma_invoice_dispatch')

    # Update Status
    if status == 'yes':
        invoice.dispatch_status = 'dispatched'
        invoice.dispatched_at = timezone.now()  # ✅ STOP THE CLOCK
        status_label = "DISPATCHED"
    elif status == 'no':
        # If Admin clicks NO, we move it to pending but keep the clock running
        invoice.dispatch_status = 'pending'
        status_label = "PENDING"
    else:
        return redirect('proforma_invoice_dispatch')

    invoice.save()

    # --- 📧 EMAIL LOGIC ---
    sp = invoice.customer.salesperson
    if sp and hasattr(sp, 'user') and sp.user.email:
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
            msg.send()
        except Exception as e:
            print(f"Email failed: {e}")

    return redirect('proforma_invoice_dispatch')

from django.db.models import Q

from django.db.models import Q
from django.contrib.auth import get_user_model

from django.db.models import Q
from django.contrib.auth import get_user_model


class ProformaInvoiceListViewForDispatch(LoginRequiredMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_list_dispatch.html"
    context_object_name = "invoices"

    def get_queryset(self):
        # 1. Start by filtering only for 'requested' and 'pending' statuses
        # We exclude 'processing' (drafts) and 'dispatched' (already done)
        qs = ProformaInvoice.objects.filter(
            dispatch_requested_at__isnull=False
        ).select_related("customer")

        # 2. Apply existing filters from the URL (Search by ID, User, etc.)
        f_id = self.request.GET.get('f_id')
        f_inv = self.request.GET.get('f_inv')
        f_user = self.request.GET.get('f_user')
        f_date = self.request.GET.get('f_date')
        sort_by = self.request.GET.get("sort_by")

        if f_id:
            qs = qs.filter(id__icontains=f_id)
        if f_inv:
            qs = qs.filter(id__icontains=f_inv)
        if f_user:
            # Filters by the username of the person who created the Proforma
            qs = qs.filter(created_by__icontains=f_user)
        if f_date:
            qs = qs.filter(date_created__date=f_date)

        # 3. Sorting
        if sort_by == "date_asc":
            qs = qs.order_by("date_created")
        else:
            # Default to newest requests first
            qs = qs.order_by("-dispatch_requested_at")

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Populate "Created By" dropdown
        unique_usernames = ProformaInvoice.objects.exclude(
            created_by__isnull=True
        ).values_list('created_by', flat=True).distinct()
        context['users'] = sorted(list(set(unique_usernames)))

        # Populate "Customer" dropdown
        context['customers'] = ProformaInvoice.objects.values(
            'customer__id', 'customer__name'
        ).distinct().order_by('customer__name')

        return context


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


def check_price_needs_approval(user, product, requested_price):
    """
    Logic to determine if a price needs approval.
    Returns: (needs_request, needs_accountant)
    """
    pricing = getattr(product, 'proforma_price', None)

    if not pricing:
        return True, False  # Default to needing approval if no rules set

    recommended_price = Decimal(str(pricing.price or 0))
    msrp = Decimal(str(pricing.msrp or 0))

    needs_req = False
    needs_acc = False

    # If requested price is lower than recommended, it needs admin review
    if requested_price < recommended_price:
        needs_req = True

    # If requested price is lower than MSRP, it needs deep discount review (Accountant)
    if requested_price < msrp:
        needs_acc = True

    return needs_req, needs_acc


class ProformaPriceChangeRequestCreateView(LoginRequiredMixin, FormView):
    template_name = "proforma_invoice/request_price_change.html"
    form_class = ProformaPriceChangeRequestForm

    def dispatch(self, request, *args, **kwargs):
        invoice_id = self.kwargs["invoice_id"]
        self.invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        if request.user.is_superuser:
            messages.error(request, "Super users cannot request price changes.")
            return redirect("proforma_detail", pk=self.invoice.id)

        # Check for pending requests
        if ProformaPriceChangeRequest.objects.filter(
                invoice=self.invoice,
                status="pending"
        ).exists():
            messages.warning(request, "There is already a pending request for this Proforma Invoice.")
            return redirect("proforma_detail", pk=self.invoice.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice

        # 1. Filter out items rejected by warehouse
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        items = self.invoice.items.select_related("product").exclude(
            product_id__in=rejected_stock_ids
        )

        # 2. Fetch latest price request history for this PI
        history_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('id')
        req_map = {req.product_id: req for req in history_requests}

        # 3. Fetch Historical Memory (Lowest price ever approved)
        from .models import ApprovedPriceMemory
        history_memory = ApprovedPriceMemory.objects.filter(customer=self.invoice.customer)
        memory_map = {m.product_id: m.min_approved_price for m in history_memory}

        for item in items:
            item.last_processed_req = req_map.get(item.product.id)
            item.last_ever_approved = memory_map.get(item.product.id)

        context["items"] = items

        # 4. Courier History
        context["courier_status_history"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").last()

        return context

    def form_valid(self, form):
        # req_reason might be empty if not provided in template
        req_reason = form.cleaned_data.get('reason', '') or "Price adjustment requested."
        any_needs_accountant = False

        # 1. Process Product Price Changes
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        items_to_process = self.invoice.items.select_related("product__proforma_price").exclude(
            product_id__in=rejected_stock_ids
        )

        for item in items_to_process:
            raw_val = self.request.POST.get(f"new_price_{item.id}")
            item_reason = self.request.POST.get(f"reason_{item.id}", "").strip()

            if raw_val and raw_val.strip():
                requested_price = Decimal(raw_val)
                # Note: ensure check_price_needs_approval is imported
                needs_req, needs_acc = check_price_needs_approval(self.request.user, item.product, requested_price)

                if needs_req:
                    pricing = getattr(item.product, 'proforma_price', None)
                    ProformaPriceChangeRequest.objects.create(
                        invoice=self.invoice,
                        customer=self.invoice.customer,
                        requested_by=self.request.user,
                        product=item.product,
                        is_product_request=True,
                        requested_price=requested_price,
                        recommended_price=pricing.price if pricing else 0,
                        msrp_snapshot=pricing.msrp if pricing else 0,
                        is_under_msrp=needs_acc,
                        reason=f"{req_reason}\n[Note: {item_reason}]" if item_reason else req_reason,
                        status="pending",
                    )
                    if needs_acc:
                        any_needs_accountant = True

        # 2. Process Courier Changes
        requested_courier_charge = self.request.POST.get("new_courier_charge")
        courier_reason = self.request.POST.get("courier_reason", "").strip()

        if requested_courier_charge and requested_courier_charge.strip():
            new_courier = Decimal(requested_courier_charge)
            # Only create if it actually changed
            if new_courier != self.invoice.courier_charge():
                ProformaPriceChangeRequest.objects.create(
                    invoice=self.invoice,
                    customer=self.invoice.customer,
                    requested_by=self.request.user,
                    is_product_request=False,
                    requested_courier_charge=new_courier,
                    reason=f"{req_reason}\n[Courier: {courier_reason}]" if courier_reason else req_reason,
                    status="pending",
                )

        # 3. Email Notification Logic
        self.send_request_email(any_needs_accountant, req_reason)

        messages.success(self.request, "Your price change request has been submitted.")
        return redirect("proforma_detail", pk=self.invoice.id)

    def form_valid(self, form):
        # 1. INITIALIZE FLAGS AND DATA
        # 'request_created' tracks if we actually saved anything to the database
        # 'any_needs_accountant' tracks if any product price is below MSRP
        request_created = False
        any_needs_accountant = False

        # Get the 'General Justification' from the bottom of the form (it is optional now)
        general_req_reason = form.cleaned_data.get('reason', '').strip()

        # 2. FILTER OUT REJECTED STOCK ITEMS
        # We don't want users requesting price changes for items the warehouse already rejected
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        # Get items that are NOT in the rejected list
        items_to_process = self.invoice.items.select_related("product__proforma_price").exclude(
            product_id__in=rejected_stock_ids
        )

        # 3. PROCESS PRODUCT PRICE CHANGES
        for item in items_to_process:
            # Look for 'new_price_ID' and 'reason_ID' in the POST data
            raw_price = self.request.POST.get(f"new_price_{item.id}")
            item_specific_reason = self.request.POST.get(f"reason_{item.id}", "").strip()

            # Check if the user actually typed a price in this row
            if raw_price and raw_price.strip():
                try:
                    requested_price = Decimal(raw_price)

                    # Logic to check if this price needs Admin or Accountant approval
                    needs_req, needs_acc = check_price_needs_approval(self.request.user, item.product, requested_price)

                    if needs_req:
                        # Fetch Product Metadata for the snapshot
                        pricing = getattr(item.product, 'proforma_price', None)
                        standard_price = pricing.price if pricing else Decimal("0.00")
                        msrp = pricing.msrp if pricing else Decimal("0.00")

                        # Merge the Reasons:
                        # Priority 1: General Reason + Item Reason
                        # Priority 2: Just Item Reason
                        # Priority 3: Just General Reason
                        if general_req_reason and item_specific_reason:
                            combined_reason = f"{general_req_reason}\n[Item Note: {item_specific_reason}]"
                        elif item_specific_reason:
                            combined_reason = item_specific_reason
                        else:
                            combined_reason = general_req_reason

                        # Create the database record for this product
                        ProformaPriceChangeRequest.objects.create(
                            invoice=self.invoice,
                            customer=self.invoice.customer,
                            requested_by=self.request.user,
                            product=item.product,
                            is_product_request=True,
                            requested_price=requested_price,
                            recommended_price=standard_price,
                            msrp_snapshot=msrp,
                            is_under_msrp=needs_acc,
                            reason=combined_reason,
                            status="pending",
                        )
                        request_created = True
                        if needs_acc:
                            any_needs_accountant = True

                except (InvalidOperation, ValueError):
                    # Skip if the user entered invalid text instead of a number
                    continue

        # 4. PROCESS COURIER CHARGE CHANGES
        raw_courier = self.request.POST.get("new_courier_charge")
        courier_note = self.request.POST.get("courier_reason", "").strip()

        if raw_courier and raw_courier.strip():
            try:
                new_courier_val = Decimal(raw_courier)

                # Only create a request if the value is different from the current system charge
                current_system_charge = self.invoice.courier_charge() if callable(
                    self.invoice.courier_charge) else self.invoice.courier_charge

                if new_courier_val != current_system_charge:
                    # Combine courier-specific reason with the general reason
                    if general_req_reason and courier_note:
                        combined_courier_reason = f"{general_req_reason}\n[Courier Note: {courier_note}]"
                    else:
                        combined_courier_reason = courier_note or general_req_reason

                    ProformaPriceChangeRequest.objects.create(
                        invoice=self.invoice,
                        customer=self.invoice.customer,
                        requested_by=self.request.user,
                        is_product_request=False,
                        requested_courier_charge=new_courier_val,
                        reason=combined_courier_reason,
                        status="pending",
                    )
                    request_created = True
            except (InvalidOperation, ValueError):
                pass

        # 5. FINALIZATION: EMAIL AND MESSAGES
        if request_created:
            # ROUTING: If anything is under MSRP, notify the Accountant (Swasti)
            # Otherwise, notify the standard Reviewer (Bhavya)
            if any_needs_accountant:
                to_emails = ["swasti.obluhc@gmail.com"]
                subject_prefix = "🚨 DEEP DISCOUNT - Approval Required"
            else:
                to_emails = ["bhavya.obluhc@gmail.com"]
                subject_prefix = "🔔 Price Change Request"

            # Send Email
            try:
                email_context = {
                    "invoice": self.invoice,
                    "requested_by": self.request.user,
                    "reason": general_req_reason or "Individual row reasons provided.",
                    "review_url": "https://oblutools.com/proforma/price-change-requests/"
                }
                html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
                subject = f"{subject_prefix} (Proforma #{self.invoice.id})"

                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Email failed to send: {e}")

            messages.success(self.request, "Success! Your price change requests have been submitted.")
        else:
            # If the user clicked submit but didn't actually fill any values
            messages.info(self.request, "No changes detected. No requests were created.")

        # Always redirect back to the Proforma detail page
        return redirect("proforma_detail", pk=self.invoice.id)

    def send_request_email(self, is_deep_discount, reason):
        if is_deep_discount:
            to_emails = ["swasti.obluhc@gmail.com"]
            subject_prefix = "🚨 DEEP DISCOUNT"
        else:
            to_emails = ["bhavya.obluhc@gmail.com"]
            subject_prefix = "🔔 Price Request"

        try:
            email_context = {
                "invoice": self.invoice,
                "requested_by": self.request.user,
                "reason": reason,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
            subject = f"{subject_prefix} (Proforma #{self.invoice.id})"
            from django.core.mail import EmailMultiAlternatives
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Email error: {e}")
# View for accountants to list all Proforma price change requests
# ----------------------------------------------------------

class ProformaPriceChangeRequestCreateView(LoginRequiredMixin, FormView):
    template_name = "proforma_invoice/request_price_change.html"
    form_class = ProformaPriceChangeRequestForm

    def dispatch(self, request, *args, **kwargs):
        invoice_id = self.kwargs["invoice_id"]
        self.invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        if request.user.is_superuser:
            messages.error(request, "Super users cannot request price changes.")
            return redirect("proforma_detail", pk=self.invoice.id)

        # Check for pending requests
        if ProformaPriceChangeRequest.objects.filter(
                invoice=self.invoice,
                status="pending"
        ).exists():
            messages.warning(request, "There is already a pending request for this Proforma Invoice.")
            return redirect("proforma_detail", pk=self.invoice.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice

        # 1. Filter out items rejected by warehouse
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        items = self.invoice.items.select_related("product").exclude(
            product_id__in=rejected_stock_ids
        )

        # 2. Fetch latest price request history for this PI
        history_requests = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('id')
        req_map = {req.product_id: req for req in history_requests}

        # 3. Fetch Historical Memory (Lowest price ever approved)
        from .models import ApprovedPriceMemory
        history_memory = ApprovedPriceMemory.objects.filter(customer=self.invoice.customer)
        memory_map = {m.product_id: m.min_approved_price for m in history_memory}

        for item in items:
            item.last_processed_req = req_map.get(item.product.id)
            item.last_ever_approved = memory_map.get(item.product.id)

        context["items"] = items

        # 4. Courier History
        context["courier_status_history"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").last()

        return context

    from decimal import Decimal

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invoice"] = self.invoice

        # 1. Filter out items rejected by warehouse (don't show them for price changes)
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        items = self.invoice.items.select_related("product").exclude(
            product_id__in=rejected_stock_ids
        )

        # 2. Fetch ALL past requests for this PI (History)
        all_history = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=True
        ).exclude(status="pending").order_by('-created_at')

        # Group history by product
        history_map = defaultdict(list)
        for req in all_history:
            history_map[req.product_id].append(req)

        # 3. Fetch "Best Price Memory" for benchmarking
        from .models import ApprovedPriceMemory
        history_memory = ApprovedPriceMemory.objects.filter(customer=self.invoice.customer)
        memory_map = {m.product_id: m.min_approved_price for m in history_memory}

        # Attach history and memory to items for easy template access
        for item in items:
            item.price_history = history_map.get(item.product.id, [])
            item.last_ever_approved = memory_map.get(item.product.id)

        context["items"] = items

        # 4. Pull all past Courier history
        context["courier_history"] = ProformaPriceChangeRequest.objects.filter(
            invoice=self.invoice,
            is_product_request=False
        ).exclude(status="pending").order_by('-created_at')

        return context

    def form_valid(self, form):
        # 1. INITIALIZE FLAGS AND DATA
        request_created = False  # Tracks if any PENDING requests were made
        any_needs_accountant = False
        is_auto_approved_any = False  # Tracks if we updated prices immediately

        general_req_reason = form.cleaned_data.get('reason', '').strip()

        # 2. PRE-FETCH DATA FOR COMPARISON
        # Get the lowest prices ever approved for this specific customer
        memory_map = {
            m.product_id: m.min_approved_price
            for m in ApprovedPriceMemory.objects.filter(customer=self.invoice.customer)
        }

        # Filter out items rejected by warehouse
        rejected_stock_ids = self.invoice.stock_requests.filter(
            status="rejected"
        ).values_list('product_id', flat=True)

        items_to_process = self.invoice.items.select_related("product__proforma_price").exclude(
            product_id__in=rejected_stock_ids
        )

        # 3. PROCESS PRODUCT PRICE CHANGES
        for item in items_to_process:
            raw_price = self.request.POST.get(f"new_price_{item.id}")
            item_specific_reason = self.request.POST.get(f"reason_{item.id}", "").strip()

            if raw_price is not None and raw_price.strip() != "":
                try:
                    requested_price = Decimal(raw_price)

                    # Get Product Pricing Metadata
                    pricing = getattr(item.product, 'proforma_price', None)
                    system_price = pricing.price if pricing else Decimal("0.00")
                    msrp = pricing.msrp if pricing else Decimal("0.00")
                    lowest_memory_price = memory_map.get(item.product.id)

                    # --- AUTO-APPROVAL LOGIC ---
                    # A: Price is >= standard system price
                    # B: Price is >= previously approved best price for this customer
                    is_auto_approved = False
                    if requested_price >= system_price:
                        is_auto_approved = True
                    elif lowest_memory_price and requested_price >= lowest_memory_price:
                        is_auto_approved = True

                    # Determine the Reason string
                    if general_req_reason and item_specific_reason:
                        combined_reason = f"{general_req_reason}\n[Item Note: {item_specific_reason}]"
                    elif item_specific_reason:
                        combined_reason = item_specific_reason
                    else:
                        combined_reason = general_req_reason or "Price adjustment"

                    if is_auto_approved:
                        # ACTION: AUTO-APPROVE
                        ProformaPriceChangeRequest.objects.create(
                            invoice=self.invoice,
                            customer=self.invoice.customer,
                            requested_by=self.request.user,
                            product=item.product,
                            is_product_request=True,
                            requested_price=requested_price,
                            recommended_price=system_price,
                            msrp_snapshot=msrp,
                            reason=f"[AUTO-APPROVED] {combined_reason}",
                            status="approved",
                            reviewed_by=self.request.user,
                            reviewed_at=timezone.now()
                        )

                        # Update the Item price immediately so Detail View reflects it
                        item.current_price = requested_price
                        item.save()

                        is_auto_approved_any = True
                        # Mark Invoice as having modified prices
                        if not self.invoice.is_price_altered:
                            self.invoice.is_price_altered = True
                            self.invoice.save()

                    else:
                        # ACTION: CHECK IF PENDING REQUEST IS NEEDED
                        # This function determines if the user's role requires approval for this price
                        needs_req, needs_acc = check_price_needs_approval(self.request.user, item.product,
                                                                          requested_price)

                        if needs_req:
                            ProformaPriceChangeRequest.objects.create(
                                invoice=self.invoice,
                                customer=self.invoice.customer,
                                requested_by=self.request.user,
                                product=item.product,
                                is_product_request=True,
                                requested_price=requested_price,
                                recommended_price=system_price,
                                msrp_snapshot=msrp,
                                is_under_msrp=needs_acc,
                                reason=combined_reason,
                                status="pending",
                            )
                            request_created = True
                            if needs_acc:
                                any_needs_accountant = True

                except (InvalidOperation, ValueError):
                    continue

        # 4. PROCESS COURIER CHARGE CHANGES
        raw_courier = self.request.POST.get("new_courier_charge")
        courier_note = self.request.POST.get("courier_reason", "").strip()

        if raw_courier and raw_courier.strip():
            try:
                new_courier_val = Decimal(raw_courier)
                current_system_charge = self.invoice.courier_charge() if callable(
                    self.invoice.courier_charge) else self.invoice.courier_charge

                if new_courier_val != current_system_charge:
                    # Logic: If user increases courier, auto-approve. If they decrease, set to pending.
                    is_courier_auto = new_courier_val >= current_system_charge

                    if general_req_reason and courier_note:
                        combined_courier_reason = f"{general_req_reason}\n[Courier Note: {courier_note}]"
                    else:
                        combined_courier_reason = courier_note or general_req_reason

                    ProformaPriceChangeRequest.objects.create(
                        invoice=self.invoice,
                        customer=self.invoice.customer,
                        requested_by=self.request.user,
                        is_product_request=False,
                        requested_courier_charge=new_courier_val,
                        reason=combined_courier_reason,
                        status="approved" if is_courier_auto else "pending",
                        reviewed_at=timezone.now() if is_courier_auto else None
                    )

                    if not is_courier_auto:
                        request_created = True
                    else:
                        is_auto_approved_any = True
            except (InvalidOperation, ValueError):
                pass

        # 5. FINALIZATION: EMAIL AND MESSAGES
        if request_created:
            # Determine notification routing
            if any_needs_accountant:
                to_emails = ["swasti.obluhc@gmail.com"]
                subject_prefix = "🚨 DEEP DISCOUNT - Approval Required"
            else:
                to_emails = ["bhavya.obluhc@gmail.com"]
                subject_prefix = "🔔 Price Change Request"

            # Send Email for the Pending requests
            try:
                email_context = {
                    "invoice": self.invoice,
                    "requested_by": self.request.user,
                    "reason": general_req_reason or "Individual row reasons provided.",
                    "review_url": "https://oblutools.com/proforma/price-change-requests/"
                }
                html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
                subject = f"{subject_prefix} (Proforma #{self.invoice.id})"

                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Email failed to send: {e}")

            messages.success(self.request, "Price change requests submitted for admin approval.")

        elif is_auto_approved_any:
            messages.success(self.request, "Prices updated successfully (Auto-approved based on history/system rates).")
        else:
            messages.info(self.request, "No changes were detected.")

        return redirect("proforma_detail", pk=self.invoice.id)

    def send_request_email(self, is_deep_discount, reason):
        if is_deep_discount:
            to_emails = ["swasti.obluhc@gmail.com"]
            subject_prefix = "🚨 DEEP DISCOUNT"
        else:
            to_emails = ["bhavya.obluhc@gmail.com"]
            subject_prefix = "🔔 Price Request"

        try:
            email_context = {
                "invoice": self.invoice,
                "requested_by": self.request.user,
                "reason": reason,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
            subject = f"{subject_prefix} (Proforma #{self.invoice.id})"
            from django.core.mail import EmailMultiAlternatives
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Email error: {e}")

class ProformaPriceChangeRequestListView(AccountantRequiredMixin, ListView):
    model = ProformaPriceChangeRequest
    template_name = "proforma_invoice/price_change_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        # Default ordering: Latest first
        queryset = ProformaPriceChangeRequest.objects.select_related(
            "invoice", "requested_by", "reviewed_by"
        ).prefetch_related(
            "invoice__items__product",
            "invoice__remarks__user"
        ).order_by("-created_at")

        # logic: Super Admin sees all, but we can default filter
        if self.request.user.is_superuser:
            return queryset # superuser sees all by default now

            # If no specific filter is selected, show 'Under MSRP' by default
            # if not self.request.GET.get('f_status'):
            #     queryset = queryset.filter(is_under_msrp=True)

        # Get values from the URL
        f_id = self.request.GET.get('f_id')
        f_inv = self.request.GET.get('f_inv')
        f_user = self.request.GET.get('f_user')
        f_status = self.request.GET.get('f_status')
        f_date = self.request.GET.get('f_date')

        # Apply Filters
        if f_id:
            queryset = queryset.filter(id__icontains=f_id)
        if f_inv:
            queryset = queryset.filter(invoice__id__icontains=f_inv)
        if f_user:
            queryset = queryset.filter(requested_by__username__icontains=f_user)
        if f_status:
            queryset = queryset.filter(status=f_status)
        if f_date:
            queryset = queryset.filter(created_at__date=f_date)

        return queryset

    # In your views.py (the one that renders the dashboard)
    from django.db.models import Prefetch

    def price_change_requests_list(request):
        # Get all requests
        all_requests = ProformaPriceChangeRequest.objects.all().order_by('-created_at')

        # Logic to group them by Invoice in memory
        grouped_data = {}
        for req in all_requests:
            inv_id = req.invoice.id
            if inv_id not in grouped_data:
                grouped_data[inv_id] = {
                    'invoice': req.invoice,
                    'items': [],
                    'status': 'PENDING',  # You can calculate aggregate status
                    'requested_by': req.requested_by,
                    'created_at': req.created_at,
                }
            grouped_data[inv_id]['items'].append(req)

        return render(request, 'price_change_request_list.html', {'grouped_requests': grouped_data.values()})

class ProformaPriceChangeRequestListView(AccountantRequiredMixin, ListView):
    model = ProformaPriceChangeRequest
    template_name = "proforma_invoice/price_change_request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        # Default ordering: Latest first
        queryset = ProformaPriceChangeRequest.objects.select_related(
            "invoice", "requested_by", "reviewed_by", "customer"
        ).prefetch_related(
            "invoice__items__product",
            "invoice__remarks__user"
        ).order_by("-created_at")

        # logic: Super Admin sees all, but we can default filter
        # if self.request.user.is_superuser:
        #     return queryset # superuser sees all by default now

            # If no specific filter is selected, show 'Under MSRP' by default
            # if not self.request.GET.get('f_status'):
            #     queryset = queryset.filter(is_under_msrp=True)

        # Get values from the URL
        f_id = self.request.GET.get('f_id')
        if f_id: queryset = queryset.filter(id__icontains=f_id)

        f_inv = self.request.GET.get('f_inv')
        f_user = self.request.GET.get('f_user')
        f_status = self.request.GET.get('f_status')
        f_date = self.request.GET.get('f_date')

        # Apply Filters
        if f_id:
            queryset = queryset.filter(id__icontains=f_id)
        if f_inv:
            queryset = queryset.filter(invoice__id__icontains=f_inv)
        if f_user:
            queryset = queryset.filter(requested_by__username__icontains=f_user)
        if f_status:
            queryset = queryset.filter(status=f_status)
        if f_date:
            queryset = queryset.filter(created_at__date=f_date)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Use the already-filtered list from the ListView
        queryset = self.object_list

        grouped_data = {}
        for req in queryset:
            inv_id = req.invoice.id
            if inv_id not in grouped_data:
                grouped_data[inv_id] = {
                    'invoice': req.invoice,
                    'requests': [],
                    'all_reviewers': [],
                    'is_pending': False,
                    'start_time': req.created_at,
                    'end_time': None,
                }

            group = grouped_data[inv_id]
            group['requests'].append(req)

            if req.reviewed_by:
                group['all_reviewers'].append(req.reviewed_by.username)

            if req.status == 'pending':
                group['is_pending'] = True

            # Use reviewed_at from your model
            if req.status != 'pending' and req.reviewed_at:
                if not group['end_time'] or req.reviewed_at > group['end_time']:
                    group['end_time'] = req.reviewed_at

        for group in grouped_data.values():
            group['unique_reviewers'] = list(dict.fromkeys(group['all_reviewers']))

            # Duration calc
            calc_end = group['end_time'] if (not group['is_pending'] and group['end_time']) else timezone.now()
            diff = calc_end - group['start_time']
            group['duration_display'] = f"{diff.days}d {diff.seconds // 3600}h {(diff.seconds // 60) % 60}m"
            group['is_running'] = group['is_pending']

        # THIS NAME MUST MATCH THE TEMPLATE
        context['grouped_requests'] = grouped_data.values()
        return context
        # In your views.py (the one that renders the dashboard)
    from django.db.models import Prefetch

    def price_change_requests_list(request):
        # Get all requests
        all_requests = ProformaPriceChangeRequest.objects.all().order_by('-created_at')

        # Logic to group them by Invoice in memory
        grouped_data = {}
        for req in all_requests:
            inv_id = req.invoice.id
            if inv_id not in grouped_data:
                grouped_data[inv_id] = {
                    'invoice': req.invoice,
                    'items': [],
                    'status': 'PENDING',  # You can calculate aggregate status
                    'requested_by': req.requested_by,
                    'created_at': req.created_at,
                }
            grouped_data[inv_id]['items'].append(req)

        return render(request, 'price_change_request_list.html', {'grouped_requests': grouped_data.values()})

def can_user_approve_request(user, price_request):
    if user.is_superuser or getattr(user, 'is_accountant', False):
        return True
    if user.username.lower() == "bhavya" and not price_request.needs_accountant_approval:
        return True
    return False


class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"], status="pending")
        invoice = price_request.invoice

        # --- 1. PERMISSION CHECK (MSRP BLOCKER) ---
        if price_request.is_under_msrp and not request.user.is_superuser:
            try:
                subject = f"🚨 Approval Needed: Below MSRP Request (Inv #{invoice.id})"
                to_email = ["swasti.obluhc@gmail.com"]
                context = {
                    "price_request": price_request,
                    "accountant": request.user.username,
                    "site_url": "https://oblutools.com/proforma/price-change-requests/"
                }
                html_content = render_to_string("proforma_invoice/msrp_notification_email.html", context)
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_email)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
                messages.success(request, "✅ Below MSRP detected. Notification sent to Nitin Sir.")
            except Exception as e:
                messages.error(request, f"Mail failed: {str(e)}")
            return redirect("proforma_price_change_requests")

        # --- 2. PROCESSING GRANULAR APPROVAL ---
        with transaction.atomic():
            updated_json = price_request.requested_product_prices

            if updated_json:
                for item_id, data in updated_json.items():
                    try:
                        item = invoice.items.get(id=item_id)

                        # Data Upgrade Check (Old string vs New Dict)
                        if isinstance(data, dict):
                            req_p = Decimal(str(data.get('requested_price', 0)))
                            rec_p = Decimal(str(data.get('recommended_price', 0)))
                        else:
                            req_p = Decimal(str(data))
                            pricing = getattr(item.product, 'proforma_price', None)
                            rec_p = pricing.price if pricing else Decimal(0)
                            # Upgrade JSON on the fly
                            updated_json[item_id] = {'requested_price': str(req_p), 'recommended_price': str(rec_p)}
                            data = updated_json[item_id]

                        # --- FETCH DECISION FROM POST ---
                        # IMPORTANT: Matches <input name="status_{{item.id}}">
                        item_decision = request.POST.get(f'status_{item_id}', 'rejected')  # Default to rejected for safety

                        # DEBUG: See decisions in terminal
                        print(f"ITEM {item_id} DECISION: {item_decision}")

                        if item_decision == 'approved':
                            final_price = req_p
                            data['decision'] = 'approved'

                            # Update Memory for future auto-approvals
                            memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                                customer=invoice.customer,
                                product=item.product,
                                defaults={'min_approved_price': req_p, 'base_price_at_approval': rec_p}
                            )
                            if not created and memory_obj.base_price_at_approval == rec_p:
                                if req_p < memory_obj.min_approved_price:
                                    memory_obj.min_approved_price = req_p
                                    memory_obj.save()
                        else:
                            # If 'rejected' or data is missing from POST
                            final_price = rec_p
                            data['decision'] = 'rejected'

                        # Update Invoice Item snapshot
                        item.current_price = final_price
                        item.custom_price = float(final_price)
                        item.save()

                    except Exception as e:
                        print(f"Error on item {item_id}: {e}")
                        continue

            # --- 3. COURIER DECISION ---
            courier_decision = request.POST.get(f'courier_status_{price_request.id}', 'rejected')
            print(f"COURIER DECISION: {courier_decision}")

            if courier_decision == 'approved' and price_request.requested_courier_charge is not None:
                invoice.courier_charge = price_request.requested_courier_charge
                price_request.courier_status = 'approved'
            else:
                # Revert to original if rejected or missing
                price_request.courier_status = 'rejected'

            # --- 4. FINALIZE REQUEST STATUS ---
            price_request.requested_product_prices = updated_json
            price_request.status = "approved"  # The 'Request' itself is now processed
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()

            # Unlock the invoice for the Salesperson
            invoice.is_price_altered = True
            invoice.save()

        # --- 5. REMARKS & NOTIFICATIONS ---
        remark_text = request.POST.get('review_remark', '').strip()
        if remark_text:
            append_remark(invoice, request.user, f"REVIEW COMPLETED: {remark_text}")
        else:
            append_remark(invoice, request.user, "Price change request processed.")

        # Email logic
        try:
            invoice_url = "https://oblutools.com/proforma/" + str(invoice.id)
            email_context = {
                "request_obj": price_request,
                "invoice": invoice,
                "user": price_request.requested_by,
                "status": "reviewed",
                "remark": remark_text,
                "invoice_url": invoice_url,
            }
            html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
            subject = f"✅ Price Request Reviewed (Proforma #{invoice.id})"
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", [price_request.requested_by.email])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Email failed: {e}")

        messages.success(request, f"Decisions saved for Invoice #{invoice.id}")
        return redirect("proforma_price_change_requests")


class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        # 1. Fetch the request and linked invoice
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"], status="pending")
        invoice = price_request.invoice

        # --- 2. PERMISSION CHECK (MSRP BLOCKER) ---
        # Keeps your original logic for Nitin Sir's final unlock
        if price_request.is_under_msrp and not request.user.is_superuser:
            try:
                subject = f"🚨 Approval Needed: Below MSRP Request (Inv #{invoice.id})"
                to_email = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]
                context = {
                    "price_request": price_request,
                    "accountant": request.user.username,
                    "site_url": "https://oblutools.com/proforma/price-change-requests/"
                }
                html_content = render_to_string("proforma_invoice/msrp_notification_email.html", context)
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_email)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
                messages.success(request, "✅ Request contains items below MSRP. Notification sent to Nitin Sir.")
            except Exception as e:
                messages.error(request, f"Mail failed: {str(e)}")
            return redirect("proforma_price_change_requests")

        # --- 3. PROCESSING APPROVAL ---
        # Get decision from the dynamic name "status_ID" used in HTML
        decision = request.POST.get(f'status_{price_request.id}', 'approved')

        with transaction.atomic():
            # Handle Product Price Change
            if price_request.is_product_request and price_request.product:
                # Find the specific item in the invoice matching this product
                item = invoice.items.filter(product=price_request.product).first()

                if item:
                    # Get decision from POST (matches name="status_{{req.id}}")
                    # Note: We use the price_request.id because the model is now per-item
                    # item_decision = request.POST.get(f'status_{price_request.id}', 'approved')

                    if decision  == 'approved':
                        final_price = price_request.requested_price
                        rec_p = price_request.recommended_price or Decimal(0)

                        # UPDATE MEMORY
                        memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                            customer=invoice.customer,
                            product=item.product,
                            defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                        )
                        if not created and memory_obj.base_price_at_approval == rec_p:
                            if final_price < memory_obj.min_approved_price:
                                memory_obj.min_approved_price = final_price
                                memory_obj.save()

                        # Apply to invoice item
                        item.current_price = final_price
                        item.custom_price = float(final_price)
                        item.save()

                    price_request.status = decision  # 'approved' or 'rejected'
                else:
                    messages.error(request, f"Product {price_request.product} not found in this invoice.")

            # --- 4. COURIER DECISION ---
            # CASE B: Courier Charge Change
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    invoice.courier_charge = price_request.requested_courier_charge

                # Update status and specific courier flag if model has it
                price_request.status = decision
                if hasattr(price_request, 'courier_status'):
                    price_request.courier_status = decision

            # --- 5. FINALIZE REQUEST ---
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            # Identify who approved for the "Reviewed By" column
            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()

            # Unlock the Proforma for viewing/dispatch by Salesperson
            invoice.is_price_altered = True
            invoice.save()

        # --- 6. REMARKS & EMAILS ---
        remark_text = request.POST.get('review_remark', '').strip()
        history_summary = f"Price review finished. Decisions saved to history."
        if remark_text:
            append_remark(invoice, request.user, f"REVIEW NOTES: {remark_text}")
        else:
            append_remark(invoice, request.user, history_summary)

        # Notify Salesperson
        try:
            invoice_url = "https://oblutools.com/proforma/" + str(invoice.id)
            email_context = {
                "request_obj": price_request,
                "invoice": invoice,
                "user": price_request.requested_by,
                "status": "reviewed",
                "remark": remark_text or history_summary,
                "invoice_url": invoice_url,
            }
            html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
            subject = f"✅ Price Request Decision (Proforma #{invoice.id})"
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", [price_request.requested_by.email])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Notification Email failed: {e}")

        messages.success(request, f"Decisions finalized for Invoice #{invoice.id}")
        return redirect("proforma_price_change_requests")

class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        # 1. Fetch the request and linked invoice
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"], status="pending")
        invoice = price_request.invoice
        # Get decision: 'approved' or 'rejected'
        decision = request.POST.get(f'status_{price_request.id}', 'approved')
        # --- 1. REJECT LOGIC (Always allowed for everyone) ---
        if decision == 'rejected':
            price_request.status = 'rejected'
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()
            price_request.accountant_approved = False  # Reset flag
            price_request.save()
            messages.info(request,
                          f"Request for {price_request.product.name if price_request.product else 'Courier'} rejected.")
            return redirect("proforma_price_change_requests")

        # --- 2. APPROVE LOGIC ---
        if decision == 'approved':
            # CASE A: Accountant (Non-Admin) approving Under-MSRP
            if not request.user.is_superuser and price_request.is_under_msrp:
                try:
                    to_emails = ["abhijay.obluhc@gmail.com","nitin.a@obluhc.com"]  # Add Nitin Sir's email here
                    email_context = {
                        "invoice": invoice,
                        "price_request": price_request,
                        "accountant_name": request.user.username,
                        "review_url": "https://oblutools.com/proforma/price-change-requests/"
                    }
                    # Ensure this template filename is correct in your folder
                    html_content = render_to_string("proforma_invoice/msrp_notification_email.html", email_context)

                    subject = f"⚖️ MSRP Review Required: Inv #{invoice.id}"
                    msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
                    msg.attach_alternative(html_content, "text/html")
                    msg.send()

                    price_request.accountant_approved = True  # This locks the UI
                    price_request.save()
                    messages.success(request, "✅ Under MSRP: Admin notified for final approval.")
                except Exception as e:
                    messages.error(request, f"Email failed: {str(e)}")
                return redirect("proforma_price_change_requests")

        # --- 3. PROCESSING APPROVAL ---

        with transaction.atomic():
            # Handle Product Price Change
            if price_request.is_product_request and price_request.product:
                # Find the specific item in the invoice matching this product
                item = invoice.items.filter(product=price_request.product).first()

                if item:
                    # Get decision from POST (matches name="status_{{req.id}}")
                    # Note: We use the price_request.id because the model is now per-item
                    # item_decision = request.POST.get(f'status_{price_request.id}', 'approved')

                    if decision  == 'approved':
                        final_price = price_request.requested_price
                        rec_p = price_request.recommended_price or Decimal(0)

                        # UPDATE MEMORY
                        memory_obj, created = ApprovedPriceMemory.objects.get_or_create(
                            customer=invoice.customer,
                            product=item.product,
                            defaults={'min_approved_price': final_price, 'base_price_at_approval': rec_p}
                        )
                        if not created and memory_obj.base_price_at_approval == rec_p:
                            if final_price < memory_obj.min_approved_price:
                                memory_obj.min_approved_price = final_price
                                memory_obj.save()

                        # Apply to invoice item
                        item.current_price = final_price
                        item.custom_price = float(final_price)
                        item.save()

                    price_request.status = decision  # 'approved' or 'rejected'
                else:
                    messages.error(request, f"Product {price_request.product} not found in this invoice.")

            # --- 4. COURIER DECISION ---
            # CASE B: Courier Charge Change
            elif not price_request.is_product_request and price_request.requested_courier_charge is not None:
                if decision == 'approved':
                    invoice.courier_charge = price_request.requested_courier_charge

                # Update status and specific courier flag if model has it
                price_request.status = decision
                if hasattr(price_request, 'courier_status'):
                    price_request.courier_status = decision

            # --- 5. FINALIZE REQUEST ---
            price_request.reviewed_by = request.user
            price_request.reviewed_at = timezone.now()

            # Identify who approved for the "Reviewed By" column
            if request.user.is_superuser:
                price_request.superuser_approved = True
            else:
                price_request.accountant_approved = True

            price_request.save()

            # Unlock the Proforma for viewing/dispatch by Salesperson
            invoice.is_price_altered = True
            invoice.save()

        # --- 6. REMARKS & EMAILS ---
        remark_text = request.POST.get('review_remark', '').strip()
        # history_summary = f"Price review finished. Decisions saved to history."
        if remark_text:
            append_remark(invoice, request.user, f"REVIEW NOTES: {remark_text}")
        # else:
        #     append_remark(invoice, request.user, history_summary)

        # Notify Salesperson
        try:
            invoice_url = "https://oblutools.com/proforma/" + str(invoice.id)
            email_context = {
                "request_obj": price_request,
                "invoice": invoice,
                "user": price_request.requested_by,
                "status": "reviewed",
                "remark": remark_text ,
                "invoice_url": invoice_url,
            }
            html_content = render_to_string("proforma_invoice/price_change_request_status_email.html", email_context)
            subject = f"✅ Price Request Decision (Proforma #{invoice.id})"
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", [price_request.requested_by.email,'abhijay.obluhc@gmail.com'])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Notification Email failed: {e}")

        # --- FINAL REVIEW CHECK ---
        # Check if there are ANY other items for this invoice still 'pending'
        any_pending = invoice.price_requests.filter(status='pending').exists()

        if not any_pending:
            # ---------------- ALL REVIEWED EMAIL ----------------
            # Trigger only when the last item is processed
            try:
                to_emails = [price_request.requested_by.email]
                cc_emails = ["swasti.obluhc@gmail.com"]  # Accountant CC

                # Gather all requests for this invoice to show in the email table
                final_requests = invoice.price_requests.all()

                email_context = {
                    "invoice": invoice,
                    "requested_by": price_request.requested_by,
                    "reviewed_by": request.user,
                    "requests": final_requests,
                    "review_url": f"https://oblutools.com/proforma/{invoice.id}/",
                }

                html_content = render_to_string(
                    "proforma_invoice/price_review_complete_email.html",
                    email_context
                )

                subject = f"✅ Price Change Review Complete (Proforma #{invoice.id})"
                from_email = "proforma@oblutools.com"

                msg = EmailMultiAlternatives(
                    subject,
                    "",
                    from_email,
                    to_emails,
                    cc=cc_emails
                )
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Final Notification Email failed: {e}")
            # -----------------------------------------------------

        messages.success(request, f"Decisions finalized for Invoice #{invoice.id}")
        return redirect("proforma_price_change_requests")


#stock under 50% not allowed to non super user
class ProformaPriceChangeRequestApproveView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        parent_obj = price_request.invoice or price_request.quotation

        if price_request.status != "pending":
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

            messages.warning(request, f"⚠️ {violation_type}: Nitin Sir notified for final approval.")
        except Exception as e:
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
                html_content = render_to_string("proforma_invoice/q.html", email_context)
                subject = f"✅ Reviewed: #{parent_obj.id} ({parent_obj.customer.name})"
                msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails, cc=cc_emails)
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                print(f"Summary Email Error: {e}")
        pass


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


def notify_remark_added(request_obj, author):
    """
    Bi-directional notification system.
    If Admin (Superuser) adds remark -> SP gets email with link to proforma_list.
    If SP adds remark -> Superuser gets email with link to price_change dashboard.
    """
    invoice = request_obj.invoice
    User = get_user_model()

    # Determine roles
    is_admin_author = author.is_superuser

    # 1. Logic for ADMIN -> SP
    if is_admin_author:
        try:
            sp_user = User.objects.get(username=invoice.created_by)
            recipient_emails = [sp_user.email] if sp_user.email else []
            subject = f"🔔 Admin Remark: Proforma #{invoice.id}"
            # Custom message requested
            headline = f"{author.username} added this remark on your price change request. Review it."
            # Link SP to the proforma list where they can reply
            action_url = "https://oblutools.com/proforma/proformas/"
        except User.DoesNotExist:
            return

    # 2. Logic for SP -> ADMIN
    else:
        # Notify only Superusers as requested
        recipient_emails = list(User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(email="").values_list('email', flat=True))

        subject = f"💬 SP Response: Proforma #{invoice.id}"
        headline = f"Salesperson {author.username} has replied to your remark."
        # Link Admin to the price change review page
        action_url = "https://oblutools.com/proforma/price-change-requests/"

    if not recipient_emails:
        return

    # Prepare Context
    newest_remark = request_obj.review_remark.split('\n')[0] if request_obj.review_remark else "New note added."

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
        msg.send()
    except Exception as e:
        print(f"Remark Email Error: {e}")

def append_remark(invoice_obj, user, new_text):
    """
    This ensures all communication is stored in the same model.
    """
    if not new_text or not new_text.strip():
        return None

    return ProformaRemark.objects.create(
        invoice=invoice_obj,  # This MUST be a ProformaInvoice instance
        user=user,
        remark=new_text
    )


class ProformaPriceChangeRequestRemarkView(AccountantRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        price_request = get_object_or_404(ProformaPriceChangeRequest, id=kwargs["pk"])
        invoice = price_request.invoice

        # 1. Get the text from the form (variable name: remark_text)
        remark_text = request.POST.get('review_remark', '').strip()

        if remark_text:
            # 2. Save the remark to the database
            append_remark(invoice, request.user, remark_text)

            # 3. Send Email Notification
            try:
                to_email = [price_request.requested_by.email]
                cc_emails = ["kashish.obluhc@gmail.com", "swasti.obluhc@gmail.com"]
                if request.user.email:
                    cc_emails.append(request.user.email)

                email_context = {
                    "request_obj": price_request,
                    "invoice": invoice,
                    "user": request.user,
                    "status": "remark_added",
                    "remark": remark_text,  # FIXED: was likely "remark" instead of "remark_text"
                }

                html_content = render_to_string(
                    "proforma_invoice/price_change_request_status_email.html",
                    email_context
                )
                subject = f"💬 New Remark on Price Request (Inv #{invoice.id})"

                msg = EmailMultiAlternatives(
                    subject, "", "proforma@oblutools.com",
                    to_email, cc=list(set(cc_emails))
                )
                msg.attach_alternative(html_content, "text/html")
                msg.send()
            except Exception as e:
                # This is where your error "name 'remark' is not defined" was appearing
                print(f"Remark Notification Email failed: {e}")

            messages.success(request, "Remark added and notification sent.")
        else:
            messages.warning(request, "Remark was empty and not saved.")

        return redirect("proforma_price_change_requests")




class CourierPricingView(TemplateView):
    template_name = "proforma_invoice/courier_editor.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. GET FILTERS FROM URL
        search_query = self.request.GET.get('q', '').strip()
        category_id = self.request.GET.get('category', '').strip()

        # 2. FETCH ALL INVENTORY PRODUCTS (NOT JUST PROFORMA ONES)
        product_qs = InventoryItem.objects.all()

        if search_query:
            product_qs = product_qs.filter(name__icontains=search_query)

        if category_id:
            product_qs = product_qs.filter(category_id=category_id)

        # Limit to first 200 items to keep the page fast,
        # or remove .all()[:200] if your DB is small.
        found_product_ids = product_qs.values_list('id', flat=True)

        # 3. SYNC LOGIC: Ensure rows exist for these found products
        with transaction.atomic():
            for p_id in found_product_ids:
                for mode_code, _ in CourierMode.choices:
                    charge_obj, _ = CourierCharge.objects.get_or_create(
                        product_id=p_id,
                        mode=mode_code
                    )
                    # If product has NO tiers at all, create a default "1+ Qty" tier
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

class BulkUpdateCourierView(View):
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
                # We look for a tier with same min_qty to update it, or create new
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
class SaveCourierSlabsView(View):
    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            data = payload.get('data', [])

            print(f"--- ATTEMPTING TO SAVE {len(data)} ROWS ---")

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
                        # We strip any ₹ or commas if Jspreadsheet sent them as strings
                        charge_val = str(row[5]).replace('₹', '').replace(',', '').strip()
                        tier.charge = Decimal(charge_val)

                        tier.save()

                    except CourierChargeTier.DoesNotExist:
                        print(f"Error: Row {index} has invalid ID: {row[0]}")
                        continue
                    except Exception as row_err:
                        print(f"Error saving row {index}: {str(row_err)}")
                        raise row_err  # Trigger rollback

            print("--- SAVE SUCCESSFUL ---")
            return JsonResponse({"status": "success", "message": "Changes saved to database."})

        except Exception as e:
            print(f"--- SAVE FAILED: {str(e)} ---")
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
    if total_seconds < 0: total_seconds = 0

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

class ProformaTimeTrackerDashboardView(AccountantRequiredMixin, ListView):
    model = ProformaInvoice
    template_name = "proforma_invoice/proforma_time_tracker.html"
    context_object_name = "all_invoices"

    def get_queryset(self):
        # Start with all invoices and connect related data
        queryset = ProformaInvoice.objects.select_related('customer').prefetch_related(
            'price_requests',
            'stock_request'
        )

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

        # This list must only contain raw timedelta objects for the math to work
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
            current_stock_request = getattr(invoice, 'stock_request', None)
            stock_time_raw = None
            stock_time_taken = None
            if current_stock_request and current_stock_request.reviewed_at:
                stock_time_raw = current_stock_request.reviewed_at - current_stock_request.created_at
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
                # FIRST: Calculate the raw timedelta object
                raw_lifetime = invoice.dispatched_at - invoice.date_created

                # SECOND: Append the RAW object to your list for math
                total_time_taken_by_a_pi.append(raw_lifetime)

                # THIRD: Format it as a string ONLY for the table display
                total_invoice_lifetime = format_duration(raw_lifetime)

            dashboard_rows.append({
                'invoice_data': invoice,
                'price_request_obj': last_price_request,
                'stock_request_obj': current_stock_request,
                'price_approval_time': price_time_taken,
                'stock_approval_time': stock_time_taken,
                'dispatch_action_time': dispatch_time_taken,
                'row_accounts_average': row_accounts_average,
                'total_invoice_lifetime': total_invoice_lifetime,
            })

        # --- FINAL CALCULATIONS ---

        final_time_taken_avg = "--"
        if total_time_taken_by_a_pi:
            # This sum() will now work because the list contains timedelta objects, not strings
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
    """
    memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()

    if memory:
        req_p = Decimal(str(requested_price))
        rec_p = Decimal(str(current_recommended))  # Ye bahar se aaya hua Tier price hai

        if memory.base_price_at_approval == rec_p:
            if req_p >= memory.min_approved_price:
                return True
    return False


from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from .models import ProformaInvoice, ProformaRemark


class ManageInvoiceRemarkView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        invoice = get_object_or_404(ProformaInvoice, pk=pk)
        return self._get_remarks_response(invoice)

    def post(self, request, pk, *args, **kwargs):  # Add 'pk' here
        # Use 'invoice_id' from the AJAX form data
        invoice_id = request.POST.get('invoice_id')
        text = request.POST.get('remark')

        if not text:
            return JsonResponse({'status': 'error', 'message': 'Empty message'}, status=400)

        invoice = get_object_or_404(ProformaInvoice, id=invoice_id)

        # Ensure model name is ProformaRemark
        ProformaRemark.objects.create(
            invoice=invoice,
            user=request.user,
            remark=text
        )

        return self._get_remarks_response(invoice)

    def _get_remarks_response(self, invoice):
        remarks = []
        # Use related_name='remarks' or filter manually
        query = invoice.remarks.all().order_by('created_at')
        for r in query:
            role = 'sales'
            if r.user.is_superuser:
                role = 'admin'
            elif getattr(r.user, 'is_accountant', False):
                role = 'accounts'

            # 2. CONVERT TO LOCAL TIME HERE
            local_datetime = timezone.localtime(r.created_at)


            remarks.append({
                'user': r.user.username,
                'text': r.remark,
                'time': local_datetime.strftime("%d %b, %H:%M"), # Use the local one
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

from django.db.models import Avg, F, ExpressionWrapper, fields, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta

class ProformaAnalyticsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "proforma_invoice/analytics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. Handle Date Range Filtering
        today = timezone.now().date()
        default_start = today - timedelta(days=30)

        start_str = self.request.GET.get('start_date')
        end_str = self.request.GET.get('end_date')

        start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else default_start
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today

        # 2. Base Querysets filtered by date range
        pi_qs = ProformaInvoice.objects.filter(date_created__date__range=[start_date, end_date])
        price_qs = ProformaPriceChangeRequest.objects.filter(created_at__date__range=[start_date, end_date])
        stock_qs = ProformaStockShortageRequest.objects.filter(created_at__date__range=[start_date, end_date])

        # 3. Calculate Global Averages
        total_created = pi_qs.count()
        total_shipped = pi_qs.filter(dispatch_status='dispatched').count()

        # Realistic Divisor (Number of days in range)
        delta = (end_date - start_date).days + 1
        avg_per_day = round(total_created / delta, 2) if total_created > 0 else 0

        # 4. SALESPERSON LEADERBOARD (Aggregated across models)
        # Get unique salesperson names from all invoices in this period
        salespeople = pi_qs.values_list('created_by', flat=True).distinct()
        # --- FIX 1: Move Expression OUTSIDE the loop ---
        cycle_expr = ExpressionWrapper(
            F('dispatched_at') - F('date_created'),
            output_field=fields.DurationField()
        )

        sales_leaderboard = []
        for name in salespeople:
            p_pi = pi_qs.filter(created_by=name) # Filter for this person

            # --- FIX 2: Calculate p_cycle for THIS specific person BEFORE appending ---
            p_cycle = p_pi.filter(
                dispatch_status='dispatched',
                dispatched_at__isnull=False,
                # customer__quotationmaker__is_converted_to_proforma=True
            ).annotate(cycle=cycle_expr).aggregate(Avg('cycle'))['cycle__avg']

            sales_leaderboard.append({
                'name': name,
                'invoices': p_pi.count(),
                'dispatches': p_pi.exclude(dispatch_requested_at__isnull=True).count(),
                'prices': price_qs.filter(requested_by__username=name).count(),
                'stocks': stock_qs.filter(requested_by__username=name).count(),
                'avg_cycle': self.format_td(p_cycle) # Now it works!
            })

        # --- FIX 3: Calculate Global Average OUTSIDE the loop ---
        global_cycle_avg = pi_qs.filter(
            dispatch_status='dispatched',
            dispatched_at__isnull=False
        ).annotate(cycle=cycle_expr).aggregate(Avg('cycle'))['cycle__avg']

        # Sort by total invoices
        sales_leaderboard = sorted(sales_leaderboard, key=lambda x: x['invoices'], reverse=True)

        # 5. Timing Calculations (Same as before but with custom range)
        duration_expr = ExpressionWrapper(F('reviewed_at') - F('created_at'), output_field=fields.DurationField())
        avg_price_time = \
        price_qs.filter(status__in=['approved', 'rejected']).annotate(duration=duration_expr).aggregate(
            Avg('duration'))['duration__avg']
        avg_stock_time = \
        stock_qs.filter(status__in=['approved', 'rejected']).annotate(duration=duration_expr).aggregate(
            Avg('duration'))['duration__avg']

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
        if not td: return "N/A"
        days, hours, minutes = td.days, td.seconds // 3600, (td.seconds // 60) % 60
        if days > 0: return f"{days}d {hours}h"
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"


class ApprovedPriceListView(LoginRequiredMixin, View):
    def get(self, request):
        is_accountant = getattr(request.user, 'is_accountant', False) or request.user.is_superuser
        selected_sp_id = request.GET.get('sp_id')

        # Get all Salespeople for the Accountant dropdown
        salespeople = SalesPerson.objects.all().order_by('name') if is_accountant else None

        if is_accountant:
            # If Accountant, filter by SP if selected, else show all
            queryset = ApprovedPriceMemory.objects.select_related('customer', 'product', 'customer__salesperson').all()
            if selected_sp_id:
                queryset = queryset.filter(customer__salesperson_id=selected_sp_id)
        else:
            # If normal user, find their SP profile and filter customers
            # Adjust 'salesperson_profile' based on your actual User-SP relationship
            user_sp = getattr(request.user, 'salesperson_profile', None)
            if user_sp:
                queryset = ApprovedPriceMemory.objects.filter(
                    customer__salesperson__in=user_sp.all()
                ).select_related('customer', 'product')
            else:
                queryset = ApprovedPriceMemory.objects.none()

        return render(request, "proforma_invoice/approved_price_list.html", {
            "memories": queryset,
            "salespeople": salespeople,
            "is_accountant": is_accountant,
            "selected_sp_id": selected_sp_id,
        })


@login_required
def delete_approved_price(request, pk):
    # Security: Only accountants or superusers can delete
    if not (getattr(request.user, 'is_accountant', False) or request.user.is_superuser):
        messages.error(request, "You do not have permission to delete these records.")
        return redirect('approved_price_list')

    memory = get_object_or_404(ApprovedPriceMemory, pk=pk)
    memory.delete()
    messages.success(request, "Approved price memory deleted successfully.")
    return redirect('approved_price_list')