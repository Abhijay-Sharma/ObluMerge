from datetime import date, timedelta

from django.core.management.base import BaseCommand
from openpyxl import Workbook

from customer_dashboard.models import (
    SalesPerson,
    Customer,
    CustomerFollowUp,
)

from tally_voucher.models import Voucher


class Command(BaseCommand):
    help = "Export inactive customers of all salespersons to Excel"

    def handle(self, *args, **options):

        from datetime import date, timedelta

        today = date.today()
        cutoff_date = today - timedelta(days=90)

        # ----------------------------
        # GET ALL SALESPERSONS
        # ----------------------------
        salespersons = SalesPerson.objects.all().order_by("name")

        # ----------------------------
        # CREATE EXCEL WORKBOOK
        # ----------------------------
        wb = Workbook()

        # Remove default sheet
        default_sheet = wb.active
        wb.remove(default_sheet)

        # ----------------------------
        # LOOP THROUGH SALESPERSONS
        # ----------------------------
        for sp in salespersons:

            customers = Customer.objects.filter(
                salesperson=sp
            )

            # Excel sheet names max length = 31
            sheet_name = (
                sp.name[:31]
                if sp.name
                else "Unknown"
            )

            ws = wb.create_sheet(title=sheet_name)

            # ----------------------------
            # HEADER ROW
            # ----------------------------
            ws.append([
                "Customer Name",
                "Phone Number",
                "Email",
                "City",
                "Address",
                "Last Invoice Date",
                "Latest Remark",
                "Follow-up Date",
                "Follow-up Status",
            ])

            # ----------------------------
            # LOOP THROUGH CUSTOMERS
            # ----------------------------
            for customer in customers:

                # ----------------------------
                # LAST TAX INVOICE
                # ----------------------------
                tax_invoice = (
                    Voucher.objects.filter(
                        party_name__iexact=customer.name,
                        voucher_type__iexact="TAX INVOICE",
                    )
                    .order_by("-date")
                    .first()
                )

                last_invoice_date = (
                    tax_invoice.date
                    if tax_invoice
                    else None
                )

                # ----------------------------
                # SKIP ACTIVE CUSTOMERS
                # ----------------------------
                if (
                    last_invoice_date
                    and last_invoice_date >= cutoff_date
                ):
                    continue

                # ----------------------------
                # LATEST REMARK
                # ----------------------------
                latest_remark = (
                    customer.remarks
                    .order_by("-created_at")
                    .first()
                )

                # ----------------------------
                # LATEST FOLLOW-UP
                # ----------------------------
                latest_followup = (
                    CustomerFollowUp.objects.filter(
                        customer=customer,
                        salesperson=sp,
                    )
                    .order_by("-followup_date")
                    .first()
                )

                # ----------------------------
                # FOLLOW-UP STATUS
                # ----------------------------
                followup_status = "None"

                if latest_followup:

                    if latest_followup.is_completed:
                        followup_status = "Completed"

                    elif latest_followup.followup_date < today:
                        followup_status = "Overdue"

                    else:
                        followup_status = "Upcoming"

                # ----------------------------
                # CUSTOMER DETAILS
                # ----------------------------
                phone_number = getattr(
                    customer,
                    "phone",
                    ""
                )

                email = getattr(
                    customer,
                    "email",
                    ""
                )

                city = getattr(
                    customer,
                    "city",
                    ""
                )

                address = getattr(
                    customer,
                    "address",
                    ""
                )

                # ----------------------------
                # ADD ROW TO SHEET
                # ----------------------------
                ws.append([
                    customer.name,

                    phone_number,

                    email,

                    city,

                    address,

                    str(last_invoice_date)
                    if last_invoice_date
                    else "No Invoice",

                    latest_remark.remark
                    if latest_remark
                    else "",

                    str(latest_followup.followup_date)
                    if latest_followup
                    else "",

                    followup_status,
                ])

            # ----------------------------
            # AUTO ADJUST COLUMN WIDTH
            # ----------------------------
            for column_cells in ws.columns:

                length = max(
                    len(str(cell.value))
                    if cell.value
                    else 0
                    for cell in column_cells
                )

                column_letter = (
                    column_cells[0].column_letter
                )

                ws.column_dimensions[
                    column_letter
                ].width = length + 5

        # ----------------------------
        # SAVE EXCEL FILE
        # ----------------------------
        file_name = (
            "inactive_customers_all_salespersons.xlsx"
        )

        wb.save(file_name)

        # ----------------------------
        # SUCCESS MESSAGE
        # ----------------------------
        self.stdout.write(
            self.style.SUCCESS(
                f"Excel exported successfully: {file_name}"
            )
        )