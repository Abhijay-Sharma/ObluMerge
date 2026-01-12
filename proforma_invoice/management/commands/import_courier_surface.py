from django.core.management.base import BaseCommand
from proforma_invoice.models import CourierCharge, CourierChargeTier
from inventory.models import InventoryItem
from django.db import transaction
import pandas as pd
from collections import defaultdict


class Command(BaseCommand):
    help = "Import SURFACE courier rates from Excel"

    EXCEL_PATH = r"C:\Users\Administrator\Desktop\Courier_Rates_Surface.xlsx"
    def handle(self, *args, **options):
        self.stdout.write(f"\n📥 Loading SURFACE Excel: {self.EXCEL_PATH}")

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
            # STEP 1: Product → Sheet (SURFACE)
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
                    mode="surface"
                )

                if created:
                    sheets_created += 1

                template_sheet_map[(template_code, "surface")].append(sheet)

            # -------------------------------
            # STEP 2: Slabs → Tiers (SURFACE)
            # -------------------------------
            for _, row in df_slabs.iterrows():
                template_code = str(row["template_code"]).strip()

                min_qty = int(row["min_qty"])
                max_qty = None if pd.isna(row["max_qty"]) else int(row["max_qty"])
                charge = float(row["courier_charge"])

                sheets = template_sheet_map.get((template_code, "surface"), [])

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
        self.stdout.write(self.style.SUCCESS("\n📊 SURFACE IMPORT SUMMARY"))
        self.stdout.write("----------------------------------")
        self.stdout.write(f"✅ Products found      : {products_found}")
        self.stdout.write(f"❌ Products missing    : {products_missing}")
        self.stdout.write(f"📦 Sheets created      : {sheets_created}")
        self.stdout.write(f"📐 Tiers created       : {tiers_created}")
        self.stdout.write("----------------------------------")
        self.stdout.write(self.style.SUCCESS("✅ Surface import completed\n"))




# from django.core.management.base import BaseCommand
# from django.db import transaction
# from collections import defaultdict
# import pandas as pd
#
# from inventory.models import InventoryItem
# from proforma_invoice.models import CourierCharge, CourierChargeTier
#
#
# # -------------------------------
# # HELPER CLASS → SURFACE IMPORT
# # -------------------------------
# class SurfaceCommand(BaseCommand):
#     help = "Import SURFACE courier rates"
#
#     FILE_PATH = r"C:\Users\Lenovo\Downloads\Courier_Rates_Surface.xlsx"
#     MODE = "surface"
#
#     def handle(self, *args, **options):
#         self.stdout.write(f"\n📥 Loading SURFACE Excel: {self.FILE_PATH}")
#
#         xls = pd.ExcelFile(self.FILE_PATH)
#         df_map = pd.read_excel(xls, "PRODUCT_TEMPLATE_MAP")
#         df_slabs = pd.read_excel(xls, "SLABS")
#
#         # normalize headers
#         for df in (df_map, df_slabs):
#             df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
#
#         template_map = defaultdict(list)
#
#         with transaction.atomic():
#
#             # STEP 1: Product → CourierCharge
#             for _, row in df_map.iterrows():
#                 product_name = str(row["product_name"]).strip()
#                 template_code = str(row["template_code"]).strip()
#
#                 product = InventoryItem.objects.filter(
#                     name__iexact=product_name
#                 ).first()
#
#                 if not product:
#                     self.stderr.write(f"❌ SURFACE Product not found: {product_name}")
#                     continue
#
#                 charge_obj, _ = CourierCharge.objects.get_or_create(
#                     product=product,
#                     mode=self.MODE
#                 )
#
#                 template_map[template_code].append(charge_obj)
#
#                 self.stdout.write(f"✔ SURFACE Product linked: {product.name}")
#
#             # STEP 2: Slabs → Tiers
#             for _, row in df_slabs.iterrows():
#                 template_code = str(row["template_code"]).strip()
#                 min_qty = int(row["min_qty"])
#                 max_qty = None if pd.isna(row["max_qty"]) else int(row["max_qty"])
#                 charge = float(row["courier_charge"])
#
#                 for charge_obj in template_map.get(template_code, []):
#                     CourierChargeTier.objects.create(
#                         courier_charge=charge_obj,
#                         min_quantity=min_qty,
#                         max_quantity=max_qty,
#                         charge=charge
#                     )
#
#                     self.stdout.write(
#                         f"   ➜ SURFACE Slab {min_qty}-{max_qty or '∞'} ₹{charge}"
#                     )
#
#         self.stdout.write(self.style.SUCCESS("✅ SURFACE import completed"))
#
#
# # -------------------------------
# # MAIN COMMAND → AIR + SURFACE
# # -------------------------------
# class Command(BaseCommand):
#     help = "Import AIR courier rates (and then SURFACE)"
#
#     FILE_PATH = r"C:\Users\Lenovo\Downloads\Courier_Rates_Air.xlsx"
#     MODE = "air"
#
#     def handle(self, *args, **options):
#         self.stdout.write(f"\n📥 Loading AIR Excel: {self.FILE_PATH}")
#
#         xls = pd.ExcelFile(self.FILE_PATH)
#         df_map = pd.read_excel(xls, "PRODUCT_TEMPLATE_MAP")
#         df_slabs = pd.read_excel(xls, "SLABS")
#
#         # normalize headers
#         for df in (df_map, df_slabs):
#             df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
#
#         template_map = defaultdict(list)
#
#         with transaction.atomic():
#
#             # STEP 1: Product → CourierCharge
#             for _, row in df_map.iterrows():
#                 product_name = str(row["product_name"]).strip()
#                 template_code = str(row["template_code"]).strip()
#
#                 product = InventoryItem.objects.filter(
#                     name__iexact=product_name
#                 ).first()
#
#                 if not product:
#                     self.stderr.write(f"❌ AIR Product not found: {product_name}")
#                     continue
#
#                 charge_obj, _ = CourierCharge.objects.get_or_create(
#                     product=product,
#                     mode=self.MODE
#                 )
#
#                 template_map[template_code].append(charge_obj)
#
#                 self.stdout.write(f"✔ AIR Product linked: {product.name}")
#
#             # STEP 2: Slabs → Tiers
#             for _, row in df_slabs.iterrows():
#                 template_code = str(row["template_code"]).strip()
#                 min_qty = int(row["min_qty"])
#                 max_qty = None if pd.isna(row["max_qty"]) else int(row["max_qty"])
#                 charge = float(row["courier_charge"])
#
#                 for charge_obj in template_map.get(template_code, []):
#                     CourierChargeTier.objects.create(
#                         courier_charge=charge_obj,
#                         min_quantity=min_qty,
#                         max_quantity=max_qty,
#                         charge=charge
#                     )pyth
#
#                     self.stdout.write(
#                         f"   ➜ AIR Slab {min_qty}-{max_qty or '∞'} ₹{charge}"
#                     )
#
#         self.stdout.write(self.style.SUCCESS("✅ AIR import completed"))
#
#         # 🔁 RUN SURFACE IMPORT (WITHOUT DELETING ANYTHING)
#         self.stdout.write("\n🔁 Running SURFACE import...")
#         SurfaceCommand().handle(*args, **options)
#
