from django.views.generic import TemplateView, ListView, UpdateView
from django.db.models import Count
from django.shortcuts import render
from .models import Customer, SalesPerson, CustomerVoucherStatus, CustomerFollowUp, CustomerCreditProfile
import json
from inventory.mixins import AccountantRequiredMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from datetime import date, timedelta
from tally_voucher.models import Voucher, VoucherRow, VoucherStockItem
from django.shortcuts import get_object_or_404, render
from django.shortcuts import redirect
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import CustomerRemark
from django.http import HttpResponseForbidden
from .models import CustomerRemark
import base64
from django.db.models import Count, OuterRef, Subquery
from django.views.generic import ListView
from django.db.models.functions import Lower, Trim
from django.db.models import Q
from .forms import CustomerReassignForm, CustomerCreditForm
from django.views import View
from django.utils import timezone
from django.urls import reverse_lazy
from django.urls import reverse
from urllib.parse import urlencode
from .models import (
    CustomerVoucherStatus,
    PaymentDiscussionThread,
PaymentTicketEvent,
    PaymentRemark,
PaymentExpectedDateHistory,
)
from .forms import PaymentRemarkForm, ExpectedDateForm
from calendar import monthrange
from decimal import Decimal
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from collections import OrderedDict
from django.db.models import Sum, Count, F
from django.db.models.functions import TruncMonth
from django.db.models import Avg, F, ExpressionWrapper, fields
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta, datetime


class AdminSalesPersonCustomersView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/admin_salesperson_customers.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        excluded_names = ["Abhijay"]

        ctx["salespersons"] = (
            SalesPerson.objects
            .all()
            .exclude(name__in=excluded_names)
            .order_by(Lower("name"))
        )

        selected_id = self.request.GET.get("salesperson")
        status_filter = self.request.GET.get("status", "all")
        outstanding_filter = self.request.GET.get("outstanding", "all")

        ctx["status_filter"] = status_filter
        ctx["outstanding_filter"] = outstanding_filter

        if not selected_id:
            ctx["customers"] = []
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=selected_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            ctx["customers"] = []
            return ctx

        # ---------------- BASE QUERY ----------------
        customers = Customer.objects.filter(
            salesperson=salesperson
        ).select_related("credit_profile")

        # ---------------- OUTSTANDING FILTER (DB LEVEL) ----------------
        if outstanding_filter == "due":
            customers = customers.filter(
                credit_profile__outstanding_balance__gt=0
            )

        elif outstanding_filter == "clear":
            customers = customers.filter(
                Q(credit_profile__outstanding_balance__lte=0) |
                Q(credit_profile__isnull=True)
            )

        cutoff_date = date.today() - timedelta(days=90)

        # ---------------- CUSTOMER LOOP ----------------
        customers = list(customers)

        for customer in customers:

            customer.remarks_list = customer.remarks.select_related(
                "salesperson", "salesperson__user"
            ).order_by("-created_at")

            customer.followups_list = customer.followups.select_related(
                "salesperson", "salesperson__user"
            ).order_by("-followup_date")

            credit_profile = getattr(customer, "credit_profile", None)
            customer.trial_balance = (
                credit_profile.outstanding_balance if credit_profile else None
            )

            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            ).order_by("-date")

            customer.vouchers_list = vouchers

            tax_invoice_vouchers = vouchers.filter(
                voucher_type="TAX INVOICE"
            )

            customer.last_order_date = (
                tax_invoice_vouchers.first().date
                if tax_invoice_vouchers.exists()
                else None
            )

            customer.total_orders = tax_invoice_vouchers.count()

            total_value = 0
            for v in tax_invoice_vouchers:
                total_row = v.rows.filter(
                    ledger__iexact=v.party_name
                ).first()
                if total_row:
                    total_value += total_row.amount

            customer.total_order_value = total_value

            customer.is_red_flag = (
                    customer.last_order_date is None
                    or customer.last_order_date < cutoff_date
            )

        # ---------------- STATUS FILTER (PYTHON LEVEL) ----------------
        if status_filter == "active":
            customers = [c for c in customers if not c.is_red_flag]

        elif status_filter == "inactive":
            customers = [c for c in customers if c.is_red_flag]

        # ---------------- STATS ----------------
        active = sum(1 for c in customers if not c.is_red_flag)
        inactive = sum(1 for c in customers if c.is_red_flag)

        outstanding_count = sum(
            1 for c in customers
            if c.trial_balance and c.trial_balance > 0
        )

        total_outstanding_amount = sum(
            c.trial_balance for c in customers
            if c.trial_balance and c.trial_balance > 0
        )

        # ---------------- FOLLOWUPS ----------------
        today = date.today()

        all_followups = CustomerFollowUp.objects.filter(
            salesperson=salesperson
        ).select_related("customer").order_by("followup_date")

        ctx["followups_previous"] = all_followups.filter(
            followup_date__lt=today
        )
        ctx["followups_today"] = all_followups.filter(
            followup_date=today
        )
        ctx["followups_future"] = all_followups.filter(
            followup_date__gt=today
        )

        # ---------------- CONTEXT ----------------
        ctx["customers"] = customers
        ctx["active_count"] = active
        ctx["inactive_count"] = inactive
        ctx["outstanding_count"] = outstanding_count
        ctx["total_outstanding_amount"] = total_outstanding_amount

        return ctx

    def post(self, request, *args, **kwargs):

        # Preserve existing filters automatically
        redirect_url = request.get_full_path()
        scroll_to = request.POST.get("scroll_to")  # reads a value sent from the form

        # DELETE REMARK
        delete_id = request.POST.get("delete_remark_id")
        if delete_id and request.user.is_accountant:

            remark = get_object_or_404(CustomerRemark, id=delete_id)
            remark.delete()
            messages.success(request, "Remark deleted successfully 🗑️")
            return redirect(f"{redirect_url}#{scroll_to}")

        # ADD REMARK
        remark_text = request.POST.get("remark", "").strip()
        customer_id = request.POST.get("customer_id")

        if not customer_id or not remark_text:
            return redirect(f"{redirect_url}#{scroll_to}")

        customer = get_object_or_404(Customer, id=customer_id)
        salesperson = request.user.salesperson_profile.first()

        CustomerRemark.objects.create(
            customer=customer,
            salesperson=salesperson,
            remark=remark_text
        )

        messages.success(request, "Remark added successfully ✅")
        return redirect(f"{redirect_url}#{scroll_to}")

class CustomerListViewLegacy(AccountantRequiredMixin, ListView):
    model = Customer
    template_name = "customers/data_Legacy.html"
    context_object_name = "customers"
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset()

        salesperson = self.request.GET.get("salesperson")
        state = self.request.GET.get("state")
        search = self.request.GET.get("search", "")

        if salesperson:
            qs = qs.filter(salesperson__name=salesperson)

        if state:
            qs = qs.filter(state=state)

        if search:
            qs = qs.filter(name__icontains=search)

        # --------------------------------------------------
        # 1️⃣ Latest voucher (ID + DATE) per customer
        # --------------------------------------------------
        latest_voucher = (
            Voucher.objects
            .annotate(party_clean=Lower(Trim("party_name")))
            .filter(
                party_clean=Lower(Trim(OuterRef("name"))),
                voucher_type="TAX INVOICE"
            )
            .order_by("-date")
        )

        qs = qs.annotate(
            last_purchase_date=Subquery(
                latest_voucher.values("date")[:1]
            ),
            last_voucher_id=Subquery(
                latest_voucher.values("id")[:1]
            )
        )

        # --------------------------------------------------
        # 2️⃣ Product name from that voucher
        # --------------------------------------------------
        last_product = (
            VoucherStockItem.objects
            .filter(
                voucher_id=OuterRef("last_voucher_id")
            )
            .values("item__name", "item_name_text")
        )

        qs = qs.annotate(
            last_product_name=Subquery(
                last_product.values("item__name")[:1]
            )
        )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["salespersons"] = SalesPerson.objects.values_list(
            "name", flat=True
        ).distinct()

        ctx["states"] = Customer.objects.values_list(
            "state", flat=True
        ).distinct()

        ctx["total_customers"] = Customer.objects.count()
        ctx["total_salespersons"] = SalesPerson.objects.count()
        ctx["unassigned_count"] = Customer.objects.filter(
            salesperson__isnull=True
        ).count()

        ctx["top_state"] = (
            Customer.objects.values("state")
            .annotate(c=Count("id"))
            .order_by("-c")
            .first()
        )

        return ctx

class CustomerListView(AccountantRequiredMixin, ListView):
    model = Customer
    template_name = "customers/data.html"
    context_object_name = "customers"
    paginate_by = 50

    def get_queryset(self):
        qs = Customer.objects.select_related("salesperson")

        salesperson = self.request.GET.get("salesperson")
        state = self.request.GET.get("state")
        search = self.request.GET.get("search", "")
        district = self.request.GET.get("district")

        if salesperson:
            qs = qs.filter(salesperson__name=salesperson)

        if state:
            qs = qs.filter(state=state)

        if search:
            qs = qs.filter(name__icontains=search)

        if district:
            qs = qs.filter(district__icontains=district)

        # ✅ Latest voucher per customer (TAX INVOICE)
        latest_voucher = (
            Voucher.objects
            .annotate(party_clean=Lower(Trim("party_name")))
            .filter(
                party_clean=Lower(Trim(OuterRef("name"))),
                voucher_type="TAX INVOICE"
            )
            .order_by("-date")
        )

        qs = qs.annotate(
            last_purchase_date=Subquery(latest_voucher.values("date")[:1]),
            last_voucher_id=Subquery(latest_voucher.values("id")[:1]),
        )

        # ✅ Product name from latest voucher
        last_product = (
            VoucherStockItem.objects
            .filter(voucher_id=OuterRef("last_voucher_id"))
            .values("item__name")
        )

        qs = qs.annotate(
            last_product_name=Subquery(last_product[:1])
        )

        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["salespersons"] = SalesPerson.objects.values_list("name", flat=True).distinct()
        ctx["states"] = Customer.objects.values_list("state", flat=True).distinct()

        #  keep filters on pagination
        querystring = self.request.GET.copy()
        if "page" in querystring:
            querystring.pop("page")
        ctx["querystring"] = querystring.urlencode()
        ctx["querystring"] = querystring.urlencode()

        return ctx

#new view- for credit period
class EditCreditPeriodView(AccountantRequiredMixin,UpdateView):
    model = CustomerCreditProfile
    form_class = CustomerCreditForm
    template_name = "customers/edit_credit_period.html"

    def get_object(self):
        customer = get_object_or_404(Customer, pk=self.kwargs["pk"])
        profile, created = CustomerCreditProfile.objects.get_or_create(
            customer=customer
        )
        return profile

    def get_success_url(self):
        return reverse_lazy("customers:data")


