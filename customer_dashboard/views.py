from django.views.generic import TemplateView, ListView
from django.db.models import Count
from django.shortcuts import render
from .models import Customer, SalesPerson
import json


class CustomerListView(ListView):
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

class ChartsView(TemplateView):
    template_name = "customers/charts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Salesperson counts
        salesperson_counts = (
            Customer.objects.values("salesperson__name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        salesperson_data = [
            {"Salesperson": s["salesperson__name"] or "Unassigned", "count": s["count"]}
            for s in salesperson_counts
        ]
        ctx["salesperson_counts_json"] = json.dumps(salesperson_data)

        # State counts
        state_counts = (
            Customer.objects.values("state")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        state_data = [
            {"State": s["state"] or "Unknown", "count": s["count"]}
            for s in state_counts
        ]
        ctx["state_counts_json"] = json.dumps(state_data)

        return ctx




class UnassignedView(TemplateView):
    template_name = "customers/unassigned.html"


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unassigned"] = Customer.objects.filter(salesperson__isnull=True)
        return ctx



import json
from django.views.generic import TemplateView
from django.db.models import Count
from .models import Customer


class MapView(TemplateView):
    template_name = "customers/map.html"

    # simplified state coords; extend as needed
    STATE_COORDS = {
        "Maharashtra": [19.7515, 75.7139],
        "Delhi": [28.7041, 77.1025],
        "Karnataka": [15.3173, 75.7139],
        "Gujarat": [22.2587, 71.1924],
        "Tamil Nadu": [11.1271, 78.6569],
        # ... add more
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
