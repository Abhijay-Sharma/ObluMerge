from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal

from customer_dashboard.models import Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher


class Command(BaseCommand):
    help = "Sync CustomerVoucherStatus and populate voucher amount for all vouchers"

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

            remaining_balance = Decimal(credit_profile.outstanding_balance)
            credit_days = credit_profile.credit_period_days

            vouchers = (
                Voucher.objects
                .filter(party_name__iexact=customer.name)
                .order_by("-date")
            )

            for voucher in vouchers:
                total_vouchers_processed += 1

                party_row = voucher.rows.filter(
                    ledger__iexact=voucher.party_name
                ).first()

                if not party_row:
                    skipped_no_party_row += 1
                    continue

                voucher_amount = Decimal(party_row.amount)

                base_defaults = {
                    "voucher_type": voucher.voucher_type,
                    "voucher_category": voucher.voucher_category,
                    "voucher_date": voucher.date,
                    "voucher_amount": voucher_amount,
                }

                # ----------------------------
                # NON–TAX INVOICE
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
                # TAX INVOICE PAYMENT LOGIC
                # ----------------------------
                if remaining_balance >= voucher_amount:
                    unpaid_amount = voucher_amount
                    remaining_balance -= voucher_amount

                    is_unpaid = True
                    is_partially_paid = False
                    is_fully_paid = False

                elif remaining_balance > 0:
                    unpaid_amount = remaining_balance
                    remaining_balance = Decimal("0.00")

                    is_unpaid = False
                    is_partially_paid = True
                    is_fully_paid = False

                else:
                    unpaid_amount = Decimal("0.00")

                    is_unpaid = False
                    is_partially_paid = False
                    is_fully_paid = True

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