class ChartsView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/charts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        cutoff_date = date.today() - timedelta(days=90)

        # =====================================================
        # 1️⃣ SALESPERSON TOTAL CUSTOMER COUNT (OLD – KEEP)
        # =====================================================
        salesperson_counts = (
            Customer.objects.values("salesperson__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        salesperson_data = [
            {
                "Salesperson": s["salesperson__name"] or "Unassigned",
                "count": s["count"],
            }
            for s in salesperson_counts
        ]

        # =====================================================
        # 2️⃣ STATE TOTAL CUSTOMER COUNT (OLD – KEEP)
        # =====================================================
        state_counts = (
            Customer.objects.values("state")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        state_data = [
            {
                "State": s["state"] or "Unknown",
                "count": s["count"],
            }
            for s in state_counts
        ]

        # =====================================================
        # 3️⃣ ACTIVE / INACTIVE CUSTOMERS (BASE LOGIC)
        # =====================================================
        active_customers = {}
        inactive_customers = {}

        customers = Customer.objects.select_related("salesperson")

        for cust in customers:
            salesperson_id = cust.salesperson_id or 0

            if salesperson_id not in active_customers:
                active_customers[salesperson_id] = 0
                inactive_customers[salesperson_id] = 0

            last_voucher = (
                Voucher.objects.filter(
                    party_name__iexact=cust.name
                )
                .order_by("-date")
                .first()
            )

            if not last_voucher:
                inactive_customers[salesperson_id] += 1
            else:
                if last_voucher.date >= cutoff_date:
                    active_customers[salesperson_id] += 1
                else:
                    inactive_customers[salesperson_id] += 1

        # =====================================================
        # 4️⃣ SALESPERSON CHART (TOTAL + ACTIVE)
        # =====================================================
        salesperson_chart = []

        salespersons = (
            Customer.objects.select_related("salesperson")
            .values("salesperson__id", "salesperson__name")
            .distinct()
        )

        for sp in salespersons:
            sp_id = sp["salesperson__id"] or 0
            sp_name = sp["salesperson__name"] or "Unassigned"

            total = Customer.objects.filter(
                salesperson_id=sp_id
            ).count()

            active = active_customers.get(sp_id, 0)

            salesperson_chart.append({
                "name": sp_name,
                "total": total,
                "active": active,
            })

        # =====================================================
        # 5️⃣ TOP 10 STATES (TOTAL + ACTIVE)
        # =====================================================
        state_totals = (
            Customer.objects.values("state")
            .annotate(total=Count("id"))
            .order_by("-total")[:10]
        )

        state_chart = []

        for row in state_totals:
            state = row["state"] or "Unknown"
            total = row["total"]

            active = 0
            for cust in Customer.objects.filter(state=row["state"]):
                last_voucher = (
                    Voucher.objects.filter(
                        party_name__iexact=cust.name
                    )
                    .order_by("-date")
                    .first()
                )

                if last_voucher and last_voucher.date >= cutoff_date:
                    active += 1

            state_chart.append({
                "state": state,
                "total": total,
                "active": active,
            })

        # =====================================================
        # 6️⃣ SUMMARY COUNTS
        # =====================================================
        ctx["active_customers_total"] = sum(active_customers.values())
        ctx["inactive_customers_total"] = sum(inactive_customers.values())

        # =====================================================
        # 7️⃣ CONTEXT
        # =====================================================
        ctx.update({
            # OLD (keep existing charts alive)
            "salesperson_counts_json": json.dumps(salesperson_data),
            "state_counts_json": json.dumps(state_data),

            # NEW (enhanced charts)
            "salesperson_chart_json": json.dumps(salesperson_chart),
            "state_chart_json": json.dumps(state_chart),

            # Active / Inactive
            "active_customers_json": json.dumps(active_customers),
            "inactive_customers_json": json.dumps(inactive_customers),

            # Summary cards
            "total_customers": Customer.objects.count(),
            "unique_salespersons": Customer.objects.values("salesperson").distinct().count(),
            "total_states": Customer.objects.values("state").distinct().count(),
        })

        return ctx



class UnassignedView(AccountantRequiredMixin,TemplateView):
    template_name = "customers/unassigned.html"


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unassigned"] = Customer.objects.filter(salesperson__isnull=True)
        return ctx



class SalesPersonCustomerOrdersView(LoginRequiredMixin, ListView):
    template_name = "customers/salesperson_customer_orders.html"
    context_object_name = "customers"
    model = Customer

    # --------------------------------------------------
    # GET: Customer list + orders + remarks
    # --------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        cutoff_date = date.today() - timedelta(days=90)

        salesperson = user.salesperson_profile.first()
        if not salesperson:
            return Customer.objects.none()

        selected_sp = self.request.GET.get("team_member")

        # ============================
        # MANAGER (manager is None)
        # ============================
        if salesperson.manager is None:
            team_members = salesperson.team_members.all()

            if selected_sp == "me":
                qs = Customer.objects.filter(salesperson=salesperson)

            elif selected_sp == "all" or not selected_sp:
                qs = Customer.objects.filter(
                    salesperson__in=[salesperson, *team_members]
                )

            else:
                qs = Customer.objects.filter(salesperson__id=selected_sp)

        # ============================
        # NORMAL SALESPERSON
        # ============================
        else:
            qs = Customer.objects.filter(salesperson=salesperson)

        # --------------------------------------------------
        # Attach order stats + remarks
        # --------------------------------------------------
        for customer in qs:
            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            ).order_by("-date")

            customer.vouchers_list = vouchers
            customer.last_order_date = vouchers.first().date if vouchers.exists() else None

            tax_vouchers = vouchers.filter(voucher_type__iexact="TAX INVOICE")
            customer.total_orders = tax_vouchers.count()

            total_value = 0
            for v in tax_vouchers:
                total_row = v.rows.filter(
                    ledger__iexact=v.party_name
                ).first()
                if total_row:
                    total_value += total_row.amount

            customer.total_order_value = total_value

            if customer.last_order_date is None:
                customer.is_red_flag = True
            else:
                customer.is_red_flag = customer.last_order_date < cutoff_date

            # ✅ ALL remarks (no restriction)
            customer.remarks_list = customer.remarks.select_related(
                "salesperson", "salesperson__user"
            )
            customer.followups_list = customer.followups.filter(
                salesperson=customer.salesperson
            ).order_by("followup_date")

        return qs


    def post(self, request):

        salesperson = request.user.salesperson_profile.first()
        if not salesperson:
            return redirect(request.path)

        #  (DELETE REMARK HANDLING)
        if request.POST.get("delete_remark_id"):
            remark_id = request.POST.get("delete_remark_id")
            remark = get_object_or_404(CustomerRemark, id=remark_id)

            salesperson = request.user.salesperson_profile.first()
            if not salesperson:
                return redirect(request.path)

            #  only owner can delete
            if remark.salesperson != salesperson:
                return HttpResponseForbidden("Not allowed")

            remark.delete()
            return redirect(request.get_full_path())

        # ADD FOLLOW-UP
        # =========================
        if request.POST.get("followup_customer_id"):
            customer = get_object_or_404(
                Customer,
                id=request.POST.get("followup_customer_id")
            )

            #  ONLY owner salesperson (manager ya normal)
            if customer.salesperson != salesperson:
                return HttpResponseForbidden(
                    "You can add follow-up only for your own customers"
                )

            CustomerFollowUp.objects.create(
                customer=customer,
                salesperson=salesperson,
                followup_date=request.POST.get("followup_date"),
                note=request.POST.get("followup_note", "")
            )

            return redirect(request.get_full_path())

        # =========================
        # MARK FOLLOW-UP COMPLETED
        # =========================
        if request.POST.get("complete_followup_id"):
            followup = get_object_or_404(
                CustomerFollowUp,
                id=request.POST.get("complete_followup_id"),
                salesperson=salesperson
            )
            followup.is_completed = True
            followup.completed_at = timezone.now()
            followup.save()
            return redirect(request.get_full_path())

        if request.POST.get("delete_followup_id"):
            followup = get_object_or_404(
                CustomerFollowUp,
                id=request.POST.get("delete_followup_id")
            )

            # #  Manager cannot delete
            # if salesperson.manager is None:
            #     return HttpResponseForbidden("Manager cannot delete follow-ups")

            # ✅ Only owner
            if followup.salesperson != salesperson:
                return HttpResponseForbidden("Not allowed")

            followup.delete()
            return redirect(request.get_full_path())

        # BELOW IS YOUR EXISTING SAVE-REMARK CODE (UNCHANGED)
        customer_id = request.POST.get("customer_id")
        remark_text = request.POST.get("remark", "").strip()

        if not remark_text:
            return redirect(request.path)

        salesperson = request.user.salesperson_profile.first()
        if not salesperson:
            return redirect(request.path)

        customer = get_object_or_404(Customer, id=customer_id)

        CustomerRemark.objects.create(
            customer=customer,
            salesperson=salesperson,
            remark=remark_text
        )

        return redirect(request.get_full_path())

    # --------------------------------------------------
    # Context data
    # --------------------------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        salesperson = self.request.user.salesperson_profile.first()

        ctx["is_manager"] = salesperson and salesperson.manager is None
        ctx["selected_member"] = self.request.GET.get("team_member", "all")

        if ctx["is_manager"]:
            ctx["team_members"] = salesperson.team_members.all()

        # --------------------------------------------------
        # STATS BASED ON FILTERED CUSTOMER LIST
        # --------------------------------------------------

        customers = ctx.get("object_list", [])
        cutoff_date = date.today() - timedelta(days=90)

        active_count = 0
        inactive_count = 0
        outstanding_count = 0
        total_outstanding_amount = 0

        for c in customers:
            # Active / Inactive
            if c.last_order_date and c.last_order_date >= cutoff_date:
                active_count += 1
            else:
                inactive_count += 1


            credit_profile = getattr(c, "credit_profile", None)
            if credit_profile:
                if credit_profile.outstanding_balance != 0:
                    outstanding_count += 1
                total_outstanding_amount += credit_profile.outstanding_balance

        ctx.update({
            "active_count": active_count,
            "inactive_count": inactive_count,
            "outstanding_count": outstanding_count,
            "total_outstanding_amount": total_outstanding_amount,
        })

        selected_member = ctx["selected_member"]
        today = timezone.now().date()

        target_salesperson = None

        if salesperson:
            if salesperson.manager is None:
                # MANAGER
                if selected_member == "me":
                    target_salesperson = salesperson
                elif selected_member != "all":
                    target_salesperson = SalesPerson.objects.filter(
                        id=selected_member
                    ).first()
            else:
                # NORMAL SALESPERSON
                target_salesperson = salesperson

        if target_salesperson:
            followups = CustomerFollowUp.objects.filter(
                salesperson=target_salesperson
            ).select_related("customer").order_by("followup_date")

            ctx["sp_followups_previous"] = followups.filter(followup_date__lt=today)
            ctx["sp_followups_today"] = followups.filter(followup_date=today)
            ctx["sp_followups_upcoming"] = followups.filter(followup_date__gt=today)

            ctx["selected_salesperson"] = target_salesperson
            ctx["selected_salesperson_name"] = (
                "My" if target_salesperson == salesperson else target_salesperson.name
            )
        else:
            ctx["sp_followups_previous"] = []
            ctx["sp_followups_today"] = []
            ctx["sp_followups_upcoming"] = []
        return ctx

class CustomerPaymentStatusView(LoginRequiredMixin, TemplateView):
    template_name = "customers/customer_payment_status.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        customer = get_object_or_404(Customer, pk=kwargs["pk"])
        ctx["customer"] = customer

        credit_profile = getattr(customer, "credit_profile", None)
        ctx["credit_profile"] = credit_profile

        qs = CustomerVoucherStatus.objects.filter(
            customer=customer
        ).order_by("-voucher_date")

        # -----------------------------
        # Filters
        # -----------------------------
        voucher_type = self.request.GET.get("voucher_type")
        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")

        payment_status = self.request.GET.get("payment_status")
        credit_crossed = self.request.GET.get("credit_crossed")

        if voucher_type:
            qs = qs.filter(voucher_type=voucher_type)

        if start_date:
            qs = qs.filter(voucher_date__gte=start_date)

        if end_date:
            qs = qs.filter(voucher_date__lte=end_date)

        # -----------------------------
        # Tax-invoice specific filters
        # -----------------------------
        if payment_status:
            if payment_status == "paid":
                qs = qs.filter(is_fully_paid=True)
            elif payment_status == "partial":
                qs = qs.filter(is_partially_paid=True)
            elif payment_status == "unpaid":
                qs = qs.filter(is_unpaid=True)

        if credit_crossed == "yes":
            qs = qs.filter(is_credit_period_crossed=True)
        elif credit_crossed == "no":
            qs = qs.filter(is_credit_period_crossed=False)

        ctx["vouchers"] = qs

        # Dropdown values
        ctx["voucher_types"] = (
            CustomerVoucherStatus.objects
            .filter(customer=customer)
            .values_list("voucher_type", flat=True)
            .distinct()
        )

        ctx["filters"] = self.request.GET
        return ctx

class PaymentThreadDetailView(LoginRequiredMixin,TemplateView):
    template_name = "customers/payment_thread_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.voucher_status = get_object_or_404(
            CustomerVoucherStatus,
            pk=kwargs["voucher_status_id"]
        )

        # Auto-create thread
        self.thread, created = PaymentDiscussionThread.objects.get_or_create(
            voucher_status=self.voucher_status
        )

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):

        action = request.POST.get("action")

        # ----------------------
        # Add Remark
        # ----------------------
        if action == "add_remark":
            form = PaymentRemarkForm(request.POST)
            if form.is_valid():
                remark = form.save(commit=False)
                remark.thread = self.thread
                remark.created_by = request.user
                remark.save()
                messages.success(request, "Remark added.")

        # ----------------------
        # Add Expected Date
        # ----------------------
        elif action == "add_expected_date":
            form = ExpectedDateForm(request.POST)
            if form.is_valid():
                expected = form.save(commit=False)
                expected.thread = self.thread
                expected.set_by = request.user
                expected.save()
                messages.success(request, "Expected date added.")

        # ----------------------
        # Raise Ticket
        # ----------------------
        elif action == "raise_ticket":
            self.thread.ticket_status = "RAISED"
            self.thread.raised_by = request.user
            self.thread.raised_at = timezone.now()
            self.thread.save()

            PaymentTicketEvent.objects.create(
                thread=self.thread,
                event_type="RAISED",
                performed_by=request.user
            )
            # ---------------------------
            # SEND EMAIL
            # ---------------------------

            salesperson = self.voucher_status.customer.salesperson
            salesperson_email = None

            if salesperson and salesperson.user:
                salesperson_email = salesperson.user.email

            page_link = "https://oblutools.com" + reverse(
                "customers:payment_thread_detail",
                args=[self.voucher_status.id]
            )

            subject = "🚨 Payment Ticket Raised"

            body = f"""
            The user {request.user.username} has raised a payment ticket.

            Customer: {self.voucher_status.customer.name}
            Invoice: {self.voucher_status.voucher.voucher_number}

            Please review the issue.

            Open Ticket:
            {page_link}
            """

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email="crm@oblutools.com",
                to=[salesperson_email] if salesperson_email else [],
                cc=[
                    "abhijay.obluhc@gmail.com",
                    request.user.email
                ]
            )

            msg.send()

            messages.success(request, "Ticket raised.")

        # ----------------------
        # Solve Ticket
        # ----------------------
        elif action == "solve_ticket":
            self.thread.ticket_status = "SOLVED"
            self.thread.solved_by = request.user
            self.thread.solved_at = timezone.now()
            self.thread.save()

            PaymentTicketEvent.objects.create(
                thread=self.thread,
                event_type="SOLVED",
                performed_by=request.user
            )

            messages.success(request, "Ticket solved.")

        return redirect(
            "customers:payment_thread_detail",
            voucher_status_id=self.voucher_status.id
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["voucher_status"] = self.voucher_status
        ctx["thread"] = self.thread

        ctx["remarks"] = self.thread.remarks.all()
        ctx["expected_dates"] = self.thread.expected_date_history.all()

        ctx["remark_form"] = PaymentRemarkForm()
        ctx["expected_date_form"] = ExpectedDateForm()
        ctx["ticket_events"] = self.thread.ticket_events.all()

        return ctx

class CustomerPaymentThreadsView(LoginRequiredMixin, TemplateView):
    template_name = "customers/customer_payment_threads.html"

    def dispatch(self, request, *args, **kwargs):
        self.customer = get_object_or_404(Customer, pk=kwargs["customer_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        voucher_statuses = (
            CustomerVoucherStatus.objects
            .filter(customer=self.customer)
            .select_related("voucher")
            .order_by("-voucher_date")
        )

        invoice_threads = []

        for vs in voucher_statuses:

            thread, created = PaymentDiscussionThread.objects.get_or_create(
                voucher_status=vs
            )

            events = []

            # remarks
            for r in thread.remarks.all():
                events.append({
                    "type": "Remark",
                    "text": r.remark,
                    "user": r.created_by,
                    "time": r.created_at,
                })

            # expected dates
            for e in thread.expected_date_history.all():
                events.append({
                    "type": "Expected Date",
                    "text": f"Expected payment on {e.expected_date}",
                    "user": e.set_by,
                    "time": e.created_at,
                })

            # ticket events
            for t in thread.ticket_events.all():
                events.append({
                    "type": f"Ticket {t.event_type}",
                    "text": f"Ticket {t.event_type.lower()}",
                    "user": t.performed_by,
                    "time": t.performed_at,
                })

            # sort timeline
            events = sorted(events, key=lambda x: x["time"], reverse=True)

            invoice_threads.append({
                "voucher_status": vs,
                "thread": thread,
                "events": events,
            })

        ctx["customer"] = self.customer
        ctx["invoice_threads"] = invoice_threads

        return ctx


class PaymentFollowUpDashboardView(LoginRequiredMixin, TemplateView):

    template_name = "customers/payment_followup_dashboard.html"

    def get_queryset(self):

        user = self.request.user

        qs = CustomerVoucherStatus.objects.filter(
            Q(is_unpaid=True) | Q(is_partially_paid=True)
        ).select_related(
            "customer",
            "voucher",
            "customer__salesperson"
        ).prefetch_related(
            "payment_thread__remarks",
            "payment_thread__expected_date_history",
            "payment_thread__ticket_events"
        )

        salesperson_id = self.request.GET.get("salesperson")

        if user.is_superuser:

            if salesperson_id:
                qs = qs.filter(customer__salesperson_id=salesperson_id)

        else:

            qs = qs.filter(customer__salesperson__user=user)

        return qs

    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        qs = self.get_queryset()

        # ensure threads exist
        for vs in qs:
            PaymentDiscussionThread.objects.get_or_create(voucher_status=vs)

        ctx["voucher_statuses"] = qs
        ctx["salespersons"] = SalesPerson.objects.all()

        ctx["selected_salesperson"] = self.request.GET.get("salesperson")

        return ctx


@require_POST
def payment_followup_action(request):

    action = request.POST.get("action")
    vs_id = request.POST.get("voucher_status_id")

    vs = CustomerVoucherStatus.objects.get(id=vs_id)

    thread, _ = PaymentDiscussionThread.objects.get_or_create(
        voucher_status=vs
    )

    if action == "remark":

        remark = request.POST.get("remark")

        obj = PaymentRemark.objects.create(
            thread=thread,
            remark=remark,
            created_by=request.user
        )

        return JsonResponse({
            "status": "ok",
            "text": remark,
            "user": request.user.username,
            "time": obj.created_at.strftime("%d %b %H:%M")
        })


    elif action == "expected":

        date = request.POST.get("expected_date")

        obj = PaymentExpectedDateHistory.objects.create(
            thread=thread,
            expected_date=date,
            set_by=request.user
        )

        return JsonResponse({
            "status": "ok",
            "date": date,
            "user": request.user.username
        })


    elif action == "raise":

        thread.ticket_status = "RAISED"
        thread.raised_by = request.user
        thread.raised_at = timezone.now()
        thread.save()

        PaymentTicketEvent.objects.create(
            thread=thread,
            event_type="RAISED",
            performed_by=request.user
        )

        return JsonResponse({"status": "ok"})


    elif action == "solve":

        thread.ticket_status = "SOLVED"
        thread.solved_by = request.user
        thread.solved_at = timezone.now()
        thread.save()

        PaymentTicketEvent.objects.create(
            thread=thread,
            event_type="SOLVED",
            performed_by=request.user
        )

        return JsonResponse({"status": "ok"})

class CustomerEditView(AccountantRequiredMixin, View):
    template_name = "customers/edit_customer.html"

    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        form = CustomerReassignForm(instance=customer)

        return render(request, self.template_name, {
            "customer": customer,
            "form": form,
        })

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        form = CustomerReassignForm(request.POST, instance=customer)

        if form.is_valid():
            form.save()
            messages.success(request, "Customer updated successfully.")
            # Preserve filters
            # remove these preserve filters
            query_string = request.GET.urlencode()
            url = reverse("customers:data")

            if query_string:
                url = f"{url}?{query_string}"

            return redirect(url)

        messages.error(request, "Please fix the errors below.")
        return render(request, self.template_name, {
            "customer": customer,
            "form": form,
        })


def get_logged_in_salesperson(user):
    return SalesPerson.objects.filter(user=user).first()

class ClaimOwnVoucherView(LoginRequiredMixin, View):
    template_name = "customers/claim_own_vouchers.html"

    def get(self, request):
        sp = get_logged_in_salesperson(request.user)

        if not sp:
            return render(request, self.template_name, {"vouchers": []})

        vouchers = (
            CustomerVoucherStatus.objects
            .select_related("customer", "voucher")
            .filter(
                customer__salesperson=sp,
                voucher_type__iexact="TAX INVOICE",
                sold_by__isnull=True,                 # not yet claimed
            )
            .order_by("-voucher_date")
        )

        return render(request, self.template_name, {
            "vouchers": vouchers,
            "salesperson": sp,
        })

    def post(self, request):
        sp = get_logged_in_salesperson(request.user)
        voucher_id = request.POST.get("voucher_id")

        if not sp or not voucher_id:
            return redirect("customers:claim_own_voucher")

        cvs = CustomerVoucherStatus.objects.select_related(
            "customer", "customer__salesperson"
        ).filter(
            voucher_id=voucher_id,
            voucher_type__iexact="TAX INVOICE"
        ).first()

        if not cvs:
            return redirect("customers:claim_own_voucher")

        # must be his own customer
        if cvs.customer.salesperson_id != sp.id:
            return redirect("customers:claim_own_voucher")

        cvs.sold_by = sp
        cvs.claim_status = "APPROVED"
        cvs.claim_requested_by = None
        cvs.save()

        return redirect("customers:claim_own_voucher")

class RequestVoucherClaimView(LoginRequiredMixin, View):
    template_name = "customers/request_voucher_claim.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        sp = get_logged_in_salesperson(request.user)
        voucher_no = request.POST.get("voucher_number")

        if not sp or not voucher_no:
            messages.error(request, "Invalid request.")
            return redirect("customers:request_voucher_claim")

        voucher = Voucher.objects.filter(
            voucher_number__iexact=voucher_no.strip(),
            voucher_type__iexact="TAX INVOICE"
        ).first()

        if not voucher:
            messages.error(request, "Voucher not found or not a TAX INVOICE.")
            return redirect("customers:request_voucher_claim")

        cvs = CustomerVoucherStatus.objects.select_related(
            "customer", "customer__salesperson"
        ).filter(voucher=voucher).first()

        if not cvs:
            messages.error(request, "Voucher exists but not linked to any customer.")
            return redirect("customers:request_voucher_claim")

        # if already sold_by someone
        if cvs.sold_by:
            messages.error(request, "This voucher is already claimed.")
            return redirect("customers:request_voucher_claim")

        # if own customer → should claim directly
        if cvs.customer.salesperson_id == sp.id:
            messages.info(request, "This is your customer. Please claim from your voucher list.")
            return redirect("customers:claim_own_voucher")

        # already requested?
        if cvs.claim_status == "PENDING":
            messages.warning(request, "Request already pending for this voucher.")
            return redirect("customers:request_voucher_claim")

        cvs.claim_requested_by = sp
        cvs.claim_status = "PENDING"
        cvs.save()

        messages.success(
            request,
            f"Request sent to {cvs.customer.salesperson.name} for approval."
        )
        return redirect("customers:request_voucher_claim")

class ApproveVoucherClaimsView(LoginRequiredMixin, View):
    template_name = "customers/approve_voucher_claims.html"

    def get(self, request):
        sp = get_logged_in_salesperson(request.user)

        if not sp:
            return render(request, self.template_name, {"requests": []})

        requests = (
            CustomerVoucherStatus.objects
            .select_related("customer", "voucher", "claim_requested_by")
            .filter(
                customer__salesperson=sp,
                claim_status="PENDING"
            )
            .order_by("-voucher_date")
        )

        return render(request, self.template_name, {
            "requests": requests,
            "salesperson": sp,
        })

    def post(self, request):
        sp = get_logged_in_salesperson(request.user)
        cvs_id = request.POST.get("cvs_id")
        action = request.POST.get("action")  # approve / reject

        if not sp or not cvs_id:
            return redirect("customers:approve_voucher_claims")

        cvs = CustomerVoucherStatus.objects.select_related(
            "customer"
        ).filter(id=cvs_id).first()

        # must be his customer
        if not cvs or cvs.customer.salesperson_id != sp.id:
            return redirect("customers:approve_voucher_claims")

        if action == "approve":
            cvs.sold_by = cvs.claim_requested_by
            cvs.claim_status = "APPROVED"
            messages.success(request, "Voucher claim approved.")

        elif action == "reject":
            cvs.claim_status = "REJECTED"
            messages.info(request, "Voucher claim rejected.")

        cvs.claim_requested_by = None
        cvs.save()

        return redirect("customers:approve_voucher_claims")

class CustomerVouchersOverviewView(LoginRequiredMixin, View):
    template_name = "customers/customer_vouchers_overview.html"

    def get(self, request):
        sp = get_logged_in_salesperson(request.user)

        if not sp:
            return render(request, self.template_name, {
                "customer_vouchers": [],
                "requested_vouchers": [],
            })

        # -----------------------------
        # 1. Vouchers of my customers
        # -----------------------------
        customer_vouchers = (
            CustomerVoucherStatus.objects
            .select_related("customer", "voucher", "sold_by", "claim_requested_by")
            .filter(customer__salesperson=sp)
            .order_by("-voucher_date")
        )

        # -----------------------------
        # 2. Vouchers I have requested
        # -----------------------------
        requested_vouchers = (
            CustomerVoucherStatus.objects
            .select_related("customer", "voucher", "sold_by")
            .filter(
                ~Q(customer__salesperson=sp),  # not my customer
                Q(claim_requested_by=sp) | Q(sold_by=sp)
            )
            .order_by("-voucher_date")
        )

        return render(request, self.template_name, {
            "customer_vouchers": customer_vouchers,
            "requested_vouchers": requested_vouchers,
            "salesperson": sp,
        })

class AdminVoucherClaimManagementView(AccountantRequiredMixin, View):
    template_name = "customers/admin_voucher_claim_management.html"

    def get(self, request):
        month = request.GET.get("month")  # format: YYYY-MM

        vouchers = []
        if month:
            year, mon = month.split("-")

            vouchers = (
                CustomerVoucherStatus.objects
                .select_related("customer", "voucher", "sold_by", "customer__salesperson")
                .filter(
                    voucher_type__iexact="TAX INVOICE",
                    voucher_date__year=int(year),
                    voucher_date__month=int(mon),
                )
                .order_by("-voucher_date")
            )

        salespersons = SalesPerson.objects.all().order_by("name")

        return render(request, self.template_name, {
            "vouchers": vouchers,
            "salespersons": salespersons,
            "selected_month": month,
        })

    def post(self, request):
        cvs_id = request.POST.get("cvs_id")
        sold_by_id = request.POST.get("sold_by")

        if not cvs_id:
            return redirect("customers:admin_voucher_claim_management")

        cvs = CustomerVoucherStatus.objects.filter(id=cvs_id).first()

        if not cvs:
            messages.error(request, "Voucher not found.")
            return redirect(request.META.get("HTTP_REFERER"))

        if sold_by_id == "":
            cvs.sold_by = None
            cvs.claim_status = "NONE"
        else:
            sp = SalesPerson.objects.filter(id=sold_by_id).first()
            if sp:
                cvs.sold_by = sp
                cvs.claim_status = "APPROVED"

        cvs.claim_requested_by = None  # admin override clears disputes
        cvs.save()

        messages.success(request, "Voucher claim updated.")
        return redirect(request.META.get("HTTP_REFERER"))

class SalesReportView(AccountantRequiredMixin,TemplateView):
    template_name = "customers/sales_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ---------------------------------
        # BASIC FILTERS
        # ---------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")
        month_picker = self.request.GET.get("month_picker")

        today = date.today()
        if month_picker:
            year, month = map(int, month_picker.split("-"))
        else:
            year, month = today.year, today.month

        ctx["year"] = year
        ctx["month"] = month

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "selected_salesperson": None,
            })
            return ctx

        year = ctx["year"]
        month = ctx["month"]

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # ---------------------------------
        # ALL TAX INVOICES (PAID + UNPAID)
        # ---------------------------------
        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[start_date, end_date],
        )

        vouchers = Voucher.objects.filter(
            id__in=voucher_statuses.values_list("voucher_id", flat=True)
        )

        voucher_status_map = {cvs.voucher_id: cvs for cvs in voucher_statuses}

        # ---------------------------------
        # STOCK ITEMS
        # ---------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        rows = []
        processed_vouchers = set()
        total_sales = Decimal("0.00")

        # ---------------------------------
        # BUILD ROWS + MAPS
        # ---------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            if si.voucher_id not in processed_vouchers:

                processed_vouchers.add(si.voucher_id)

                party_row = si.voucher.rows.filter(
                    ledger__iexact=si.voucher.party_name
                ).first()

                if party_row:
                    total_sales += Decimal(str(party_row.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)
            is_partially_paid = bool(voucher_status and voucher_status.is_partially_paid)
            is_unpaid = bool(voucher_status and voucher_status.is_unpaid)


            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": is_partially_paid,
                "is_unpaid": is_unpaid,
            })

        # ---------------------------------
        # CONTEXT
        # ---------------------------------
        ctx["rows"] = rows
        ctx["total_sales"] = total_sales

        return ctx

