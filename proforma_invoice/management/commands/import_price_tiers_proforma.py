import re
import pandas as pd
from decimal import Decimal

from django.core.management.base import BaseCommand

from inventory.models import InventoryItem
from proforma_invoice.models import ProductPrice, ProductPriceTier


class Command(BaseCommand):
    help = "Import product price tiers from Excel file"

    # def add_arguments(self, parser):
    #     parser.add_argument(
    #         'file_path',
    #         type=str,
    #         help='Path to Excel file (Product_price_tier.xlsx)'
    #     )
    #

    def clean_price(self, value):
        """
        Accepts:
        Rs. 100
        Rs100
        100
        100.00
        """

        if value is None or pd.isna(value):
            return None

        # If already numeric (Excel number cell)
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value)).quantize(Decimal("0.00"))

        value = str(value).strip()

        # remove only Rs / rs / ₹ and spaces
        value = re.sub(r'(?i)(rs\.?|₹)', '', value)
        value = value.replace(' ', '')

        if value == '':
            return None

        return Decimal(value).quantize(Decimal("0.00"))

    def handle(self, *args, **options):
        # HARDCODED FILE PATH
        file_path = r"C:\Users\Administrator\Desktop\Product_price_tier (5) (1).xlsx"

        df = pd.read_excel(file_path)

        # 1. Added MSRP to required columns
        required_columns = ['Product', 'min_quantity', 'unit_price', 'MSRP']
        for col in required_columns:
            if col not in df.columns:
                self.stderr.write(self.style.ERROR(f"Missing column: {col}"))
                return

        created_tiers = 0
        updated_tiers = 0

        for index, row in df.iterrows():
            product_name = str(row['Product']).strip()
            min_qty = row['min_quantity']
            raw_price = row['unit_price']
            # 2. Extract raw MSRP
            raw_msrp = row['MSRP']

            if not product_name or pd.isna(min_qty):
                continue

            price = self.clean_price(raw_price)
            # 3. Clean MSRP
            msrp = self.clean_price(raw_msrp)

            if price is None:
                self.stderr.write(f"⚠ Skipped row {index + 2}: Invalid unit price")
                continue

            # 1️⃣ InventoryItem
            try:
                inventory_item = InventoryItem.objects.get(name=product_name)
            except InventoryItem.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"❌ Row {index + 2}: Product '{product_name}' not found"))
                continue

            # 2️⃣ ProductPrice
            # 4. Added msrp to update/defaults
            product_price, created_pp = ProductPrice.objects.get_or_create(
                product=inventory_item,
                defaults={
                    'price': price,
                    'msrp': msrp,  # Set MSRP if creating
                    'has_dynamic_price': True,
                    'min_requirement': 1
                }
            )

            # 5. If ProductPrice already existed, update the MSRP from Excel
            if not created_pp and msrp is not None:
                product_price.msrp = msrp
                product_price.save()

            # 3️⃣ ProductPriceTier
            tier, created = ProductPriceTier.objects.update_or_create(
                product=product_price,
                min_quantity=int(min_qty),
                defaults={
                    'unit_price': price
                }
            )

            if created:
                created_tiers += 1
            else:
                updated_tiers += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Import complete | Created: {created_tiers}, Updated: {updated_tiers}"
            )
        )
