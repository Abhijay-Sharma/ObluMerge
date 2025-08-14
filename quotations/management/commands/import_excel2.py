from django.core.management.base import BaseCommand
import pandas as pd
from quotations.models import Product, ProductCategory

class Command(BaseCommand):
    help = 'Import products and categories from Excel into Product model'

    def handle(self, *args, **kwargs):
        file_path = r"C:\Users\Administrator\ObluMerge\inventory\management\commands\oblupricelist.xlsx"
        df = pd.read_excel(file_path)

        for _, row in df.iterrows():
            category_name = row['Product Category']
            product_name = row['Product Name']
            price = row['Price']
            tax_rate = row['Tax Rate']

            if pd.isna(category_name) or pd.isna(product_name) or pd.isna(price):
                self.stdout.write(self.style.WARNING(f"⚠️ Skipping incomplete row: {row.to_dict()}"))
                continue

            # Get or create the category
            category_obj, _ = ProductCategory.objects.get_or_create(name=category_name.strip())

            # Create or update the product
            product_obj, created = Product.objects.update_or_create(
                name=product_name.strip(),
                tax_rate=tax_rate,
                defaults={
                    'category': category_obj,
                    'price_per_unit': price,
                    'is_quantity_dependent': True,
                }
            )

            status = "Created" if created else "Updated"
            self.stdout.write(f"{status}: {product_name} under {category_name}")

        self.stdout.write(self.style.SUCCESS("✅ All products imported successfully."))