class MonthlySalesReportView(LoginRequiredMixin, TemplateView):
    template_name = "customers/monthly_sales_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ---------------------------------
        # BASIC FILTERS
        # ---------------------------------

        month_picker = self.request.GET.get("month_picker")

        today = date.today()
        if month_picker:
            year, month = map(int, month_picker.split("-"))
        else:
            year, month = today.year, today.month

        ctx["year"] = year
        ctx["month"] = month

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])

        salesperson = SalesPerson.objects.filter(user=self.request.user).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            ctx["rows"] = []
            return ctx


        # ---------------------------------
        # ALL TAX INVOICES (PAID + UNPAID)
        # ---------------------------------
        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[start_date, end_date],
        )

        vouchers = Voucher.objects.filter(
            id__in=voucher_statuses.values_list("voucher_id", flat=True)
        )

        voucher_status_map = {cvs.voucher_id: cvs for cvs in voucher_statuses}

        # ---------------------------------
        # STOCK ITEMS
        # ---------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        rows = []

        total_sales = Decimal("0.00")
        processed_vouchers = set()

        # ---------------------------------
        # BUILD ROWS + MAPS
        # ---------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            if si.voucher_id not in processed_vouchers:

                processed_vouchers.add(si.voucher_id)

                party_row = si.voucher.rows.filter(
                    ledger__iexact=si.voucher.party_name
                ).first()

                if party_row:
                    total_sales += Decimal(str(party_row.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)
            is_partially_paid = bool(voucher_status and voucher_status.is_partially_paid)
            is_unpaid = bool(voucher_status and voucher_status.is_unpaid)


            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": is_partially_paid,
                "is_unpaid": is_unpaid,
            })

        # ---------------------------------
        # CONTEXT
        # ---------------------------------
        ctx["rows"] = rows
        ctx["total_sales"] = total_sales

        return ctx

