import pandas as pd
from django.core.management.base import BaseCommand
from inventory.models import InventoryItem, Category

class Command(BaseCommand):
    help = "Import or update stock summary from Excel into InventoryItem"

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            help='Path to the Excel file containing stock summary',
            required=True
        )

    def handle(self, *args, **options):
        file_path = options['file']

        try:
            df = pd.read_excel(file_path, sheet_name='stock summary')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error reading Excel: {e}"))
            return

        # Use actual column names from your file
        required_cols = ['Name', 'Parent', 'ClosingQuantity']
        if not all(col in df.columns for col in required_cols):
            self.stdout.write(self.style.ERROR(
                f"Missing required columns. Found: {list(df.columns)}"
            ))
            return

        created_count = 0
        updated_count = 0

        for _, row in df.iterrows():
            product_name = str(row['Name']).strip()
            category_name = str(row['Parent']).strip() if not pd.isna(row['Parent']) else "Uncategorized"
            closing_qty = int(row['ClosingQuantity']) if not pd.isna(row['ClosingQuantity']) else 0

            if not product_name:
                continue

            # Get or create category
            category_obj, _ = Category.objects.get_or_create(name=category_name)

            # Get or create product
            item, created = InventoryItem.objects.get_or_create(name=product_name)
            if created:
                created_count += 1
            else:
                updated_count += 1

            # Update category and quantity
            item.category = category_obj
            item.quantity = closing_qty
            item.save()

        self.stdout.write(self.style.SUCCESS(
            f"Imported stock summary. Created: {created_count}, Updated: {updated_count}"
        ))
