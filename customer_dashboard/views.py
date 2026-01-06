from django.views.generic import TemplateView, ListView
from django.db.models import Count
from django.shortcuts import render
from .models import Customer, SalesPerson, CustomerVoucherStatus
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




class AdminSalesPersonCustomersView(AccountantRequiredMixin, TemplateView):
    template_name = "customers/admin_salesperson_customers.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["salespersons"] = SalesPerson.objects.all()
        selected_id = self.request.GET.get("salesperson")

        if not selected_id:
            ctx["customers"] = []
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=selected_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            ctx["customers"] = []
            return ctx

        customers = Customer.objects.filter(salesperson=salesperson)
        cutoff_date = date.today() - timedelta(days=90)

        for customer in customers:
            # ---- REMARK LOGIC STARTS HERE ----
            customer.remarks_list = customer.remarks.select_related(
                "salesperson", "salesperson__user"
            ).order_by("-created_at")
            # ---- REMARK LOGIC ENDS HERE ----
            # ---- CREDIT PROFILE LOGIC (NEW) ----
            credit_profile = getattr(customer, "credit_profile", None)

            if credit_profile:
                customer.trial_balance = credit_profile.outstanding_balance
            else:
                customer.trial_balance = None
            # ---- CREDIT PROFILE LOGIC ENDS (NEW) ----

            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            ).order_by("-date")

            customer.vouchers_list = vouchers
            tax_invoice_vouchers = vouchers.filter(voucher_type='TAX INVOICE')
            customer.last_order_date = tax_invoice_vouchers.first().date if tax_invoice_vouchers.exists() else None

            tax_vouchers = vouchers.filter(
                voucher_type__iexact="TAX INVOICE"
            )
            customer.total_orders = tax_vouchers.count()

            total_value = 0
            for v in tax_vouchers:
                total_row = v.rows.filter(
                    ledger__iexact=v.party_name
                ).first()
                if total_row:
                    total_value += total_row.amount

            customer.total_order_value = total_value

            customer.is_red_flag = (
                customer.last_order_date is None or
                customer.last_order_date < cutoff_date
            )

        # ---- NEW CODE HERE ----
        customers = list(customers)

        active = sum(1 for c in customers if not c.is_red_flag)
        inactive = sum(1 for c in customers if c.is_red_flag)
        outstanding_count = sum(
            1 for c in customers if c.trial_balance and c.trial_balance > 0
        )
        total_outstanding_amount = sum(
            c.trial_balance for c in customers if c.trial_balance and c.trial_balance > 0
        )

        ctx["customers"] = customers
        ctx["active_count"] = active
        ctx["inactive_count"] = inactive
        ctx["outstanding_count"] = outstanding_count
        ctx["total_outstanding_amount"] = total_outstanding_amount

        return ctx

    def post(self, request, *args, **kwargs):
        customer_id = request.POST.get("customer_id")
        remark_text = request.POST.get("remark", "").strip()
        salesperson_id = request.GET.get("salesperson")  # keep page state

        salesperson = request.user.salesperson_profile.first()
        if not salesperson:
            return redirect(request.path)

        if not customer_id or not remark_text:
            return redirect(f"{request.path}?salesperson={salesperson_id}")

        customer = get_object_or_404(Customer, id=customer_id)

        CustomerRemark.objects.create(
            customer=customer,
            salesperson=salesperson,
            remark=remark_text
        )

        return redirect(f"{request.path}?salesperson={salesperson_id}")

class CustomerListView(AccountantRequiredMixin, ListView):
    model = Customer
    template_name = "customers/data.html"
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

        return qs

    # --------------------------------------------------
    # POST: Save remark (MANAGER + SALESPERSON)
    # --------------------------------------------------
    def post(self, request, *args, **kwargs):
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

        return redirect(request.path)
    def post(self, request):
        # ✅ ADD THIS AT TOP (DELETE REMARK HANDLING)
        if request.POST.get("delete_remark_id"):
            remark_id = request.POST.get("delete_remark_id")
            remark = get_object_or_404(CustomerRemark, id=remark_id)

            salesperson = request.user.salesperson_profile.first()
            if not salesperson:
                return redirect(request.path)

            # ✅ only owner can delete
            if remark.salesperson != salesperson:
                return HttpResponseForbidden("Not allowed")

            remark.delete()
            return redirect(request.path)

        # ✅ BELOW IS YOUR EXISTING SAVE-REMARK CODE (UNCHANGED)
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

        return redirect(request.path)


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

        return ctx

class CustomerPaymentStatusView(TemplateView):
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
