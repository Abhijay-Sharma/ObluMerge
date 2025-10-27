import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from customer_dashboard.models import Customer, SalesPerson

User = get_user_model()

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
    "Akshay": "Akshay_Narayan",
    "Nitin": "Nitin_Aggarwal"
}

class Command(BaseCommand):
    help = "Import customers and assign them to users based on salesperson mapping"

    def add_arguments(self, parser):
        parser.add_argument("excel_path", type=str, help="Path to Excel file")

    def handle(self, *args, **options):
        excel_path = options["excel_path"]

        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            raise CommandError(f"Error reading Excel: {e}")

        required_columns = ["Name", "Email", "Pincode", "Address", "State", "District", "Phone", "SalesPerson"]
        for col in required_columns:
            if col not in df.columns:
                raise CommandError(f"Missing required column: {col}")

        count = 0
        for _, row in df.iterrows():
            name = str(row["Name"]).strip()
            email = str(row.get("Email", "")).strip() or None
            pincode = str(row.get("Pincode", "")).strip()
            address = str(row.get("Address", "")).strip()
            state = str(row.get("State", "")).strip()
            district = str(row.get("District", "")).strip()
            phone = str(row.get("Phone", "")).strip()
            salesperson_name = str(row.get("SalesPerson", "")).strip()

            if salesperson_name.lower() == "nan" or not salesperson_name:
                salesperson_name = None

            if not name or not address or not state:
                self.stdout.write(self.style.WARNING(f"⚠️ Skipping incomplete row: {name}"))
                continue

            user = None
            salesperson = None

            if salesperson_name in SALESPERSON_MAPPING:
                username = SALESPERSON_MAPPING[salesperson_name]
                user = User.objects.filter(username=username).first()
                if not user:
                    self.stdout.write(self.style.WARNING(f"⚠️ No user found for {salesperson_name} ({username})"))
                salesperson, _ = SalesPerson.objects.get_or_create(name=salesperson_name, user=user)
            elif salesperson_name:
                salesperson, _ = SalesPerson.objects.get_or_create(name=salesperson_name)

            Customer.objects.update_or_create(
                name=name,
                phone=phone,
                defaults={
                    "email": email,
                    "pincode": pincode,
                    "address": address,
                    "state": state,
                    "district": district,
                    "phone": phone,
                    "salesperson": salesperson,
                },
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Successfully imported {count} customers."))