class AllMonthsSalesReportView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/all_months_sales_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        # 1. GET FILTERS
        salesperson_id = self.request.GET.get("salesperson")
        start_date_str = self.request.GET.get("start_date")
        end_date_str = self.request.GET.get("end_date")

        if not salesperson_id:
            ctx.update({"grouped_months": None, "grand_total": Decimal("0.00")})
            return ctx

        salesperson = get_object_or_404(SalesPerson, id=salesperson_id)
        ctx["selected_salesperson"] = salesperson

        # 2. FETCH DATA WITH DATE RANGE
        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
        ).select_related("customer").order_by("-voucher_date", "-voucher_id")

        # Apply Date Range Filters
        if start_date_str:
            voucher_statuses = voucher_statuses.filter(voucher_date__gte=start_date_str)
        if end_date_str:
            voucher_statuses = voucher_statuses.filter(voucher_date__lte=end_date_str)

        voucher_status_map = {cvs.voucher_id: cvs for cvs in voucher_statuses}
        voucher_ids = list(voucher_status_map.keys())

        vouchers = Voucher.objects.filter(id__in=voucher_ids).prefetch_related("rows")
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("-voucher__date", "-voucher_id")
        )

        # 3. GROUPING & TOTALS LOGIC
        grouped_months = OrderedDict()
        processed_vouchers = set()
        grand_total = Decimal("0.00")
        global_sr_no = 1

        for si in stock_items:
            month_key = si.voucher.date.strftime("%B %Y")

            if month_key not in grouped_months:
                grouped_months[month_key] = {"rows": [], "monthly_total": Decimal("0.00")}

            current_invoice_total = Decimal("0.00")
            for row in si.voucher.rows.all():
                if row.ledger.strip().lower() == si.voucher.party_name.strip().lower():
                    current_invoice_total = Decimal(str(row.amount))
                    break

            display_total = None
            if si.voucher_id not in processed_vouchers:
                processed_vouchers.add(si.voucher_id)
                display_total = current_invoice_total
                grouped_months[month_key]["monthly_total"] += current_invoice_total
                grand_total += current_invoice_total

            voucher_status = voucher_status_map.get(si.voucher_id)
            grouped_months[month_key]["rows"].append({
                "sn": global_sr_no,
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_no": si.voucher.voucher_number,
                "voucher_id": si.voucher.id,
                "product": si.item.name if si.item else "Unknown",
                "quantity": si.quantity,
                "item_amount": si.amount,
                "invoice_total": display_total,
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
            })
            global_sr_no += 1

        # 4. CALCULATE MONTHLY AVERAGE
        num_months = len(grouped_months)
        avg_monthly_sales = grand_total / num_months if num_months > 0 else 0

        # 5. CONTEXT
        ctx.update({
            "grouped_months": grouped_months,
            "grand_total": grand_total,
            "total_invoices_count": len(processed_vouchers),
            "total_items_count": len(stock_items),
            "num_months": num_months,
            "avg_monthly_sales": avg_monthly_sales,
            "start_date": start_date_str,
            "end_date": end_date_str,
        })
        return ctx




# KASHISH KPI VIEWS

