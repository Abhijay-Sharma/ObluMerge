# vouchers/views.py
from django.shortcuts import render, get_object_or_404
from .models import Voucher , VoucherStockItem
from django.views.generic import DetailView
from django.db.models import Sum
from customer_dashboard.models import Customer

def voucher_list(request):
    # Prefetch related rows and stock_rows to avoid N+1 queries
    vouchers = (
        Voucher.objects
        .prefetch_related("rows", "stock_rows__item")
        .order_by("-date", "-id")
    )

    return render(request, "tally_voucher/voucher_list.html", {"vouchers": vouchers})

class VoucherDetailView(DetailView):
    model = Voucher
    template_name = "tally_voucher/voucher_detail.html"
    context_object_name = "voucher"



def customer_item_purchases(request, customer_id):
    # find vouchers belonging to this customer
    customer = get_object_or_404(Customer, id=customer_id)
    vouchers = Voucher.objects.filter(
        party_name__iexact=customer.name,
        voucher_type="TAX INVOICE"
    )

    # get all stock items for these vouchers
    stock_items = VoucherStockItem.objects.filter(voucher__in=vouchers)

    # aggregate totals per item
    items_summary = (
        stock_items
        .values("item__name", "item_name_text")
        .annotate(
            total_qty=Sum("quantity"),
            total_value=Sum("amount"),
        )
        .order_by("-total_qty")
    )

    return render(request, "tally_voucher/customer_item_purchases.html", {
        "customer": customer,
        "items_summary": items_summary,
    })