import pandas as pd
import re

from django.core.management.base import BaseCommand
from inventory.models import InventoryItem
from proforma_invoice.models import ProductPrice
from decimal import Decimal


class Command(BaseCommand):
    help = "Import Product Prices from Excel with intelligent name matching"

    FILE_PATH = r"C:\Users\abhij\Downloads\HSN+tally SUMMARY (8).xlsx"

    # ---------------- CLEANERS ---------------- #

    def clean_decimal(self, value):
        """
        Accepts all Indian price formats like:
        Rs. 310
        Rs. 8,800/-
        Rs. Rs. 10800
        15,500/-
        """

        if pd.isna(value):
            return None

        value = str(value).strip()

        # remove Rs, rs, INR (any count)
        value = re.sub(r"(?i)rs\.?", "", value)
        value = re.sub(r"(?i)inr", "", value)

        # remove /- and commas
        value = value.replace("/-", "")
        value = value.replace(",", "")

        # keep only digits + decimal
        value = re.sub(r"[^\d.]", "", value)

        if value == "":
            return None

        try:
            return Decimal(value)
        except Exception:
            return None

    def clean_tax_rate(self, value):
        """
        Ensure GST is stored as percentage:
        0.18 -> 18
        18   -> 18
        """
        rate = self.clean_decimal(value)
        if rate is None:
            return Decimal("0")

        if rate <= 1:
            return rate * 100  # 0.18 → 18
        return rate  # already percentage

    def clean_bool(self, value):
        return str(value).strip().lower() in ["yes", "y", "true", "1"]

    def normalize_name(self, name):
        """
        Normalize product names for loose matching
        """
        name = name.lower()
        name = re.sub(r"[^a-z0-9 ]+", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name

    # ---------------- MAIN ---------------- #

    def handle(self, *args, **options):
        df = pd.read_excel(self.FILE_PATH)

        created = 0
        updated = 0
        skipped = []

        inventory_items = list(InventoryItem.objects.all())

        # Pre-normalize inventory names
        inventory_map = {
            self.normalize_name(item.name): item
            for item in inventory_items
        }

        for index, row in df.iterrows():
            raw_name = row.get("Particulars")

            if pd.isna(raw_name):
                continue

            excel_name = str(raw_name).strip()
            normalized_excel = self.normalize_name(excel_name)

            inventory_item = None

            # 1️⃣ Exact normalized match
            inventory_item = inventory_map.get(normalized_excel)

            # 2️⃣ Contains match fallback
            if not inventory_item:
                for norm_name, item in inventory_map.items():
                    if normalized_excel in norm_name or norm_name in normalized_excel:
                        inventory_item = item
                        break

            if not inventory_item:
                skipped.append(f"{excel_name} → InventoryItem not found")
                continue

            price = self.clean_decimal(row.get("Price"))
            if price is None:
                skipped.append(f"{excel_name} → Invalid price")
                continue

            tax_rate = self.clean_tax_rate(row.get("Tax_Rate"))
            hsn = row.get("HSN NO.")

            min_qty_raw = row.get("Min_Qty")
            try:
                min_qty = int(float(min_qty_raw)) if not pd.isna(min_qty_raw) else 1
            except Exception:
                min_qty = 1

            has_dynamic = self.clean_bool(row.get("Dynamic_Prices"))

            obj, is_created = ProductPrice.objects.update_or_create(
                product=inventory_item,
                defaults={
                    "price": price,
                    "tax_rate": tax_rate,
                    "min_requirement": min_qty,
                    "has_dynamic_price": has_dynamic,
                    "hsn": hsn,
                }
            )

            if is_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS("✔ IMPORT FINISHED"))
        self.stdout.write(f"Created: {created}")
        self.stdout.write(f"Updated: {updated}")

        if skipped:
            self.stdout.write("\n⚠ Skipped rows:")
            for s in skipped:
                self.stdout.write(f"- {s}")
