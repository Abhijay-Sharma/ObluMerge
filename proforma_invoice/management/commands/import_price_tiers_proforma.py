import re
import pandas as pd
from decimal import Decimal

from django.core.management.base import BaseCommand

from inventory.models import InventoryItem
from proforma_invoice.models import ProductPrice, ProductPriceTier


class Command(BaseCommand):
    help = "Import product price tiers from Excel file"

    def add_arguments(self, parser):
        parser.add_argument(
            'file_path',
            type=str,
            help='Path to Excel file (Product_price_tier.xlsx)'
        )

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
        file_path = options['file_path']

        df = pd.read_excel(file_path)

        required_columns = ['Product', 'min_quantity', 'unit_price']
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

            if not product_name or pd.isna(min_qty):
                continue

            price = self.clean_price(raw_price)
            if price is None:
                self.stderr.write(f"⚠ Skipped row {index+2}: Invalid price")
                continue

            # 1️⃣ InventoryItem
            try:
                inventory_item = InventoryItem.objects.get(name=product_name)
            except InventoryItem.DoesNotExist:
                self.stderr.write(
                    self.style.ERROR(
                        f"❌ Row {index+2}: Product '{product_name}' not found in Inventory"
                    )
                )
                continue

            # 2️⃣ ProductPrice
            product_price, _ = ProductPrice.objects.get_or_create(
                product=inventory_item,
                defaults={
                    'price': price,
                    'has_dynamic_price': True,
                    'min_requirement': 1
                }
            )

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
