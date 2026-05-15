from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import ProformaInvoice, ProformaInvoiceItem , ProformaPriceChangeRequest,ProformaStockShortageRequest,ProformaRemark
from .models import ApprovedPriceMemory, ProformaPriceChangeRequest # Ensure these are imported

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
from decimal import Decimal
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
2710, 2712, 2796, 2810, 2833, 2901, 2908, 2950, 3000,
3001, 3035, 3065, 3066, 3070, 3094, 3098, 3099, 3102,
3154, 3157, 3167, 3168, 3171, 3182, 3221, 3233, 3254,
3259, 3261, 3265, 3274, 3275, 3281, 3302, 3303, 3304, 3319,
3182,2997,

]


# ---5th ✅
from django.db import transaction
from django.shortcuts import render, redirect
from django.contrib import messages
from decimal import Decimal


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
                        cc_emails = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com"]
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
                            cc_emails = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com"]
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
        req = get_object_or_404(ProformaStockShortageRequest, pk=pk)
        action = request.POST.get("action")

        if action == "approve":
            req.status = "approved"

            # Check for any other pending issues (e.g. Price Change)
            from .models import ProformaPriceChangeRequest
            pending_prices = ProformaPriceChangeRequest.objects.filter(
                invoice=req.invoice,
                status="pending"
            ).exists()

            if not pending_prices:
                # Unlock Proforma only if all hurdles are cleared
                req.invoice.is_price_altered = False
                req.invoice.save()

            messages.success(request, f"✅ Stock request for Invoice #{req.invoice.id} approved.")
        else:
            req.status = "rejected"
            messages.error(request, f"❌ Stock request for Invoice #{req.invoice.id} rejected.")

        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.save()
        # abhijay changes start
        # ---------------- STOCK APPROVAL EMAIL ----------------
        try:
            requester_email = req.requested_by.email

            if requester_email:
                email_context = {
                    "request_obj": req,
                    "invoice": req.invoice,
                    "reviewed_by": request.user,
                    "review_url": f"https://oblutools.com/proforma/{req.invoice.id}/"
                }

                html_content = render_to_string(
                    "proforma_invoice/stock_request_approved_email.html",
                    email_context
                )

                subject = f"✅ Stock Available - Request Approved (Proforma #{req.invoice.id})"

                msg = EmailMultiAlternatives(
                    subject,
                    "",
                    "proforma@oblutools.com",
                    [requester_email],
                    cc=["abhijay.obluhc@gmail.com"]
                )

                msg.attach_alternative(html_content, "text/html")
                msg.send()

        except Exception as e:
            print(f"Stock Approval Mail Failed: {e}")

        # -----------------------------------------------------
        # abhijay changes ends
        return redirect("stock_request_dashboard")

