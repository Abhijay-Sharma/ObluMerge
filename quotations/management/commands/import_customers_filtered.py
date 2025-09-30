import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from quotations.models import Customer

User = get_user_model()

#to use command -  python manage.py import_customers_filtered path/to/customers.xlsx


# Mapping SalesPerson -> Username
SALESPERSON_MAPPING = {
    "Bhavya": "Bhavya_Bhardwaj",
    "Ankush": "Ankush_Oblu",
    "Aman": "Aman_Poddar",
    "Nimit": "Nimit_Sharma",
    "Naveen": "Naveen_K",
    "Satish": "Satish_Kumar",
    "Jackson": "Jackson_Flinto",
    "Rushikesh": "Dhornala_Rushikesh",
    "Danish": "Mohammed_Danish",
}


class Command(BaseCommand):
    help = "Import customers from Excel and assign to users"

    def add_arguments(self, parser):
        parser.add_argument("excel_path", type=str, help="Path to Excel file")

    def handle(self, *args, **options):
        excel_path = options["excel_path"]
        df = pd.read_excel(excel_path)

        created_count = 0
        skipped_count = 0

        for _, row in df.iterrows():
            try:
                # Skip if required fields are missing (Email can be blank)
                if (
                    pd.isna(row["Name"])
                    or pd.isna(row["Pincode"])
                    or pd.isna(row["Address"])
                    or pd.isna(row["State"])
                    or pd.isna(row["District"])
                    or pd.isna(row["Phone"])
                    or pd.isna(row["SalesPerson"])
                ):
                    skipped_count += 1
                    continue

                salesperson = row["SalesPerson"].strip()
                username = SALESPERSON_MAPPING.get(salesperson)

                if not username:
                    self.stdout.write(self.style.WARNING(f"⚠️ No mapping for salesperson {salesperson}, skipping"))
                    skipped_count += 1
                    continue

                try:
                    user = User.objects.get(username=username)
                except User.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f"⚠️ User {username} not found, skipping"))
                    skipped_count += 1
                    continue

                customer, created = Customer.objects.get_or_create(
                    name=str(row["Name"]).strip(),
                    address=str(row["Address"]).strip(),
                    phone=str(row["Phone"]).split(".")[0],  # remove .0 if Excel formatted as float
                    defaults={
                        "state": str(row["State"]).strip(),
                        "city": str(row["District"]).strip(),
                        "pin_code": str(row["Pincode"]).strip(),
                        "email": None if pd.isna(row["Email"]) else str(row["Email"]).strip(),
                        "company": str(row["Name"]).strip(),
                        "created_by": user,
                    },
                )

                if created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f"✅ Added {customer.name} under {user.username}"))
                else:
                    self.stdout.write(self.style.WARNING(f"⏩ Skipped duplicate {customer.name}"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Error with row {row.to_dict()}: {e}"))
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(f"Done! Created: {created_count}, Skipped: {skipped_count}"))
