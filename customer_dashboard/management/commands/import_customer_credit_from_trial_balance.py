from django.core.management.base import BaseCommand
from decimal import Decimal
import pandas as pd

from customer_dashboard.models import Customer, CustomerCreditProfile


class Command(BaseCommand):
    help = "Import customer outstanding balance from Trial Balance Excel"

    def add_arguments(self, parser):
        parser.add_argument(
            "--excel",
            required=True,
            help="Absolute path to Trial Balance Excel file"
        )

    def handle(self, *args, **options):
        excel_path = options["excel"]

        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to read Excel: {e}"))
            return

        created = 0
        updated = 0
        skipped_not_customer = 0
        skipped_not_found = 0

        for _, row in df.iterrows():
            name = str(row.get("Name", "")).strip()
            parent = str(row.get("Parent", "")).strip()
            balance = row.get("Balance", 0)

            # Only Sundry Debtors are customers
            if parent.lower() != "sundry debtors":
                skipped_not_customer += 1
                continue

            if not name:
                skipped_not_found += 1
                continue

            customer = Customer.objects.filter(
                name__iexact=name
            ).first()

            if not customer:
                skipped_not_found += 1
                continue

            balance = Decimal(balance or 0)

            # 🔑 CRITICAL ACCOUNTING FIX
            # Trial Balance convention:
            # Negative balance => customer owes us money
            if balance < 0:
                outstanding_balance = abs(balance)
            else:
                outstanding_balance = Decimal("0.00")

            obj, was_created = CustomerCreditProfile.objects.update_or_create(
                customer=customer,
                defaults={
                    "outstanding_balance": outstanding_balance,
                    "credit_period_days": 0,  # editable later
                }
            )

            if was_created:
                created += 1
            else:
                updated += 1

        # ------------------------
        # SUMMARY
        # ------------------------
        self.stdout.write(self.style.SUCCESS("Trial Balance Import Complete"))
        self.stdout.write(f"Created credit profiles : {created}")
        self.stdout.write(f"Updated credit profiles : {updated}")
        self.stdout.write(f"Skipped (not customers): {skipped_not_customer}")
        self.stdout.write(f"Skipped (no match)     : {skipped_not_found}")