#sales report by each product
class SalesByProductsView(LoginRequiredMixin, TemplateView):
    template_name = "customers/product_sales_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        sp_id = self.request.GET.get("salesperson")
        if not sp_id:
            ctx.update({
                "product_data": None,
                "selected_salesperson": None
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=sp_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # STEP 1: Get the IDs of all vouchers sold by this salesperson
        sold_voucher_ids = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE"
        ).values_list('voucher_id', flat=True)

        # STEP 2: Get the stock items belonging to those specific vouchers
        stock_items = VoucherStockItem.objects.filter(
            voucher_id__in=sold_voucher_ids
        ).select_related('item', 'voucher').order_by('-voucher__date')

        # 3. Build the Hierarchy: Product -> Customer -> List of Orders
        product_data = OrderedDict()
        total_qty_all = 0

        for si in stock_items:
            p_name = si.item.name if si.item else "Unknown Product"
            c_name = si.voucher.party_name
            qty = si.quantity or 0
            total_qty_all += qty

            # Initialize Product level
            if p_name not in product_data:
                product_data[p_name] = {
                    "total_qty": 0,
                    "customers": OrderedDict()
                }

            product_data[p_name]["total_qty"] += qty

            # Initialize Customer level inside Product
            if c_name not in product_data[p_name]["customers"]:
                product_data[p_name]["customers"][c_name] = {
                    "total_qty": 0,
                    "orders": []
                }

            product_data[p_name]["customers"][c_name]["total_qty"] += qty

            # Add specific order details
            product_data[p_name]["customers"][c_name]["orders"].append({
                "date": si.voucher.date,
                "voucher_no": si.voucher.voucher_number,
                "voucher_id": si.voucher.id,
                "qty": qty
            })

        ctx["product_data"] = product_data
        ctx["total_units"] = total_qty_all
        ctx["unique_products_count"] = len(product_data)

        return ctx

# page created by swasti
class SalesPersonCustomerOwnershipView(LoginRequiredMixin,TemplateView):
    template_name = "customers/salesperson_customer_summary.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # -------------------------
        # Salesperson dropdown
        # -------------------------

        ctx["salespersons"] = (
            SalesPerson.objects
            .all()
            .order_by("name")
        )

        salesperson_id = self.request.GET.get("salesperson")

        # -------------------------
        # Date range (default 3 months)
        # -------------------------

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")

        if start_date:
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        else:
            start_date = default_start

        if end_date:
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        else:
            end_date = today
        if not start_date:
            start_date = default_start

        if not end_date:
            end_date = today

        # IMPORTANT — send strings to template
        ctx["start_date"] = start_date.strftime("%Y-%m-%d")
        ctx["end_date"] = end_date.strftime("%Y-%m-%d")

        if not salesperson_id:
            ctx["new_customers"] = []
            ctx["new_customer_count"] = 0
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = get_object_or_404(
            SalesPerson,
            pk=salesperson_id
        )

        ctx["selected_salesperson"] = salesperson

        # -------------------------
        # First voucher per customer
        # -------------------------
        # #  consider TAX INVOICE
        first_voucher_subquery = (
            CustomerVoucherStatus.objects
            .filter(
        # Run this query for each customer

                customer=OuterRef("pk"),
                voucher_type="TAX INVOICE"

            )
            .order_by("voucher_date", "id")
        )

        #  Attach first sale info to every customer

        customers_with_first_sale = (
            Customer.objects
            .annotate(
        # Get the date of the first TAX INVOICE

                first_sale_date=Subquery(
                    first_voucher_subquery
                    .values("voucher_date")[:1]
                ),
        # Get who sold that first TAX INVOICE

                first_seller_id=Subquery(
                    first_voucher_subquery
                    .values("sold_by")[:1]
                )
            )
        )

        # -------------------------
        # NEW CUSTOMERS
        # -------------------------

        new_customers = (
            customers_with_first_sale
            .filter(
                first_seller_id=salesperson.id,
                first_sale_date__gte=start_date,
                first_sale_date__lte=end_date
            )
            .order_by("-first_sale_date")
        )

        ctx["new_customers"] = new_customers
        ctx["new_customer_count"] = new_customers.count()

        return ctx

#page created by swasti
class AdminSalespersonConversionReportView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/salesperson_performance_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s_date = self.request.GET.get("start_date")
        e_date = self.request.GET.get("end_date")
        start_date = date.fromisoformat(s_date) if s_date else date(2026, 1, 1)
        end_date = date.fromisoformat(e_date) if e_date else date(2026, 3, 31)
        weeks_passed = ((end_date - start_date).days or 1) / 7.0

        excluded = ["Abhijay", "Aryan", "Jackson", "Kaushik", "Mukesh", "Nimit", "Online", "online Order", "Raman",
                    "test1", "Vibhuti"]
        all_sp = SalesPerson.objects.all().exclude(name__in=excluded).order_by(Lower("name"))
        ctx["all_salespersons"] = all_sp

        sp_id = self.request.GET.get("salesperson")
        salespersons = all_sp.filter(id=sp_id) if sp_id else all_sp
        ctx["selected_sp_id"] = int(sp_id) if sp_id else None

        salesperson_data = []
        for sp in salespersons:
            customers = Customer.objects.filter(salesperson=sp)
            conv_custs = []
            total_inact_days = 0
            for cust in customers:
                v_hist = Voucher.objects.filter(party_name__iexact=cust.name, voucher_type="TAX INVOICE").order_by(
                    "-date")

                # behavioral sync logic
                recent = v_hist.filter(date__range=(start_date, end_date)).order_by('date')
                if recent.exists():
                    first_inv = recent.first()
                    prev = v_hist.filter(date__lt=first_inv.date).order_by('-date').first()
                    if prev and (first_inv.date - prev.date).days > 90:
                        cust.remarks_count = cust.remarks.filter(created_at__date__range=(start_date, end_date)).count()
                        cust.followup_count = cust.followups.filter(followup_date__range=(start_date, end_date)).count()
                        conv_custs.append(cust)
                if v_hist.first(): total_inact_days += max((end_date - v_hist.first().date).days, 0)

            # KPI CALCULATIONS (Strictly 10% total)
            total_c = customers.count()
            avg_inact = round(total_inact_days / total_c, 2) if total_c > 0 else 0

            # E1: Converted (5%)
            e1 = round(min((len(conv_custs) / weeks_passed) * 5, 5), 2) if weeks_passed > 0 else 0
            # E2: Inactiveness (5%)
            e2 = 5.0 if avg_inact <= 45 else (2.5 if avg_inact <= 90 else 0.0)

            salesperson_data.append({
                "salesperson": sp, "total_customers": total_c, "converted_count": len(conv_custs),
                "converted_customers": conv_custs,
                "avg_inactiveness": avg_inact, "total_conversion_score": round(e1 + e2, 2),
                "e1": e1, "e2": e2,
            })

        ctx.update({"salesperson_data": salesperson_data, "start_date": start_date, "end_date": end_date})
        return ctx

# HELPER FUNCTION: To calculate Average time between remarks
def calculate_avg_time(current_sp):
    today = date.today()
    start_date = date(today.year, 1, 1)

    # Get all remarks in range
    remarks = CustomerRemark.objects.filter(
        salesperson=current_sp,
        created_at__date__gte=start_date,
        created_at__date__lte=today
    ).order_by('customer_id', 'created_at')

    # Group remarks by customer
    customer_map = defaultdict(list)

    for r in remarks:
        customer_map[r.customer_id].append(r.created_at.date())

    customer_gaps = []

    for customer_id, dates in customer_map.items():

        # Case 1: Only 1 remark
        if len(dates) == 1:
            gap = (today - dates[0]).days
            customer_gaps.append(gap)
            continue

        # Case 2: Multiple remarks → calculate gaps
        gaps = []
        for i in range(1, len(dates)):
            diff = (dates[i] - dates[i-1]).days
            gaps.append(diff)

        avg_gap = sum(gaps) / len(gaps)
        customer_gaps.append(avg_gap)

    # Final average across customers
    if customer_gaps:
        final_avg = round(sum(customer_gaps) / len(customer_gaps), 1)
        return f"{final_avg} Days"

    return "N/A"

# PAGE 1: Summary Dashboard
class SalespersonPaymentSummaryView(LoginRequiredMixin, TemplateView):
    template_name = "customers/payment_collection_summary.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. Dropdown Filter
        all_sp = SalesPerson.objects.all().order_by("name")
        hide = ["abhijay", "raman", "vibhuti", "akshay", "nitin", "online", "online order", "mukesh", "aryan", "test1"]
        ctx["salespersons_list"] = [sp for sp in all_sp if sp.name.lower() not in hide]

        sp_id = self.request.GET.get("salesperson")
        if not sp_id: return ctx

        current_sp = get_object_or_404(SalesPerson, id=sp_id)
        ctx["selected_sp"] = current_sp

        # 2. Timeframe Setup (Jan 1st Start)
        today = date.today()
        start_yr = date(today.year if today.month > 3 else today.year - 1, 1, 1)
        days_passed = (today - start_yr).days or 1
        weeks_passed = days_passed / 7.0

        assigned_customers = Customer.objects.filter(salesperson=current_sp)
        total_cust = assigned_customers.count()

        # --- KPI CALCULATIONS (TOTAL 15%) ---
        # A. Remark Volume (3%)
        actual_remarks_yr = CustomerRemark.objects.filter(salesperson=current_sp,
                                                          created_at__date__range=(start_yr, today)).count()
        target_rem_total = total_cust * weeks_passed
        score_rem_vol = round(min((actual_remarks_yr / target_rem_total) * 3, 3), 2) if target_rem_total > 0 else 0

        # B. Follow-up Volume (3%)
        actual_fups_yr = CustomerFollowUp.objects.filter(salesperson=current_sp,
                                                         created_at__date__range=(start_yr, today)).count()
        target_fup_total = total_cust * weeks_passed
        score_fup_vol = round(min((actual_fups_yr / target_fup_total) * 3, 3), 2) if target_fup_total > 0 else 0

        # C. Follow-up On-Time (5%)
        tasks_due = CustomerFollowUp.objects.filter(salesperson=current_sp, followup_date__lte=today)
        on_time_count = total_delay_sum = 0
        if tasks_due.exists():
            on_time_count = tasks_due.filter(is_completed=True, completed_at__date__lte=F('followup_date')).count()
            score_on_time = round((on_time_count / tasks_due.count()) * 5, 2)
            for f in tasks_due:
                if f.is_completed and f.completed_at:
                    total_delay_sum += max((f.completed_at.date() - f.followup_date).days, 0)
                else:
                    total_delay_sum += (today - f.followup_date).days
            avg_fup_delay = total_delay_sum / tasks_due.count()
        else:
            score_on_time = avg_fup_delay = 0

        # D. Ticket Resolution Speed (4%)
        active_tickets = PaymentDiscussionThread.objects.filter(voucher_status__sold_by=current_sp).exclude(
            ticket_status="NONE")
        avg_t_days = 0
        if active_tickets.exists():
            now = timezone.now()
            total_t_days = sum([((t.solved_at.date() - t.raised_at.date()).days if t.solved_at else (
                        now.date() - t.raised_at.date()).days) for t in active_tickets])
            avg_t_days = total_t_days / active_tickets.count()
            score_speed = round(max(0, 4 - (avg_t_days - 2)), 2)
        else:
            score_speed = 0

        # --- NON-WEIGHTED TRACKING DATA ---
        customer_avg_list = []
        for cust in assigned_customers:
            c = CustomerRemark.objects.filter(customer=cust, salesperson=current_sp,
                                              created_at__date__range=(start_yr, today)).count()
            customer_avg_list.append(days_passed / (c if c > 0 else 1))

        # FIXED: Variable explicitly defined for the template
        date_hist = PaymentExpectedDateHistory.objects.filter(thread__voucher_status__sold_by=current_sp)
        total_expected_dates_count = date_hist.count()
        kept = sum(1 for log in date_hist if
                   log.thread.voucher_status.is_fully_paid and log.thread.voucher_status.updated_at.date() <= log.expected_date)

        # 3. CONTEXT UPDATE
        ctx.update({
            "total_block_d_score": round(score_rem_vol + score_fup_vol + score_on_time + score_speed, 2),
            "score_rem_vol": score_rem_vol, "score_fup_vol": score_fup_vol,
            "score_on_time": score_on_time, "score_speed": score_speed,
            "total_remarks_count": actual_remarks_yr,
            "remarks_per_customer": round(actual_remarks_yr / (total_cust or 1), 2),
            "avg_fup_per_cust": round(actual_fups_yr / (total_cust or 1), 2),
            "avg_fups_per_week": round((actual_fups_yr / (total_cust or 1)) / weeks_passed,
                                       2) if weeks_passed > 0 else 0,
            "avg_remark_gap": f"{round(sum(customer_avg_list) / total_cust, 1) if total_cust > 0 else 0} Days",
            "on_time_followups_count": on_time_count,
            "total_followups_count": tasks_due.count(),
            "avg_followup_delay": f"{round(avg_fup_delay, 1)} Days",
            "payments_received_on_time": kept,
            "total_expected_dates_count": total_expected_dates_count,  # Matches template name
            "weeks_passed": round(weeks_passed, 1),
            "tickets_total": active_tickets.count(),
            "tickets_raised": active_tickets.filter(ticket_status="RAISED").count(),
            "tickets_solved": active_tickets.filter(ticket_status="SOLVED").count(),
            "avg_ticket_resolve_time": f"{round(avg_t_days, 1)} Days",
            "pending_payment_count": CustomerVoucherStatus.objects.filter(sold_by=current_sp, is_fully_paid=False,
                                                                          voucher_type__iexact="TAX INVOICE").count(),
            "lifetime_invoice_count": CustomerVoucherStatus.objects.filter(sold_by=current_sp,
                                                                           voucher_type__iexact="TAX INVOICE").count(),
            "thread_remarks_count": PaymentRemark.objects.filter(
                created_by=current_sp.user).count() if current_sp.user else 0,
        })
        return ctx

# PAGE 2: The Detailed List Page
class SalespersonPerformanceCollectionView(LoginRequiredMixin, TemplateView):
    template_name = "customers/performance_collection.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sp_id = self.request.GET.get("salesperson")
        current_sp = get_object_or_404(SalesPerson, id=sp_id)
        ctx["selected_sp"] = current_sp
        today = date.today()

        # 1. Health Stats (Jan 1st Logic - Shared with Summary)
        assigned_customers = Customer.objects.filter(salesperson=current_sp)
        start_of_year = date(today.year, 1, 1)
        period = (today - start_of_year).days or 1
        d_sum, g_sum = 0, 0
        for cust in assigned_customers:
            c = CustomerRemark.objects.filter(customer=cust, salesperson=current_sp,
                                              created_at__date__range=(start_of_year, today)).count()
            d_sum += period
            g_sum += (c + 1)
        ctx["avg_remark_gap"] = f"{round(d_sum / g_sum, 1)} Days" if g_sum > 0 else "1 Day"
        ctx["remarks_per_customer"] = round(
            CustomerRemark.objects.filter(salesperson=current_sp).count() / assigned_customers.count(),
            2) if assigned_customers.exists() else 0

        # 2. Follow-up Audit logic
        followups = CustomerFollowUp.objects.filter(salesperson=current_sp).select_related('customer').order_by(
            '-followup_date')
        total_delay, done_count = 0, 0
        for f in followups:
            f.date_set_on = f.created_at.date()
            if f.is_completed and f.completed_at:
                done_count += 1
                if f.completed_at.date() <= f.followup_date:
                    f.res_label, f.res_class, f.delay = "On-Time", "b-green", 0
                else:
                    delay = (f.completed_at.date() - f.followup_date).days
                    f.res_label, f.res_class, f.delay = "Late", "b-red", delay
                    total_delay += delay
            else:
                f.res_label, f.res_class, f.delay = ("Overdue", "b-red",
                                                     (today - f.followup_date).days) if f.followup_date < today else (
                    "Pending", "b-dim", 0)

        ctx["all_followups_list"] = followups
        ctx["avg_followup_delay"] = f"{round(total_delay / done_count, 1)} Days" if done_count > 0 else "0 Days"

        # 3. Tickets logic (Resolution Speed + Individual Speed)
        tickets = PaymentDiscussionThread.objects.filter(voucher_status__sold_by=current_sp).exclude(
            ticket_status="NONE").select_related('voucher_status__customer', 'voucher_status__voucher').order_by(
            '-raised_at')
        now, total_t_sec = timezone.now(), 0
        for t in tickets:
            if t.raised_at:
                diff = (t.solved_at - t.raised_at) if t.solved_at else (now - t.raised_at)
                total_t_sec += diff.total_seconds()
                t.time_label, t.time_result = ("Solved In",
                                               f"{diff.days}d {diff.seconds // 3600}h") if t.solved_at else (
                    "Open Since", f"{diff.days}d {diff.seconds // 3600}h")

        ctx["all_tickets_list"] = tickets
        if tickets.exists():
            avg_t = total_t_sec / tickets.count()
            ctx["avg_ticket_resolve_time"] = f"{int(avg_t // 86400)}d {int((avg_t % 86400) // 3600)}h"
        else:
            ctx["avg_ticket_resolve_time"] = "0 Days"

        # 4. Standard Logs
        ctx["pending_invoices"] = CustomerVoucherStatus.objects.filter(sold_by=current_sp, is_fully_paid=False,
                                                                       voucher_type__iexact="TAX INVOICE").select_related(
            'customer', 'voucher').annotate(
            date_entry_count=Count('payment_thread__expected_date_history', distinct=True)).order_by('-voucher_date')

        date_log = PaymentExpectedDateHistory.objects.filter(thread__voucher_status__sold_by=current_sp).select_related(
            'thread__voucher_status__customer', 'thread__voucher_status__voucher').order_by('-created_at')
        for log in date_log:
            v = log.thread.voucher_status
            if v.is_fully_paid:
                log.reality_label, log.reality_class = ("On-Time",
                                                        "b-green") if v.updated_at.date() <= log.expected_date else (
                    "Delayed", "b-yellow")
            else:
                log.reality_label, log.reality_class = ("Missed", "b-red") if log.expected_date < today else ("Waiting",
                                                                                                              "b-dim")
        ctx["expected_date_log"] = date_log

        ctx["all_remarks_list"] = CustomerRemark.objects.filter(salesperson=current_sp).select_related(
            'customer').order_by('-created_at')
        if current_sp.user:
            ctx["all_thread_remarks"] = PaymentRemark.objects.filter(created_by=current_sp.user).select_related(
                'thread__voucher_status__customer').order_by('-created_at')

        return ctx

class RemarkInteractionGapView(LoginRequiredMixin, TemplateView):
    template_name = "customers/remark_interaction_gap.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. Setup Salesperson and Date Range
        sp_id = self.request.GET.get("salesperson")
        selected_sp = get_object_or_404(SalesPerson, id=sp_id)

        today = date.today()
        start_of_year = date(today.year, 1, 1)

        total_days_in_year_so_far = (today - start_of_year).days
        if total_days_in_year_so_far <= 0:
            total_days_in_year_so_far = 1

        # 2. Get all customers
        assigned_customers = Customer.objects.filter(
            salesperson=selected_sp
        ).order_by('name')

        audit_data = []
        customer_avg_list = []   # ✅ NEW (IMPORTANT)

        for cust in assigned_customers:
            # Get remarks for this customer
            remarks = CustomerRemark.objects.filter(
                customer=cust,
                salesperson=selected_sp,
                created_at__date__range=(start_of_year, today)
            ).order_by('created_at')

            remark_count = remarks.count()

            gaps = remarks.count()

            if remark_count == 0:
                gaps = remark_count+ 1
            else:
                gaps = remarks.count()

            # per customer avg
            cust_avg = round(total_days_in_year_so_far / gaps, 1)

            # store for final avg
            customer_avg_list.append(cust_avg)   # ✅ FIXED

            audit_data.append({
                'customer_name': cust.name,
                'remark_count': remark_count,
                'remark_dates': [r.created_at.date() for r in remarks],
                'total_days': total_days_in_year_so_far,
                'gaps': gaps,
                'result': cust_avg
            })

        # 3. FINAL CORRECT AVERAGE
        if customer_avg_list:
            final_avg = round(sum(customer_avg_list) / len(customer_avg_list), 1)
        else:
            final_avg = 0

        ctx.update({
            "selected_sp": selected_sp,
            "audit_data": audit_data,
            "start_date": start_of_year,
            "end_date": today,
            "total_period_days": total_days_in_year_so_far,
            "final_avg_result": final_avg,
            "total_customers": assigned_customers.count()
        })

        return ctx


class SalesPerformanceReviewView(LoginRequiredMixin, TemplateView):
    template_name = "customers/performance_review.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. POPULATE DROPDOWN
        all_sp = SalesPerson.objects.all().order_by("name")
        hide_names = ["abhijay", "raman", "vibhuti", "akshay", "nitin", "onine", "online order", "mukesh", "aryan",
                      "test1", "online","jackson","kaushik","nimit"]
        ctx["salespersons"] = [sp for sp in all_sp if sp.name.lower() not in hide_names]

        # 2. CHECK SELECTION
        sp_id = self.request.GET.get("salesperson")
        if not sp_id:
            return ctx

        selected_sp = get_object_or_404(SalesPerson, id=sp_id)
        ctx["selected_salesperson"] = selected_sp
        qualitative_map = {
            "aman": 29.0,
            "ankush": 29.0,
            "bhavya": 29.5,
            "naveen": 29.5,
            "satish": 29.5,
            "rushikesh": 28.6
        }
        # Lookup score, default to 0 if name not in dictionary
        qualitative_score = qualitative_map.get(selected_sp.name.lower(), 0.0)

        # ------------------------------------------------------
        # 3. INITIALIZE ALL VARIABLES (Prevents NameError)
        # ------------------------------------------------------
        today = date.today()
        sync_start = date(2026, 1, 1)
        sync_end = date(2026, 4, 1)
        sync_weeks = (sync_end - sync_start).days / 7.0
        fy_start = date(2025, 4, 1)

        # Data Objects
        performance_rows = []
        cat_data = []
        new_cust_qs = Customer.objects.none()

        # Counters
        actual_category_count = 0
        actual_new_clients = 0
        conv_count = 0
        total_inactive_days = 0
        on_time_pay = 0
        total_interactions = 0
        g_actual = g_bench = Decimal("0.00")

        # Scores
        score_a = score_b = score_c = score_d = score_e = score_geo = 0
        d1 = d2 = d3 = d4 = e1_score = e2_score = 0

        # Geo
        top_st_name = "N/A"
        top_st_orders = 0
        top_st_dist_actual = 0
        top_st_dist_total = 1
        top_st_penetration_pct = 0

        # ------------------------------------------------------
        # 4. FETCH DATA
        # ------------------------------------------------------
        voucher_statuses = CustomerVoucherStatus.objects.filter(sold_by=selected_sp, voucher_type__iexact="TAX INVOICE")
        sold_vids = voucher_statuses.filter(voucher_date__range=(fy_start, today)).values_list('voucher_id', flat=True)
        sp_customers = Customer.objects.filter(salesperson=selected_sp)
        total_assigned = sp_customers.count()

        # --- BLOCK A: CATEGORY (10%) ---
        stock_items = VoucherStockItem.objects.filter(voucher_id__in=sold_vids)
        cat_data = stock_items.exclude(item__category__name__icontains="Service").values(
            cat_name=F('item__category__name')).annotate(total_qty=Sum('quantity')).order_by('-total_qty')
        actual_category_count = cat_data.count()
        score_a = round(min((actual_category_count / 15) * 10, 10), 2)

        # --- BLOCK B: REVENUE (20%) ---
        target_val = Decimal("500000.00") if selected_sp.manager is None else Decimal("200000.00")
        monthly_data = VoucherRow.objects.filter(voucher_id__in=sold_vids,
                                                 ledger__iexact=F('voucher__party_name')).annotate(
            month=TruncMonth('voucher__date')).values('month').annotate(actual=Sum('amount')).order_by('-month')
        for entry in monthly_data:
            act = Decimal(str(entry['actual'] or 0));
            g_actual += act;
            g_bench += target_val
            performance_rows.append(
                {'month': entry['month'], 'target': target_val, 'actual': act, 'diff': act - target_val,
                 'pct': round((act / target_val * 100), 2) if target_val > 0 else 0, 'status': act >= target_val})
        score_b = round(min(((g_actual / g_bench) if g_bench > 0 else 0) * 20, 20), 2)

        # --- BLOCK C: NEW CLIENTS (10%) ---
        new_cust_qs = Customer.objects.annotate(f_date=Subquery(
            CustomerVoucherStatus.objects.filter(customer=OuterRef("pk"), voucher_type="TAX INVOICE").order_by(
                "voucher_date").values("voucher_date")[:1]), f_seller=Subquery(
            CustomerVoucherStatus.objects.filter(customer=OuterRef("pk"), voucher_type="TAX INVOICE").order_by(
                "voucher_date").values("sold_by")[:1])).filter(f_seller=selected_sp.id,
                                                               f_date__gte=sync_start).order_by("-f_date")
        actual_new_clients = new_cust_qs.count()
        score_c = round(min((actual_new_clients / 12) * 10, 10), 2)

        # --- BLOCK D: COLLECTION (15% TOTAL) ---
        if total_assigned > 0:
            target_vol = total_assigned * sync_weeks
            rem_gen = CustomerRemark.objects.filter(salesperson=selected_sp,
                                                    created_at__date__range=(sync_start, sync_end)).count()
            rem_thread = PaymentRemark.objects.filter(created_by=selected_sp.user, created_at__date__range=(sync_start,
                                                                                                            sync_end)).count() if selected_sp.user else 0
            total_interactions = rem_gen + rem_thread
            d1 = round(min((total_interactions / target_vol) * 3, 3), 2)
            fup_cnt = CustomerFollowUp.objects.filter(salesperson=selected_sp,
                                                      created_at__date__range=(sync_start, sync_end)).count()
            d2 = round(min((fup_cnt / target_vol) * 3, 3), 2)
            tasks = CustomerFollowUp.objects.filter(salesperson=selected_sp,
                                                    followup_date__range=(sync_start, sync_end))
            if tasks.exists():
                on_time = tasks.filter(is_completed=True, completed_at__date__lte=F('followup_date')).count()
                d3 = round((on_time / tasks.count()) * 5, 2)
            active_t = PaymentDiscussionThread.objects.filter(
                voucher_status__customer__salesperson=selected_sp).exclude(ticket_status="NONE")
            if active_t.exists():
                t_days = sum([((t.solved_at.date() - t.raised_at.date()).days if t.solved_at else (
                            sync_end - t.raised_at.date()).days) for t in active_t])
                d4 = round(max(0, min(4, 4 - ((t_days / active_t.count()) - 2))), 2)
        score_d = round(d1 + d2 + d3 + d4, 2)

        # --- BLOCK E: CONVERSION (10% TOTAL) ---
        for cust in sp_customers:
            v_hist = Voucher.objects.filter(party_name__iexact=cust.name, voucher_type="TAX INVOICE").order_by("-date")
            recent = v_hist.filter(date__range=(sync_start, sync_end)).order_by('date')
            if recent.exists():
                first_inv = recent.first()
                prev_inv = v_hist.filter(date__lt=first_inv.date).order_by('-date').first()
                if prev_inv and (first_inv.date - prev_inv.date).days > 90: conv_count += 1
            if v_hist.first(): total_inactive_days += max((sync_end - v_hist.first().date).days, 0)
        e1_score = round(min((conv_count / sync_weeks) * 5, 5), 2)
        avg_inact = round(total_inactive_days / (total_assigned or 1), 2)
        e2_score = 5.0 if avg_inact <= 45 else (2.5 if avg_inact <= 90 else 0.0)
        score_e = round(e1_score + e2_score, 2)

        # --- BLOCK H: GEO (5%) ---
        DIST_MAP = {"Andhra Pradesh": 26, "Kerala": 14, "Maharashtra": 36, "Gujarat": 33, "Karnataka": 31,
                    "Tamil Nadu": 38, "Uttar Pradesh": 75}
        geo_qs = CustomerVoucherStatus.objects.filter(customer__salesperson=selected_sp,
                                                      voucher_type__iexact="TAX INVOICE",
                                                      voucher_date__range=(fy_start, sync_end)).values(
            st=F('customer__state')).annotate(orders=Count('id'),
                                              dists=Count('customer__district', distinct=True)).order_by('-orders')
        if geo_qs.exists():
            top = geo_qs[0]
            top_st_name = top['st']
            top_st_orders = top['orders']
            top_st_dist_actual = top['dists']
            top_st_dist_total = DIST_MAP.get(top_st_name, 1)
            top_st_penetration_pct = round((top_st_dist_actual / top_st_dist_total * 100), 1)
            score_geo = round(min((top_st_penetration_pct / 70) * 5, 5), 2)


        quantitative_total = score_a + score_b + score_c + score_d + score_e + score_geo
        # ------------------------------------------------------
        # 5. CONTEXT UPDATE
        # ------------------------------------------------------
        ctx.update({
            "total_kpi": round(score_a + score_b + score_c + score_d + score_e + score_geo, 1),
            "category_earned_score": score_a, "revenue_earned_score": score_b, "new_customer_earned_score": score_c,
            "total_collection_score": score_d, "total_conversion_score": score_e, "score_geo": score_geo,
            "performance_table": performance_rows, "grand_actual": g_actual, "grand_benchmark": g_bench,
            "grand_ach_pct": round((g_actual / g_bench * 100) if g_bench > 0 else 0, 2),
            "grand_diff": g_actual - g_bench,
            "category_sales": cat_data, "actual_category_count": actual_category_count,
            "new_customers": new_cust_qs, "actual_new_clients": actual_new_clients,
            "top_st_name": top_st_name, "top_st_orders": top_st_orders, "top_st_dist_actual": top_st_dist_actual,
            "top_st_dist_total": top_st_dist_total, "top_st_penetration_pct": top_st_penetration_pct,
            "avg_inactiveness": avg_inact, "converted_count": conv_count, "e1_score": e1_score, "e2_score": e2_score,
            "pending_count": CustomerVoucherStatus.objects.filter(customer__salesperson=selected_sp,
                                                                  is_fully_paid=False).count(),
            "total_expected_dates": PaymentExpectedDateHistory.objects.filter(
                thread__voucher_status__customer__salesperson=selected_sp).count(),
            "total_thread_remarks": PaymentRemark.objects.filter(
                thread__voucher_status__customer__salesperson=selected_sp).count(),
            "total_tickets": active_t.count() if 'active_t' in locals() else 0,
            "unique_products_count": stock_items.values('item').distinct().count(),
            "total_units_sold": stock_items.aggregate(t=Sum('quantity'))['t'] or 0,
            "total_vouchers_count": sold_vids.count(),
            "top_products": stock_items.values('item__name').annotate(qty=Sum('quantity')).order_by('-qty')[:5],
            "remark_frequency": round((total_interactions / (total_assigned or 1)) / sync_weeks, 2),
            "total_kpi": round(quantitative_total + qualitative_score, 1),  # Out of 100
            "qualitative_score": qualitative_score,
            "quantitative_score": round(quantitative_total, 1),
        })
        return ctx


class SalesPersonQualitativeReportView(LoginRequiredMixin, TemplateView):
    template_name = "customers/salesperson_qualitative_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. Handle Navigation & Filtering
        all_sp = SalesPerson.objects.all().order_by("name")
        excluded = ["abhijay", "raman", "vibhuti", "akshay", "nitin", "online", "mukesh", "aryan", "test1", "danish",
                    "jackson", "kaushik", "online order", "nimit"]
        ctx["salespersons"] = [sp for sp in all_sp if sp.name.lower() not in excluded]

        sp_id = self.request.GET.get("salesperson")
        if not sp_id:
            return ctx

        selected_sp = get_object_or_404(SalesPerson, id=sp_id)
        name_key = selected_sp.name.lower()
        ctx["selected_sp"] = selected_sp

        # 2. COMPLETE DATABASE FROM PDFs
        QUALITATIVE_DB = {
            "bhavya": {
                "attendance": {"present": 281, "working": 315, "score": 7.14},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 32, "taken": 31},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 3},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 0},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting", "val": "YES", "rem": "SOMETIMES"},
                    {"ind": "Dress Code Compliance", "val": "YES", "rem": "SOMETIMES"},
                    {"ind": "Behavior Complaints", "val": "NO", "rem": ""},
                    {"ind": "Workplace Violations", "val": "NO", "rem": ""},
                ],
                "teamwork": {"comm": 5, "part": 5, "conflict": 5, "support": 5, "collab": 5, "score": 5.0},
                "integrity": {"hon": 4, "acc": 5, "res": 5, "eth": 5, "val": 5, "score": 4.5},
                "total": 28.5
            },
            "ankush": {
                "attendance": {"present": 285, "working": 315, "score": 7.0},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 32, "taken": 27.5},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 3},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 0},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting (Punctual)", "val": "4", "rem": "Rating (1-5)"},
                    {"ind": "Dress Code Compliance", "val": "5", "rem": "Rating (1-5)"},
                    {"ind": "Behavior Complaints", "val": "5", "rem": ""},
                    {"ind": "Workplace Violations", "val": "5", "rem": ""},
                ],
                "teamwork": {"comm": 5, "part": 5, "conflict": 5, "support": 5, "collab": 5, "score": 5.0},
                "integrity": {"hon": 4, "acc": 4, "res": 4, "eth": 5, "val": 5, "score": 4.5},
                "total": 28.0
            },
            "aman": {
                "attendance": {"present": 285, "working": 315, "score": 7.0},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 32, "taken": 27},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 3},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 0},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting", "val": "NO", "rem": ""},
                    {"ind": "Dress Code Compliance", "val": "NO", "rem": ""},
                    {"ind": "Behavior Complaints", "val": "NO", "rem": ""},
                    {"ind": "Workplace Violations", "val": "NO", "rem": ""},
                ],
                "teamwork": {"comm": 5, "part": 3, "conflict": 5, "support": 5, "collab": 5, "score": 4.5,
                             "rem_part": "Never attend sport activity"},
                "integrity": {"hon": 3.5, "acc": 4, "res": 5, "eth": 5, "val": 5, "score": 4.5,
                              "rem_hon": "Never disclose reason of his leaves"},
                "total": 28.0
            },
            "rushikesh": {
                "attendance": {"present": "N/A", "working": 315, "score": 8.0},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 32, "taken": 4.5},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 1},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 1},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting", "val": "NO", "rem": ""},
                    {"ind": "Dress Code Compliance", "val": "NO", "rem": ""},
                    {"ind": "Behavior Complaints", "val": "NO", "rem": ""},
                    {"ind": "Workplace Violations", "val": "NO", "rem": ""},
                ],
                "teamwork": {"comm": 4, "part": 5, "conflict": 5, "support": 5, "collab": 5, "score": 4.5},
                "integrity": {"hon": 4, "acc": 4, "res": 4.5, "eth": 4, "val": 4, "score": 4.1},
                "total": 27.6,
                "score_discipline": 4.0,
            },
            "satish": {
                "attendance": {"present": 313, "working": 315, "score": 8.0},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 5, "taken": 2},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 0},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 0},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting", "val": "NO", "rem": ""},
                    {"ind": "Dress Code Compliance", "val": "NO", "rem": ""},
                    {"ind": "Behavior Complaints", "val": "NO", "rem": ""},
                    {"ind": "Workplace Violations", "val": "NO", "rem": ""},
                ],
                "teamwork": {"comm": 5, "part": 5, "conflict": 5, "support": 5, "collab": 5, "score": 5.0},
                "integrity": {"hon": 5, "acc": 4.5, "res": 4.5, "eth": 5, "val": 5, "score": 4.5},
                "total": 28.5
            },
            "naveen": {
                "attendance": {"present": 310, "working": 315, "score": 7.14},
                "leaves": [
                    {"type": "EL + CL (Earned & Casual)", "assigned": 20, "taken": 4.5},
                    {"type": "Sick Leave (SL)", "assigned": 5, "taken": 0},
                    {"type": "Bereavement Leave", "assigned": 3, "taken": 0},
                    {"type": "Half Day (Grace)", "assigned": 0, "taken": 0}
                ],
                "compliance": [
                    {"ind": "Policy Violation", "status": "No", "rem": ""},
                    {"ind": "Warning Issued", "status": "No", "rem": ""},
                    {"ind": "Notice Served", "status": "0", "rem": ""},
                    {"ind": "HR Complaint", "status": "No", "rem": ""},
                ],
                "discipline": [
                    {"ind": "Late Reporting", "val": "NO", "rem": ""},
                    {"ind": "Dress Code Compliance", "val": "NO", "rem": ""},
                    {"ind": "Behavior Complaints", "val": "NO", "rem": ""},
                    {"ind": "Workplace Violations", "val": "NO", "rem": ""},
                ],
                "teamwork": {"comm": 5, "part": 5, "conflict": 5, "support": 5, "collab": 5, "score": 5.0},
                "integrity": {"hon": 5, "acc": 4.5, "res": 4.5, "eth": 5, "val": 5, "score": 4.5},
                "total": 28.5
            },
        }

        ctx["data"] = QUALITATIVE_DB.get(name_key, None)
        return ctx

