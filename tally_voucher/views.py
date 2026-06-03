# vouchers/views.py
from django.shortcuts import render, get_object_or_404
from .models import Voucher , VoucherStockItem
from django.views.generic import DetailView
from django.db.models import Sum
from customer_dashboard.models import Customer
from django.views.generic import DetailView, ListView
from inventory.mixins import AccountantRequiredMixin


class VoucherDetailView(DetailView):
    model = Voucher
    template_name = "tally_voucher/voucher_detail.html"
    context_object_name = "voucher"



def customer_item_purchases(request, customer_id):
    customer = get_object_or_404(Customer, id=customer_id)

    vouchers = Voucher.objects.filter(
        party_name__iexact=customer.name,
        voucher_type="TAX INVOICE"
    )

    stock_items = VoucherStockItem.objects.filter(voucher__in=vouchers)

    # 1) TOTAL SUMMARY PER ITEM
    items_summary_qs = (
        stock_items
        .values("item__name", "item_name_text")
        .annotate(
            total_qty=Sum("quantity"),
            total_value=Sum("amount"),
        )
        .order_by("-total_qty")
    )

    # Convert to dict
    items_summary = {}
    for row in items_summary_qs:
        key = row["item__name"] or row["item_name_text"]
        items_summary[key] = {
            "total_qty": row["total_qty"],
            "total_value": row["total_value"],
            "entries": []          # will fill below
        }

    # 2) DATE-WISE PURCHASE HISTORY
    purchase_history_qs = (
        stock_items
        .values(
            "item__name",
            "item_name_text",
            "voucher__date",
        )
        .annotate(
            qty=Sum("quantity"),
            value=Sum("amount"),
        )
        .order_by("voucher__date")
    )

    # Attach date-wise rows inside dictionary
    for row in purchase_history_qs:
        key = row["item__name"] or row["item_name_text"]

        # Ensure key exists (it always will)
        if key in items_summary:
            items_summary[key]["entries"].append({
                "date": row["voucher__date"],
                "qty": row["qty"],
            })

    return render(request, "tally_voucher/customer_item_purchases.html", {
        "customer": customer,
        "items_summary": items_summary,   # now proper dictionary
    })


class VoucherListView(AccountantRequiredMixin,ListView):
    model = Voucher
    template_name = "tally_voucher/voucher_list.html"
    context_object_name = "vouchers"
    paginate_by = 50

    def get_queryset(self):
        queryset = Voucher.objects.prefetch_related("rows", "stock_rowsitem").order_by("-date", "-id")

        self.q = self.request.GET.get('q', '')
        self.start_date = self.request.GET.get('start_date')
        self.end_date = self.request.GET.get('end_date')
        self.v_type = self.request.GET.get('v_type')
        self.v_cat = self.request.GET.get('v_cat')

        # Apply Filters
        if self.q:
            queryset = queryset.filter(party_nameicontains=self.q)
        if self.start_date:
            queryset = queryset.filter(dategte=self.start_date)
        if self.end_date:
            queryset = queryset.filter(datelte=self.end_date)
        if self.v_type:
            queryset = queryset.filter(voucher_type=self.v_type)
        if self.v_cat:
            queryset = queryset.filter(voucher_category=self.v_cat)

        return queryset

    def get_context_data(self, kwargs):
        context = super().get_context_data(kwargs)

        context['voucher_types'] = Voucher.objects.values_list('voucher_type', flat=True).distinct()
        context['voucher_categories'] = Voucher.objects.values_list('voucher_category', flat=True).distinct()

        context['params'] = self.request.GET
        return context