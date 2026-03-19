from django.core.management.base import BaseCommand
from django.db.models import Q
from datetime import date, timedelta
from decimal import Decimal
from openpyxl import Workbook
from decimal import Decimal
from customer_dashboard.models import Customer
from tally_voucher.models import Voucher


class Command(BaseCommand):
    help = "Export customer report with credit + outstanding + AOV"

    def handle(self, *args, **kwargs):

        wb = Workbook()
        ws = wb.active
        ws.title = "Customer Report"

        # -------- HEADERS --------
        headers = [
            "Customer Name",
            "Salesperson",
            "Credit Period (Days)",
            "Outstanding Balance",
            "Total Orders",
            "Total Order Value",
            "Average Order Value"
        ]
        ws.append(headers)

        customers = Customer.objects.select_related(
            "salesperson",
            "credit_profile"
        ).all()

        self.stdout.write(f"Processing {customers.count()} customers...")

        for customer in customers:

            # -------- CREDIT PROFILE --------
            credit_profile = getattr(customer, "credit_profile", None)

            outstanding = (
                credit_profile.outstanding_balance
                if credit_profile else Decimal("0.00")
            )

            credit_days = (
                credit_profile.credit_period_days
                if credit_profile else 0
            )

            # -------- VOUCHERS --------
            vouchers = Voucher.objects.filter(
                party_name__iexact=customer.name
            )

            tax_invoices = vouchers.filter(
                voucher_type="TAX INVOICE"
            )

            total_orders = tax_invoices.count()

            total_value = Decimal("0.00")

            for v in tax_invoices:
                total_row = v.rows.filter(
                    ledger__iexact=v.party_name
                ).first()

                if total_row:
                    total_value += Decimal(str(total_row.amount))

            # -------- AOV --------
            avg_order_value = (
                total_value / total_orders
                if total_orders > 0 else Decimal("0.00")
            )

            # -------- WRITE ROW --------
            ws.append([
                customer.name,
                customer.salesperson.name if customer.salesperson else "",
                credit_days,
                float(outstanding),
                total_orders,
                float(total_value),
                float(avg_order_value)
            ])

        # -------- SAVE FILE --------
        file_name = "customer_report.xlsx"
        wb.save(file_name)

        self.stdout.write(self.style.SUCCESS(f"✅ Report generated: {file_name}"))