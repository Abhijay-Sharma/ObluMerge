import pandas as pd
from django.core.management.base import BaseCommand
from tally_voucher.models import Voucher, VoucherRow

# how to run - python manage.py import_accounting_vouchers <path_to_excel_file>


class Command(BaseCommand):
    help = "Import accounting vouchers from Excel"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to the Excel file")

    def handle(self, *args, **options):
        file_path = options["file_path"]

        df = pd.read_excel(file_path)
        df["DATE"] = pd.to_datetime(df["DATE"], dayfirst=True)

        for idx, row in df.iterrows():

            # Header values
            date = row["DATE"].date()
            voucher_type = str(row["VOUCHERTYPE"]).strip()
            voucher_number = str(row["VOUCHERNUMBER"]).strip()
            party_name = str(row["PARTYNAME"]).strip()
            voucher_category = str(row["VOUCHERCATEGORY"]).strip()

            # Row-level values
            ledger = str(row["LEDGER"]).strip()
            narration = str(row.get("NARRATION", "") or "").strip()
            amount = abs(float(row["AMOUNT"]))   # always positive

            # 👉 Skip rows where amount is blank or NaN
            if pd.isna(amount) or amount == "":
                print(f"[SKIPPED] Row has no amount → Ledger: {row['LEDGER']}, Voucher: {row['VOUCHERNUMBER']}")
                continue

            amount = abs(float(amount))

            # ------------------------------
            # Create / get voucher header
            # ------------------------------
            voucher, created = Voucher.objects.get_or_create(
                date=date,
                voucher_type=voucher_type,
                voucher_number=voucher_number,
                party_name=party_name,
                voucher_category=voucher_category,
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[CREATED VOUCHER] {voucher_type} {voucher_number} ({party_name})"
                    )
                )

            # ------------------------------
            # Create the row entry
            # ------------------------------
            VoucherRow.objects.create(
                voucher=voucher,
                ledger=ledger,
                narration=narration,
                amount=amount
            )

            self.stdout.write(
                self.style.NOTICE(
                    f"   → Row added: Ledger={ledger}, Amount={amount}"
                )
            )

        self.stdout.write(self.style.SUCCESS("Excel import completed successfully."))
