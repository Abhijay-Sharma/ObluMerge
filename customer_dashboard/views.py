from django.views.generic import TemplateView, ListView
from django.db.models import Count
from django.shortcuts import render
from .models import Customer, SalesPerson
import json
from inventory.mixins import AccountantRequiredMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from datetime import date, timedelta
from tally_voucher.models import Voucher, VoucherRow


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
            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            ).order_by('-date')

            customer.vouchers_list = vouchers
            customer.last_order_date = vouchers.first().date if vouchers.exists() else None

            tax_vouchers = vouchers.filter(voucher_type__iexact="TAX INVOICE")
            customer.total_orders = tax_vouchers.count()

            total_value = 0
            for v in tax_vouchers:
                total_row = v.rows.filter(ledger__iexact=v.party_name).first()
                if total_row:
                    total_value += total_row.amount

            customer.total_order_value = total_value

            customer.is_red_flag = (
                customer.last_order_date is None or
                customer.last_order_date < cutoff_date
            )

        # --- NEW CODE HERE ---
        customers = list(customers)

        active = sum(1 for c in customers if not c.is_red_flag)
        inactive = sum(1 for c in customers if c.is_red_flag)

        ctx["customers"] = customers
        ctx["active_count"] = active
        ctx["inactive_count"] = inactive

        return ctx


class CustomerListView(AccountantRequiredMixin,ListView):
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

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["salespersons"] = SalesPerson.objects.values_list("name", flat=True).distinct()
        ctx["states"] = Customer.objects.values_list("state", flat=True).distinct()
        ctx["total_customers"] = Customer.objects.count()
        ctx["total_salespersons"] = SalesPerson.objects.count()
        ctx["unassigned_count"] = Customer.objects.filter(salesperson__isnull=True).count()
        ctx["top_state"] = (
            Customer.objects.values("state")
            .annotate(c=Count("id"))
            .order_by("-c")
            .first()
        )
        return ctx

class ChartsView(AccountantRequiredMixin,TemplateView):
    template_name = "customers/charts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        salesperson_counts = (
            Customer.objects.values("salesperson__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        salesperson_data = [
            {"Salesperson": s["salesperson__name"] or "Unassigned", "count": s["count"]}
            for s in salesperson_counts
        ]

        state_counts = (
            Customer.objects.values("state")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        state_data = [
            {"State": s["state"] or "Unknown", "count": s["count"]}
            for s in state_counts
        ]

        ctx.update({
            "salesperson_counts_json": json.dumps(salesperson_data),
            "state_counts_json": json.dumps(state_data),
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

    def get_queryset(self):
        user = self.request.user
        cutoff_date = date.today() - timedelta(days=90)

        # --------------------------------------------------
        # 1) Identify logged-in SalesPerson
        # --------------------------------------------------
        salesperson = user.salesperson_profile.first()
        if not salesperson:
            return Customer.objects.none()

        selected_sp = self.request.GET.get("team_member")

        # --------------------------------------------------
        # 2) If MANAGER (manager = None)
        # --------------------------------------------------
        if salesperson.manager is None:
            team_members = salesperson.team_members.all()

            if selected_sp == "me":
                qs = Customer.objects.filter(salesperson=salesperson)

            elif selected_sp == "all" or selected_sp is None:
                qs = Customer.objects.filter(
                    salesperson__in=[salesperson, *team_members]
                )

            else:
                qs = Customer.objects.filter(salesperson__id=selected_sp)

        # --------------------------------------------------
        # 3) If SALESPERSON (has a manager)
        # --------------------------------------------------
        else:
            qs = Customer.objects.filter(salesperson=salesperson)

        # --------------------------------------------------
        # 4) Attach order stats to each customer
        # --------------------------------------------------
        for customer in qs:
            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            ).order_by('-date')

            customer.vouchers_list = vouchers
            customer.last_order_date = vouchers.first().date if vouchers.exists() else None

            # TAX INVOICE vouchers
            tax_vouchers = vouchers.filter(voucher_type__iexact="TAX INVOICE")
            customer.total_orders = tax_vouchers.count()

            # total order value
            total_value = 0
            for v in tax_vouchers:
                total_row = v.rows.filter(ledger__iexact=v.party_name).first()
                if total_row:
                    total_value += total_row.amount

            customer.total_order_value = total_value

            # Red flag (inactive 90+ days)
            if customer.last_order_date is None:
                customer.is_red_flag = True
            else:
                customer.is_red_flag = customer.last_order_date < cutoff_date

        return qs

    # --------------------------------------------------
    # 5) Pass manager/team info to template
    # --------------------------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        salesperson = self.request.user.salesperson_profile.first()

        # check if manager
        ctx["is_manager"] = salesperson and salesperson.manager is None
        ctx["selected_member"] = self.request.GET.get("team_member", "all")

        if ctx["is_manager"]:
            ctx["team_members"] = salesperson.team_members.all()

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
