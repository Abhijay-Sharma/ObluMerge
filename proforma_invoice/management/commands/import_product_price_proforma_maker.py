import pandas as pd
import re

from django.core.management.base import BaseCommand
from inventory.models import InventoryItem
from proforma_invoice.models import ProductPrice
from decimal import Decimal


class Command(BaseCommand):
    help = "Import Product Prices from Excel with intelligent name matching"

    FILE_PATH = r"C:\Users\Administrator\Desktop\HSN+tally SUMMARY (10).xlsx"
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

        if pd.isna(value):
            return Decimal("0")

        value = str(value).replace("%", "")

        rate = self.clean_decimal(value)

        if rate is None:
            return Decimal("0")

        if rate <= 1:
            return rate * 100

        return rate
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

        # Load all inventory items once to optimize matching
        inventory_items = list(InventoryItem.objects.all())

        # Pre-normalize inventory names into a lookup map
        inventory_map = {
            self.normalize_name(item.name): item
            for item in inventory_items
        }

        for index, row in df.iterrows():
            raw_name = row.get("Particulars")

            # Skip empty rows
            if pd.isna(raw_name):
                continue

            excel_name = str(raw_name).strip()
            normalized_excel = self.normalize_name(excel_name)

            inventory_item = None

            # 1️⃣ Step 1: Exact normalized match
            inventory_item = inventory_map.get(normalized_excel)

            # 2️⃣ Step 2: Contains match fallback (if exact match fails)
            if not inventory_item:
                for norm_name, item in inventory_map.items():
                    if normalized_excel in norm_name or norm_name in normalized_excel:
                        inventory_item = item
                        break

            # If no item found in inventory, skip this row
            if not inventory_item:
                skipped.append(f"{excel_name} → InventoryItem not found")
                continue

            # --- DATA CLEANING & PREPARATION ---

            # 1. Price (Required)
            price = self.clean_decimal(row.get("New_Price"))
            if price is None:
                skipped.append(f"{excel_name} → Invalid price")
                continue

            # 2. Tax Rate
            tax_rate = self.clean_tax_rate(row.get("Tax_Rate"))

            # 3. HSN Number
            hsn_raw = row.get("HSN NO.")
            hsn = str(int(hsn_raw)) if not pd.isna(hsn_raw) else None

            # 4. Minimum Quantity
            min_qty_raw = row.get("Min_Qty")
            try:
                # Handle cases where Excel might store numbers as floats (e.g., 20.0)
                min_qty = int(float(min_qty_raw)) if not pd.isna(min_qty_raw) else 1
            except Exception:
                min_qty = 1

            # 5. Dynamic Pricing Boolean
            has_dynamic = self.clean_bool(row.get("Dynamic_Prices"))

            # 6. MSRP (Conditional: Ignore if empty)
            msrp_val = self.clean_decimal(row.get("MSRP") if "MSRP" in row else row.get("MSRP "))

            # --- DEBUG LINE ---
            if msrp_val:
                self.stdout.write(f"DEBUG: Found MSRP {msrp_val} for {excel_name}")


            # --- PREPARE THE UPDATE DICTIONARY ---

            # Create the base dictionary with required fields
            import_defaults = {
                "price": price,
                "tax_rate": tax_rate,
                "min_requirement": min_qty,
                "has_dynamic_price": has_dynamic,
                "hsn": hsn,
            }

            # Only add MSRP to the update dictionary if the cell wasn't empty
            # This ensures we don't overwrite existing MSRPs with NULL
            if msrp_val is not None:
                import_defaults["msrp"] = msrp_val

            # --- DATABASE EXECUTION ---

            try:
                obj, is_created = ProductPrice.objects.update_or_create(
                    product=inventory_item,
                    defaults=import_defaults
                )

                if is_created:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                skipped.append(f"{excel_name} → Database Error: {str(e)}")

        # --- FINAL CONSOLE REPORTING ---
        self.stdout.write(self.style.SUCCESS("\n✔ IMPORT FINISHED"))
        self.stdout.write(f"Created: {created}")
        self.stdout.write(f"Updated: {updated}")

        if skipped:
            self.stdout.write(self.style.WARNING(f"\n⚠ Skipped {len(skipped)} rows:"))
            for s in skipped:
                self.stdout.write(f"- {s}")