# this file is made so we can add latest inwards outwards data on IMS
# to run write python manage.py import_stock_data_for_new_data "new excel path"
from django.core.management.base import BaseCommand
from inventory.models import InventoryItem, DailyStockData
import pandas as pd

class Command(BaseCommand):
    help = "Import stock data from Excel and insert DailyStockData entries only if inwards or outwards > 0"

    def add_arguments(self, parser):
        parser.add_argument('excel_file', type=str, help='Path to Excel file')

    def handle(self, *args, **options):
        excel_file = options['excel_file']

        try:
            df = pd.read_excel(excel_file)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error reading Excel file: {e}"))
            return

        required_cols = [
            'ReportDate', 'Name',
            'InwardQuantity', 'InwardValue',
            'OutwardQuantity', 'OutwardValue',
            'ClosingQuantity', 'ClosingValue'
        ]
        for col in required_cols:
            if col not in df.columns:
                self.stderr.write(self.style.ERROR(f"Missing column: {col}"))
                return

        inserted, skipped = 0, 0

        for _, row in df.iterrows():
            product_name = str(row['Name']).strip()
            try:
                product = InventoryItem.objects.get(name=product_name)
            except InventoryItem.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"Product not found: {product_name}, skipping..."))
                skipped += 1
                continue

            # Parse date
            try:
                date = pd.to_datetime(row['ReportDate']).date()
            except Exception:
                self.stderr.write(self.style.WARNING(f"Invalid date for {product_name}, skipping..."))
                skipped += 1
                continue

            inwards_qty = row.get('InwardQuantity', 0) or 0
            outwards_qty = row.get('OutwardQuantity', 0) or 0
            closing_qty = row.get('ClosingQuantity', None)

            # ✅ Skip if both are zero
            if inwards_qty == 0 and outwards_qty == 0:
                skipped += 1
                continue

            try:
                obj, created = DailyStockData.objects.get_or_create(
                    product=product,
                    date=date,
                    inwards_quantity=inwards_qty,
                    outwards_quantity=outwards_qty,
                    closing_quantity=closing_qty,
                    defaults={
                        "inwards_value": row.get('InwardValue', None),
                        "outwards_value": row.get('OutwardValue', None),
                        "closing_value": row.get('ClosingValue', None),
                        "unit": product.unit or 'no',
                        "voucher_type": 'sale',  # or adjust if you want different logic
                    }
                )
                if created:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error inserting {product_name} ({date}): {e}"))
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Done. Inserted: {inserted}, Skipped: {skipped}"))
