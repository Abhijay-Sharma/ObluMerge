import pandas as pd
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date
from django.db import transaction
from decimal import Decimal
from datetime import datetime

from tally_voucher.models import Voucher, VoucherStockItem  # CHANGE app name
from inventory.models import InventoryItem


class Command(BaseCommand):
    help = "Import voucher data from Excel"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to Excel file")

    def handle(self, *args, **options):
        file_path = options["file_path"]

        self.stdout.write(self.style.WARNING("Reading Excel..."))
        df = pd.read_excel(file_path)

        df.columns = [
            "date", "voucher_type", "voucher_number", "party_name",
            "voucher_category", "narration", "stockitem", "quantity",
            "amount", "godown"
        ]

        df.fillna("", inplace=True)

        total = len(df)

        for index, row in df.iterrows():
            try:
                with transaction.atomic():

                    # --- DATE ---
                    date_str = row["date"]
                    if isinstance(date_str, datetime):
                        dt = date_str.date()
                    else:
                        dt = datetime.strptime(str(date_str), "%d-%m-%Y").date()

                    # --- Voucher (unique by date + type + number) ---
                    voucher, created = Voucher.objects.get_or_create(
                        date=dt,
                        voucher_type=row["voucher_type"],
                        voucher_number=str(row["voucher_number"]),
                        defaults={
                            "party_name": row["party_name"],
                            "voucher_category": row["voucher_category"],
                        }
                    )

                    # Update fields if needed
                    if not created:
                        voucher.party_name = row["party_name"]
                        voucher.voucher_category = row["voucher_category"]
                        voucher.save()

                    # --- Stock item row? ---
                    stockitem = row["stockitem"].strip()

                    if stockitem:  # only create if STOCKITEM exists

                        # Find matching inventory item
                        item_obj = InventoryItem.objects.filter(name__iexact=stockitem).first()

                        qty = row["quantity"]
                        amt = row["amount"]

                        if qty == "" or qty is None:
                            qty = Decimal("0")

                        if amt == "" or amt is None:
                            amt = Decimal("0")

                        qty = Decimal(str(qty))
                        amt = Decimal(str(amt))

                        VoucherStockItem.objects.create(
                            voucher=voucher,
                            item=item_obj,
                            item_name_text=stockitem if not item_obj else "",
                            quantity=qty,
                            amount=amt,
                            godown=row["godown"] or ""
                        )

                print(f"✔ Row {index+1}/{total} imported")

            except Exception as e:
                print(f"❌ ERROR row {index+1}: {e}")
                continue

        self.stdout.write(self.style.SUCCESS("Import done!"))
