import pandas as pd
from decimal import Decimal
import os
from django.core.management.base import BaseCommand
from inventory.models import InventoryItem
from incentive_calculator.models import IncentiveCategory, ProductIncentive


class Command(BaseCommand):
    help = 'Syncs incentives from Excel and sets Category Default Rates'

    def safe_decimal(self, value):
        if pd.isna(value) or str(value).strip() == "" or value == 0:
            return None
        try:
            return Decimal(str(value))
        except:
            return Decimal('0.00')

    def handle(self, *args, **options):
        # If a category in Excel matches one of these, it gets these rates.
        category_defaults = {
            "Sheets & Aligners": {"asm": 3.0, "rsm": 1.0},
            "Standard Resins": {"asm": 100.0, "rsm": 50.0},
            "Premium Resins (Curo)": {"asm": 500.0, "rsm": 100.0},
            "D-Tech Resins": {"asm": 200.0, "rsm": 50.0},
            "Mid-Range Printers": {"asm": 1000.0, "rsm": 1000.0},
            "Luxury Machines": {"asm": 10000.0, "rsm": 2000.0},
            "Printers": {"asm": 500.0, "rsm": 100.0},
            "Curie Printer": {"asm": 5000.0, "rsm": 1000.0},
        }

        file_path = r"C:\Users\Administrator\Desktop\incentive_price_list.xlsx"
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f'File not found at {file_path}'))
            return

        self.stdout.write(self.style.SUCCESS('--- Starting Sync ---'))
        ProductIncentive.objects.all().delete()

        try:
            df = pd.read_excel(file_path)
            df.columns = [str(c).strip().lower() for c in df.columns]
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            return

        count = 0
        for index, row in df.iterrows():
            raw_name = str(row.get('product_name', '')).strip()
            if not raw_name or raw_name.lower() == 'nan':
                continue

            try:
                inventory_item = InventoryItem.objects.get(name__iexact=raw_name)
                cat_name = str(row.get('category_name', 'Others')).strip()

                # --- SAVING DEFAULT VALUES HERE ---
                # Check if we have default rates defined for this category name
                defaults = category_defaults.get(cat_name, {"asm": 0.0, "rsm": 0.0})

                # Update or Create the category with the Base Rates
                category_obj, _ = IncentiveCategory.objects.update_or_create(
                    name=cat_name,
                    defaults={
                        'base_ASM_incentive': Decimal(str(defaults['asm'])),
                        'base_RSM_incentive': Decimal(str(defaults['rsm']))
                    }
                )

                # Parsing remaining Excel columns
                multiplier = self.safe_decimal(row.get('multiplier', 1)) or Decimal('1.00')
                msp_val = self.safe_decimal(row.get('msp', 0)) or Decimal('0.00')
                is_dyn = str(row.get('is_dynamic', 'FALSE')).upper() in ['TRUE', '1', 'YES', '1.0']

                # Create the product configuration
                config = ProductIncentive.objects.create(
                    product=inventory_item,
                    category=category_obj,
                    msp=msp_val,
                    pack_size_multiplier=multiplier,
                    is_special_pack=(multiplier > 1),
                    has_dynamic_price=is_dyn
                )

                # Apply overrides from Excel (if any)
                asm_ov = self.safe_decimal(row.get('asm_override'))
                rsm_ov = self.safe_decimal(row.get('rsm_override'))
                if asm_ov: config.asm_override = asm_ov
                if rsm_ov: config.rsm_override = rsm_ov

                config.save()
                count += 1

            except InventoryItem.DoesNotExist:
                continue

        self.stdout.write(self.style.SUCCESS(f'Successfully synced {count} products.'))