import json
from django.views.generic import TemplateView
from django.db.models import Count
from .models import Customer


class MapView(AccountantRequiredMixin,TemplateView):
    template_name = "customers/map.html"

    # simplified state coords; extend as needed
    STATE_COORDS = {
    "Andhra Pradesh": [15.9129, 79.7400],
    "Arunachal Pradesh": [28.2180, 94.7278],
    "Assam": [26.2006, 92.9376],
    "Bihar": [25.0961, 85.3131],
    "Chhattisgarh": [21.2787, 81.8661],
    "Goa": [15.2993, 74.1240],
    "Gujarat": [22.2587, 71.1924],
    "Haryana": [29.0588, 76.0856],
    "Himachal Pradesh": [31.1048, 77.1734],
    "Jharkhand": [23.6102, 85.2799],
    "Karnataka": [15.3173, 75.7139],
    "Kerala": [10.8505, 76.2711],
    "Madhya Pradesh": [22.9734, 78.6569],
    "Maharashtra": [19.7515, 75.7139],
    "Manipur": [24.6637, 93.9063],
    "Meghalaya": [25.4670, 91.3662],
    "Mizoram": [23.1645, 92.9376],
    "Nagaland": [26.1584, 94.5624],
    "Odisha": [20.9517, 85.0985],
    "Punjab": [31.1471, 75.3412],
    "Rajasthan": [27.0238, 74.2179],
    "Sikkim": [27.5330, 88.5122],
    "Tamil Nadu": [11.1271, 78.6569],
    "Telangana": [18.1124, 79.0193],
    "Tripura": [23.9408, 91.9882],
    "Uttar Pradesh": [26.8467, 80.9462],
    "Uttarakhand": [30.0668, 79.0193],
    "West Bengal": [22.9868, 87.8550],

    "Andaman and Nicobar Islands": [11.7401, 92.6586],
    "Chandigarh": [30.7333, 76.7794],
    "Dadra and Nagar Haveli and Daman and Diu": [20.1809, 73.0169],
    "Delhi": [28.7041, 77.1025],
    "Jammu and Kashmir": [33.7782, 76.5762],
    "Ladakh": [34.1526, 77.5770],
    "Lakshadweep": [10.5667, 72.6417],
    "Puducherry": [11.9416, 79.8083]
}


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = (
            Customer.objects.values("state")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # build a list of dicts with proper key names
        data = []
        for row in qs:
            state = row["state"] or "Unknown"
            count = row["count"]
            lat, lon = self.STATE_COORDS.get(state, [None, None])
            data.append({
                "State": state,
                "Customer_Count": count,
                "lat": lat,
                "lon": lon,
            })

        ctx["map_data_json"] = json.dumps(data)
        return ctx


class DetailedMapView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/detailed_map.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = (
            Customer.objects
            .filter(latitude__isnull=False, longitude__isnull=False)
            .values("state", "district", "latitude", "longitude")
            .annotate(count=Count("id"))
        )

        data = [
            {
                "State": q["state"],
                "District": q["district"],
                "Customer_Count": q["count"],
                "lat": q["latitude"],
                "lon": q["longitude"]
            }
            for q in qs
        ]

        ctx["map_data_json"] = json.dumps(data)
        return ctx


class GeoSalesReportView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/geo_sales_report.html"

    STATE_COORDS = {
        "Andhra Pradesh": [15.91, 79.74], "Arunachal Pradesh": [28.21, 94.72], "Assam": [26.20, 92.93],
        "Bihar": [25.09, 85.31], "Chhattisgarh": [21.27, 81.86], "Goa": [15.29, 74.12],
        "Gujarat": [22.25, 71.19], "Haryana": [29.05, 76.08], "Himachal Pradesh": [31.10, 77.17],
        "Jharkhand": [23.61, 85.27], "Karnataka": [15.31, 75.71], "Kerala": [10.85, 76.27],
        "Madhya Pradesh": [22.97, 78.65], "Maharashtra": [19.75, 75.71], "Manipur": [24.66, 93.90],
        "Meghalaya": [25.46, 91.36], "Mizoram": [23.16, 92.93], "Nagaland": [26.15, 94.56],
        "Odisha": [20.95, 85.09], "Punjab": [31.14, 75.34], "Rajasthan": [27.02, 74.21],
        "Sikkim": [27.53, 88.51], "Tamil Nadu": [11.12, 78.65], "Telangana": [18.11, 79.01],
        "Tripura": [23.94, 91.98], "Uttar Pradesh": [26.84, 80.94], "Uttarakhand": [30.06, 79.01],
        "West Bengal": [22.98, 87.85], "Delhi": [28.70, 77.10], "Jammu and Kashmir": [33.77, 76.57],
        "Ladakh": [34.15, 77.57], "Chandigarh": [30.73, 76.77]
    }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        all_sp = SalesPerson.objects.all().order_by("name")
        hide = ["abhijay", "raman", "vibhuti", "akshay", "nitin", "onine", "online order", "mukesh", "aryan", "test1"]
        ctx["salespersons"] = [sp for sp in all_sp if sp.name.lower() not in hide]
        ctx["categories"] = VoucherStockItem.objects.values_list('item__category__name', flat=True).distinct().exclude(
            item__category__name__isnull=True).order_by('item__category__name')

        sp_id = self.request.GET.get("salesperson")
        selected_cat = self.request.GET.get("category")
        if not sp_id: return ctx

        selected_sp = get_object_or_404(SalesPerson, id=sp_id)
        ctx["selected_sp"] = selected_sp
        ctx["selected_cat"] = selected_cat

        start_date, end_date = date(2025, 4, 1), date(2026, 3, 31)
        base_qs = CustomerVoucherStatus.objects.filter(
            customer__salesperson=selected_sp, voucher_type__iexact="TAX INVOICE",
            voucher_date__range=(start_date, end_date)
        ).select_related('customer')

        # --- SECTION 1 LOGIC (REVENUE MAP & TABLE) ---
        raw_geo = base_qs.values(st=F('customer__state'), dt=F('customer__district'), name=F('customer__name'),
                                 lat=F('customer__latitude'), lon=F('customer__longitude')).annotate(
            total_val=Sum('voucher_amount'), total_orders=Count('id')).order_by('st', 'dt', 'name')

        geo_hierarchy = {}
        s_map1, d_map1 = defaultdict(lambda: {'val': 0, 'count': 0}), {}

        for item in raw_geo:
            st, dt = str(item['st']).strip().title(), str(item['dt']).strip().title()
            val, count = float(item['total_val'] or 0), item['total_orders']

            s_map1[st]['val'] += val
            s_map1[st]['count'] += count

            dk = (st, dt)
            if dk not in d_map1:
                d_map1[dk] = {'val': 0, 'count': 0, 'lat': item['lat'] or self.STATE_COORDS.get(st, [22, 78])[0],
                              'lon': item['lon'] or self.STATE_COORDS.get(st, [22, 78])[1]}
            d_map1[dk]['val'] += val
            d_map1[dk]['count'] += count

            if st not in geo_hierarchy: geo_hierarchy[st] = {'orders': 0, 'value': 0, 'districts': {}}
            geo_hierarchy[st]['orders'] += count
            geo_hierarchy[st]['value'] += val
            if dt not in geo_hierarchy[st]['districts']: geo_hierarchy[st]['districts'][dt] = {'orders': 0, 'value': 0,
                                                                                               'customers': {}}
            geo_hierarchy[st]['districts'][dt]['orders'] += count
            geo_hierarchy[st]['districts'][dt]['value'] += val
            geo_hierarchy[st]['districts'][dt]['customers'][item['name']] = {'orders': count, 'value': val}

        ctx["geo_data"] = OrderedDict(sorted(geo_hierarchy.items()))
        ctx["map1_state_json"] = json.dumps([{'name': k, 'lat': self.STATE_COORDS.get(k, [22, 78])[0],
                                              'lon': self.STATE_COORDS.get(k, [22, 78])[1], 'value': v['val'],
                                              'count': v['count']} for k, v in s_map1.items()])
        ctx["map1_dist_json"] = json.dumps(
            [{'name': f"{k[1]}, {k[0]}", 'lat': v['lat'], 'lon': v['lon'], 'value': v['val'], 'count': v['count']} for
             k, v in d_map1.items()])

        # --- SECTION 2 LOGIC (CATEGORY MAP & TABLE) ---
        cat_hierarchy = {}
        s_map2, d_map2 = defaultdict(float), {}
        if selected_cat:
            v_ids = base_qs.values_list('voucher_id', flat=True)
            cat_raw = VoucherStockItem.objects.filter(voucher_id__in=v_ids, item__category__name=selected_cat).values(
                st=F('voucher__customer_status__customer__state'), dt=F('voucher__customer_status__customer__district'),
                p_name=F('item__name'), lat=F('voucher__customer_status__customer__latitude'),
                lon=F('voucher__customer_status__customer__longitude')).annotate(q=Sum('quantity'),
                                                                                 a=Sum('amount')).order_by('st', 'dt',
                                                                                                           'p_name')

            for item in cat_raw:
                st, dt = str(item['st']).strip().title(), str(item['dt']).strip().title()
                qty, amt = float(item['q'] or 0), float(item['a'] or 0)
                s_map2[st] += qty
                dk = (st, dt)
                if dk not in d_map2:
                    d_map2[dk] = {'qty': 0, 'lat': item['lat'] or self.STATE_COORDS.get(st, [22, 78])[0],
                                  'lon': item['lon'] or self.STATE_COORDS.get(st, [22, 78])[1]}
                d_map2[dk]['qty'] += qty

                if st not in cat_hierarchy: cat_hierarchy[st] = {'qty': 0, 'amt': 0, 'districts': {}}
                if dt not in cat_hierarchy[st]['districts']: cat_hierarchy[st]['districts'][dt] = []
                cat_hierarchy[st]['districts'][dt].append({'p_name': item['p_name'], 'qty': qty, 'amt': amt})
                cat_hierarchy[st]['qty'] += qty
                cat_hierarchy[st]['amt'] += amt

            ctx["cat_hierarchy"] = OrderedDict(sorted(cat_hierarchy.items()))
            ctx["map2_state_json"] = json.dumps([{'name': k, 'lat': self.STATE_COORDS.get(k, [22, 78])[0],
                                                  'lon': self.STATE_COORDS.get(k, [22, 78])[1], 'value': v} for k, v in
                                                 s_map2.items()])
            ctx["map2_dist_json"] = json.dumps(
                [{'name': f"{k[1]}, {k[0]}", 'lat': v['lat'], 'lon': v['lon'], 'value': v['qty']} for k, v in
                 d_map2.items()])

        return ctx