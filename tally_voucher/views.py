# vouchers/views.py
from django.shortcuts import render, get_object_or_404
from .models import Voucher , VoucherStockItem, VoucherEmiPaymentAllocation
from customer_dashboard.models import Customer, CustomerVoucherStatus
from django.views.generic import DetailView
from django.db.models import Sum
from customer_dashboard.models import Customer
from django.views.generic import DetailView, ListView
from inventory.mixins import AccountantRequiredMixin
from django.http import JsonResponse
from decimal import Decimal
from django.utils import timezone
from django.db import transaction, OperationalError
import time

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

def party_autocomplete_for_item(request):
    term = request.GET.get('term', '')
    item_id = request.GET.get('item_id')

    vouchers = Voucher.objects.filter(
        stock_rowsitem_id=item_id,
        party_nameicontains=term
    ).values_list('party_name', flat=True).distinct()[:10]

    return JsonResponse(list(vouchers), safe=False)

def get_voucher_products(request, voucher_id):

    voucher = get_object_or_404(Voucher.objects.prefetch_related('rows'), id=voucher_id)


    party_row = next(
        (row for row in voucher.rows.all() if row.ledger == voucher.party_name),
        None
    )
    total_invoice_amount = float(party_row.amount) if party_row else 0.0

    # 3. Get the stock items
    stock_items = VoucherStockItem.objects.filter(voucher=voucher).select_related('item')

    data = []
    for si in stock_items:
        data.append({
            'id': si.id,
            'name': si.item.name if si.item else si.item_name_text,
            'qty': float(si.quantity),
            'total_bill_amount': total_invoice_amount,  # This is the amount in front of the party name
        })
    return JsonResponse(data, safe=False)

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['voucher_types'] = Voucher.objects.values_list('voucher_type', flat=True).distinct()
        context['voucher_categories'] = Voucher.objects.values_list('voucher_category', flat=True).distinct()

        context['params'] = self.request.GET
        return context

class SaveEmiFromVoucherListView(ListView):
    def post(self, request, *args, **kwargs):
        stock_item_id = request.POST.get('stock_item_id')
        allocated_amount = Decimal(request.POST.get('amount', 0))

        # Get or create the allocation
        stock_item = get_object_or_404(VoucherStockItem.objects.select_related('voucher'), id=stock_item_id)
        allocation, created = VoucherEmiPaymentAllocation.objects.get_or_create(
            voucher=stock_item,
            defaults={'amount_received': allocated_amount}
        )

        if not created:
            allocation.amount_received = allocated_amount
            allocation.save()

        customer_name = stock_item.voucher.party_name
        success = False

        for i in range(1, 11):
            try:
                with transaction.atomic():
                    run_bucket_logic_for_customer(customer_name)
                    success = True
                if success: break
            except (OperationalError, Exception) as e:
                if i < 10: time.sleep(0.5)

        if success:
            return JsonResponse({'status': 'success'})
        else:
            return JsonResponse(
                {'status': 'error', 'message': "EMI saved but payment status not processed. Try again."})

def run_bucket_logic_for_customer(customer_name):
    today = timezone.now().date()
    # Filter only for the specific customer updated in the view
    customers = Customer.objects.filter(name__iexact=customer_name)

    for customer in customers:
        try:
            credit_profile = customer.credit_profile
        except Exception:
            continue

        total_tally_balance = Decimal(str(credit_profile.outstanding_balance))
        credit_days = credit_profile.credit_period_days

        manual_allocations = VoucherEmiPaymentAllocation.objects.filter(
            voucher__voucher__party_name__iexact=customer.name
        ).select_related('voucher', 'voucher__voucher')

        total_machine_unpaid = Decimal("0.00")
        emi_voucher_map = {}

        for allocation in manual_allocations:
            parent_voucher = allocation.voucher.voucher
            party_row = parent_voucher.rows.filter(ledger__iexact=parent_voucher.party_name).first()

            if party_row:
                item_price = Decimal(str(party_row.amount))
            else:
                item_price = Decimal(str(allocation.voucher.amount))

            received_so_far = Decimal(str(allocation.amount_received))
            net_unpaid = item_price - received_so_far
            total_machine_unpaid += net_unpaid
            emi_voucher_map[parent_voucher.id] = received_so_far

        remaining_stock_balance = total_tally_balance - total_machine_unpaid
        if total_tally_balance < total_machine_unpaid:
            remaining_stock_balance = total_tally_balance
        if remaining_stock_balance < 0:
            remaining_stock_balance = Decimal("0.00")

        vouchers = Voucher.objects.filter(party_name__iexact=customer.name).order_by("-date", "-id")

        for voucher in vouchers:
            party_row = voucher.rows.filter(ledger__iexact=voucher.party_name).first()
            if not party_row: continue

            voucher_amount = Decimal(str(party_row.amount))
            base_defaults = {
                "voucher_type": voucher.voucher_type,
                "voucher_category": voucher.voucher_category,
                "voucher_date": voucher.date,
                "voucher_amount": voucher_amount,
            }

            if voucher.voucher_type != "TAX INVOICE":
                CustomerVoucherStatus.objects.update_or_create(
                    customer=customer, voucher=voucher,
                    defaults={**base_defaults, "unpaid_amount": None, "is_unpaid": None,
                              "is_partially_paid": None, "is_fully_paid": None}
                )
                continue

            if voucher.id in emi_voucher_map:
                received_amount = emi_voucher_map[voucher.id]
                unpaid_amount = voucher_amount - received_amount
                is_unpaid = (unpaid_amount == voucher_amount)
                is_partially_paid = (0 < unpaid_amount < voucher_amount)
                is_fully_paid = (unpaid_amount <= 0)
            else:
                if remaining_stock_balance >= voucher_amount:
                    unpaid_amount = voucher_amount
                    remaining_stock_balance -= voucher_amount
                    is_unpaid, is_partially_paid, is_fully_paid = True, False, False
                elif remaining_stock_balance > 0:
                    unpaid_amount = remaining_stock_balance
                    remaining_stock_balance = Decimal("0.00")
                    is_unpaid, is_partially_paid, is_fully_paid = False, True, False
                else:
                    unpaid_amount = Decimal("0.00")
                    is_unpaid, is_partially_paid, is_fully_paid = False, False, True

            if is_fully_paid:
                credit_days_elapsed, is_credit_crossed = 0, False
            else:
                credit_days_elapsed = (today - voucher.date).days
                is_credit_crossed = credit_days_elapsed > credit_days

            CustomerVoucherStatus.objects.update_or_create(
                customer=customer, voucher=voucher,
                defaults={
                    **base_defaults, "unpaid_amount": unpaid_amount,
                    "is_unpaid": is_unpaid, "is_partially_paid": is_partially_paid,
                    "is_fully_paid": is_fully_paid, "credit_days_elapsed": credit_days_elapsed,
                    "is_credit_period_crossed": is_credit_crossed,
                }
            )