from django.core.management.base import BaseCommand
from proforma_invoice.models import CourierCharge, CourierChargeTier
from inventory.models import InventoryItem
from django.db import transaction
import pandas as pd
from collections import defaultdict


class Command(BaseCommand):
    help = "Import AIR courier rates from Excel"

    EXCEL_PATH = r"C:\Users\Administrator\Desktop\Courier_Rates_Air.xlsx"

    def handle(self, *args, **options):
        self.stdout.write(f"\n📥 Loading AIR Excel: {self.EXCEL_PATH}")

        # Debug counters
        products_found = 0
        products_missing = 0
        sheets_created = 0
        tiers_created = 0

        xls = pd.ExcelFile(self.EXCEL_PATH)
        df_map = pd.read_excel(xls, "PRODUCT_TEMPLATE_MAP")
        df_slabs = pd.read_excel(xls, "SLABS")

        # Normalize headers
        for df in (df_map, df_slabs):
            df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

        template_sheet_map = defaultdict(list)

        with transaction.atomic():

            # -------------------------------
            # STEP 1: Product → Sheet (AIR)
            # -------------------------------
            for _, row in df_map.iterrows():
                product_name = str(row["product_name"]).strip()
                template_code = str(row["template_code"]).strip()

                product = InventoryItem.objects.filter(
                    name__iexact=product_name
                ).first()

                if not product:
                    products_missing += 1
                    self.stderr.write(f"❌ Product not found: {product_name}")
                    continue

                products_found += 1

                sheet, created = CourierCharge.objects.get_or_create(
                    product=product,
                    mode="air"
                )

                if created:
                    sheets_created += 1

                template_sheet_map[(template_code, "air")].append(sheet)

            # -------------------------------
            # STEP 2: Slabs → Tiers (AIR)
            # -------------------------------
            for _, row in df_slabs.iterrows():
                template_code = str(row["template_code"]).strip()

                min_qty = int(row["min_qty"])
                max_qty = None if pd.isna(row["max_qty"]) else int(row["max_qty"])
                charge = float(row["courier_charge"])

                sheets = template_sheet_map.get((template_code, "air"), [])

                for sheet in sheets:
                    CourierChargeTier.objects.create(
                        courier_product=sheet,
                        min_quantity=min_qty,
                        max_quantity=max_qty,
                        charge=charge
                    )
                    tiers_created += 1

        # -------------------------------
        # SUMMARY
        # -------------------------------
        self.stdout.write(self.style.SUCCESS("\n📊 AIR IMPORT SUMMARY"))
        self.stdout.write("----------------------------------")
        self.stdout.write(f"✅ Products found      : {products_found}")
        self.stdout.write(f"❌ Products missing    : {products_missing}")
        self.stdout.write(f"📦 Sheets created      : {sheets_created}")
        self.stdout.write(f"📐 Tiers created       : {tiers_created}")
        self.stdout.write("----------------------------------")
        self.stdout.write(self.style.SUCCESS("✅ Air import completed\n"))
