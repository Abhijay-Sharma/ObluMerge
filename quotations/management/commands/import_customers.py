from django.core.management.base import BaseCommand
import pandas as pd
from quotations.models import Customer


class Command(BaseCommand):
    help='Import Customers and there addresses from excel to Category Model'

    def handle(self, *args, **kwargs):
        file_path=r"C:\Users\Administrator\ObluMerge\quotations\management\commands\import_customers.py"
        df = pd.read_excel(file_path)

        for _, row in df.iterrows():
            Customer_name = row['Name']
            State = row['State']
            Address = row['Address']

            if pd.isna(Customer_name) or pd.isna(Address):
                self.stdout.write(self.style.WARNING(f"⚠️ Skipping incomplete row: {row.to_dict()}"))
                continue

            # Create or update the product
            Customer_obj, created = Customer.objects.update_or_create(
                name=Customer_name.strip(),
                state=State,
                address=Address,
            )

            status = "Created" if created else "Updated"
            self.stdout.write(f"{status}: {Customer_name} and {Address}")

        self.stdout.write(self.style.SUCCESS("✅ All products imported successfully."))