from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
from django.db.models import Sum

from customer_dashboard.models import Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher, VoucherEmiPaymentAllocation


class Command(BaseCommand):
    help = "Sync CustomerVoucherStatus with Manual EMI Differentiation"

    def handle(self, *args, **options):
        today = timezone.now().date()

        total_customers = 0
        total_vouchers_processed = 0
        skipped_no_party_row = 0

        for customer in Customer.objects.all():
            total_customers += 1

            try:
                credit_profile = customer.credit_profile
            except Exception:
                continue

            # 1. GET TOTAL BALANCE FROM TALLY
            total_tally_balance = Decimal(str(credit_profile.outstanding_balance))
            credit_days = credit_profile.credit_period_days

            # 2. CALCULATE RESERVED MACHINE DEBT
            # Get all manual allocations for this specific customer
            manual_allocations = VoucherEmiPaymentAllocation.objects.filter(
                voucher__voucher__party_name__iexact=customer.name
            ).select_related('voucher', 'voucher__voucher')

            total_machine_unpaid = Decimal("0.00")
            emi_voucher_map = {}  # To quickly find manual data inside the loop

            for allocation in manual_allocations:
                # going to parent voucher to get invoice price not item price
                parent_voucher = allocation.voucher.voucher

                party_row = parent_voucher.rows.filter(
                    ledger__iexact=parent_voucher.party_name
                ).first()

                if party_row:
                    item_price = Decimal(str(party_row.amount))
                else:
                    item_price = Decimal(str(allocation.voucher.amount))
                received_so_far = Decimal(str(allocation.amount_received))

                # Debt remaining on this machine
                net_unpaid = item_price - received_so_far
                total_machine_unpaid += net_unpaid

                # Store the manual 'received' amount for the loop below
                emi_voucher_map[allocation.voucher.voucher.id] = received_so_far

            # 3. CALCULATE STOCK BUCKET
            # The money available for regular stock is Total Balance minus Machine Debt
            remaining_stock_balance = total_tally_balance - total_machine_unpaid
            if total_tally_balance < total_machine_unpaid:
                remaining_stock_balance = total_tally_balance
            if remaining_stock_balance < 0:
                remaining_stock_balance = Decimal("0.00")

            # 4. FETCH VOUCHERS (Newest First)
            vouchers = (
                Voucher.objects
                .filter(party_name__iexact=customer.name)
                .order_by("-date", "-id")
            )

            for voucher in vouchers:
                total_vouchers_processed += 1

                party_row = voucher.rows.filter(
                    ledger__iexact=voucher.party_name
                ).first()

                if not party_row:
                    skipped_no_party_row += 1
                    continue

                voucher_amount = Decimal(str(party_row.amount))

                base_defaults = {
                    "voucher_type": voucher.voucher_type,
                    "voucher_category": voucher.voucher_category,
                    "voucher_date": voucher.date,
                    "voucher_amount": voucher_amount,
                }

                # ----------------------------
                # NON–TAX INVOICE (Receipts etc)
                # ----------------------------
                if voucher.voucher_type != "TAX INVOICE":
                    CustomerVoucherStatus.objects.update_or_create(
                        customer=customer,
                        voucher=voucher,
                        defaults={
                            **base_defaults,
                            "unpaid_amount": None,
                            "is_unpaid": None,
                            "is_partially_paid": None,
                            "is_fully_paid": None,
                            "credit_days_elapsed": None,
                            "is_credit_period_crossed": None,
                        }
                    )
                    continue

                # ----------------------------
                # TAX INVOICE LOGIC (Two-Track)
                # ----------------------------

                # TRACK A: IF VOUCHER IS MANUALLY MARKED AS EMI
                if voucher.id in emi_voucher_map:
                    received_amount = emi_voucher_map[voucher.id]
                    unpaid_amount = voucher_amount - received_amount

                    is_unpaid = (unpaid_amount == voucher_amount)
                    is_partially_paid = (0 < unpaid_amount < voucher_amount)
                    is_fully_paid = (unpaid_amount <= 0)

                    # Note: We do NOT subtract from remaining_stock_balance here
                    # because EMI debt is handled separately.

                # TRACK B: REGULAR STOCK INVOICE (FIFO)
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

                # ----------------------------
                # COMMON CREDIT LOGIC
                # ----------------------------
                if is_fully_paid:
                    credit_days_elapsed = 0
                    is_credit_crossed = False
                else:
                    credit_days_elapsed = (today - voucher.date).days
                    is_credit_crossed = credit_days_elapsed > credit_days

                CustomerVoucherStatus.objects.update_or_create(
                    customer=customer,
                    voucher=voucher,
                    defaults={
                        **base_defaults,
                        "unpaid_amount": unpaid_amount,
                        "is_unpaid": is_unpaid,
                        "is_partially_paid": is_partially_paid,
                        "is_fully_paid": is_fully_paid,
                        "credit_days_elapsed": credit_days_elapsed,
                        "is_credit_period_crossed": is_credit_crossed,
                    }
                )

        self.stdout.write(self.style.SUCCESS("Customer Voucher Sync Complete"))
        self.stdout.write(f"Customers processed : {total_customers}")
        self.stdout.write(f"Vouchers processed  : {total_vouchers_processed}")
        self.stdout.write(f"Skipped party rows  : {skipped_no_party_row}")