# ----------------------------------------------------------------------------------------------------

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
        cc_emails = ["abhijay.obluhc@gmail.com","swasti.obluhc@gmail.com"]

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
Grand Total: ₹{invoice.grand_total}

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
        items = self.invoice.items.select_related("product__proforma_price")
        requested_product_prices = {}
        any_needs_accountant = False

        for item in items:
            raw_val = self.request.POST.get(f"new_price_{item.id}")
            if raw_val:
                requested_price = Decimal(raw_val)
                needs_req, needs_acc = check_price_needs_approval(self.request.user, item.product, requested_price)
                if needs_req:
                    # FIX: Save as a dictionary with metadata to prevent Approve View errors
                    pricing = getattr(item.product, 'proforma_price', None)
                    rec_p = pricing.price if pricing else 0
                    msrp_p = pricing.msrp if pricing else 0

                    requested_product_prices[str(item.id)] = {
                        "requested_price": str(raw_val),
                        "recommended_price": str(rec_p),
                        "msrp": str(msrp_p),
                        "quantity": item.quantity
                    }

                    if needs_acc:
                        any_needs_accountant = True

        # Capture courier charge (Fixes the red line for requested_courier_charge)
        requested_courier_charge = self.request.POST.get("new_courier_charge")

        price_request = form.save(commit=False)
        price_request.invoice = self.invoice
        price_request.customer = self.invoice.customer  # ✅ Add this line
        price_request.requested_by = self.request.user
        price_request.requested_product_prices = requested_product_prices
        price_request.needs_accountant_approval = any_needs_accountant

        if requested_courier_charge:
            price_request.requested_courier_charge = Decimal(requested_courier_charge)
        price_request.save()

        # ---------------- EMAIL ROUTING ----------------
        # If below MSRP: Send to Accountants
        # If above/equal MSRP: Send to Bhavya
        if any_needs_accountant:
            to_emails = ["swasti.obluhc@gmail.com"]  # Accountants
            subject_prefix = "🚨 DEEP DISCOUNT - Accountant Review Required"
        else:
            # Replace with Bhavya's actual email
            to_emails = ["bhavya.obluhc@gmail.com"]
            subject_prefix = "🔔 New Price Request for Bhavya"

        email_context = {
            "request_obj": price_request,
            "invoice": self.invoice,
            "requested_by": self.request.user,
            "requested_product_prices": requested_product_prices,
            "requested_courier_charge": requested_courier_charge, # Now defined!
            "needs_accountant": any_needs_accountant, # Fixed name!
            "review_url": "https://oblutools.com/proforma/price-change-requests/"
        }

        html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
        subject = f"{subject_prefix} (Proforma #{self.invoice.id})"

        msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        messages.success(self.request, "Your price change request has been submitted.")
        return redirect("proforma_detail", pk=self.invoice.id)

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

    # ... (keep your dispatch and get_context_data as they are) ...

    def form_valid(self, form):
        items = self.invoice.items.select_related("product__proforma_price")
        any_needs_accountant = False
        req_reason = form.cleaned_data.get('reason')

        # 1. LOOP THROUGH PRODUCTS
        for item in items:
            raw_val = self.request.POST.get(f"new_price_{item.id}")
            if raw_val:
                requested_price = Decimal(raw_val)
                needs_req, needs_acc = check_price_needs_approval(self.request.user, item.product, requested_price)

                if needs_req:
                    pricing = getattr(item.product, 'proforma_price', None)
                    standard_price = pricing.price if pricing else 0
                    msrp = pricing.msrp if pricing else 0

                    # FIX: Change 'needs_accountant_approval' to 'is_under_msrp'
                    ProformaPriceChangeRequest.objects.create(
                        invoice=self.invoice,
                        customer=self.invoice.customer,
                        requested_by=self.request.user,
                        product=item.product,
                        is_product_request=True,
                        requested_price=requested_price,
                        recommended_price=standard_price,
                        msrp_snapshot=msrp,
                        is_under_msrp=needs_acc,  # <--- CHANGED THIS
                        reason=req_reason,
                        status="pending"
                    )

                    if needs_acc:
                        any_needs_accountant = True

        # 2. COURIER LOGIC
        requested_courier_charge = self.request.POST.get("new_courier_charge")
        if requested_courier_charge:
            new_courier = Decimal(requested_courier_charge)
            if new_courier != self.invoice.courier_charge:
                ProformaPriceChangeRequest.objects.create(
                    invoice=self.invoice,
                    customer=self.invoice.customer,
                    requested_by=self.request.user,
                    is_product_request=False,
                    requested_courier_charge=new_courier,
                    reason=req_reason,
                    status="pending"
                )

        # 3. EMAIL LOGIC (Determining recipient based on the any_needs_accountant flag)
        if any_needs_accountant:
            to_emails = ["swasti.obluhc@gmail.com"]  # Accountant
            subject_prefix = "🚨 DEEP DISCOUNT"
        else:
            to_emails = ["bhavya.obluhc@gmail.com"]  # Standard Reviewer
            subject_prefix = "🔔 Price Request"

        try:
            email_context = {
                "invoice": self.invoice,
                "requested_by": self.request.user,
                "reason": req_reason,
                "review_url": "https://oblutools.com/proforma/price-change-requests/"
            }
            html_content = render_to_string("proforma_invoice/price_change_request_email.html", email_context)
            subject = f"{subject_prefix} (Proforma #{self.invoice.id})"
            msg = EmailMultiAlternatives(subject, "", "proforma@oblutools.com", to_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()
        except Exception as e:
            print(f"Email error: {e}")

        messages.success(self.request, "Your price change request has been submitted.")
        return redirect("proforma_detail", pk=self.invoice.id)


# View for accountants to list all Proforma price change requests
# ----------------------------------------------------------


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
        if self.request.user.is_superuser:
            return queryset # superuser sees all by default now

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
                to_email = ["swasti.obluhc@gmail.com","abhijay.obluhc@gmail.com"]
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
                    to_emails = ["abhijay.obluhc@gmail.com"]  # Add Nitin Sir's email here
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

        # Fetch data
        tiers = CourierChargeTier.objects.select_related(
            'courier_product__product'
        ).all()

        # DEBUG: Check your terminal!
        print(f"DEBUG: Found {tiers.count()} slabs in database")

        courier_data = []
        for t in tiers:
            # Safely get product name
            p_name = "Unknown Product"
            if t.courier_product and t.courier_product.product:
                p_name = t.courier_product.product.name

            # Safely get mode
            mode = "N/A"
            if t.courier_product:
                mode = t.courier_product.get_mode_display()

            courier_data.append([
                int(t.id),
                str(p_name),
                str(mode),
                int(t.min_quantity or 0),
                int(t.max_quantity) if t.max_quantity is not None else "",
                float(t.charge or 0)
            ])

        context['courier_json'] = json.dumps(courier_data)
        return context


@method_decorator(csrf_exempt, name='dispatch')





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

def check_is_permitted(customer, product, requested_price):
    """
    Checks if this price (or lower) was already approved for this customer.
    Logic: If current base price matches the snapshot, and requested price >= min approved.
    """
    from .models import ApprovedPriceMemory, ProductPrice

    # Get current master recommended price
    master_price_obj = getattr(product, "proforma_price", None)
    if not master_price_obj:
        return False

    current_recommended = master_price_obj.price

    memory = ApprovedPriceMemory.objects.filter(customer=customer, product=product).first()

    if memory:
        # Check if the recommended price has changed since the memory was created
        if memory.base_price_at_approval == current_recommended:
            # If salesperson is asking for a price higher or equal to what was approved before
            if Decimal(str(requested_price)) >= memory.min_approved_price:
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