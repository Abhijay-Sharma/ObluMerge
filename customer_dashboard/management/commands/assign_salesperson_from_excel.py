import pandas as pd

from django.core.management.base import BaseCommand
from customer_dashboard.models import CustomerVoucherStatus, SalesPerson

#this command is made to automatically assign sales to salesperson reading invoices google sheet

class Command(BaseCommand):
    help = "Assign sold_by salesperson to vouchers using Excel sheet"

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_file",
            type=str,
            help="Path to the Excel file"
        )

    def handle(self, *args, **options):

        file_path = options["excel_file"]

        df = pd.read_excel(file_path)

        total_rows = 0
        assigned = 0
        already_assigned = 0
        voucher_not_found = 0
        salesperson_not_found = 0

        for index, row in df.iterrows():

            invoice = str(row.get("Invoice No.", "")).strip()
            if invoice or invoice != "nan":
                #OH/2026-27/278    I'm writing code to add 0 after the second '/' because currently system saved invoices has that 0
                copy=invoice
                invoice = copy[:11]+'0'+copy[11:]
            salesperson_name = str(row.get("Sales Person", "")).strip()

            # Skip child rows where invoice is blank
            if not invoice or invoice == "nan":
                continue

            if not salesperson_name or salesperson_name == "nan":
                continue

            total_rows += 1

            # Find voucher
            try:
                cvs = CustomerVoucherStatus.objects.get(
                    voucher__voucher_number=invoice
                )
            except CustomerVoucherStatus.DoesNotExist:
                voucher_not_found += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[NOT FOUND] Voucher {invoice}"
                    )
                )
                continue

            # If already assigned
            if cvs.sold_by is not None:
                already_assigned += 1
                self.stdout.write(
                    f"[SKIPPED] {invoice} already assigned to {cvs.sold_by.name}"
                )
                continue

            # Find salesperson
            try:
                sp = SalesPerson.objects.get(name__iexact=salesperson_name)
            except SalesPerson.DoesNotExist:
                salesperson_not_found += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"[SALESPERSON NOT FOUND] {salesperson_name} for invoice {invoice}"
                    )
                )
                continue

            # Assign salesperson
            cvs.sold_by = sp
            cvs.save(update_fields=["sold_by"])

            assigned += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"[ASSIGNED] {invoice} → {sp.name}"
                )
            )

        self.stdout.write("\n")
        self.stdout.write(self.style.SUCCESS("------ SUMMARY ------"))
        self.stdout.write(f"Rows processed: {total_rows}")
        self.stdout.write(f"Assigned: {assigned}")
        self.stdout.write(f"Already assigned: {already_assigned}")
        self.stdout.write(f"Voucher not found: {voucher_not_found}")
        self.stdout.write(f"Salesperson not found: {salesperson_not_found}")