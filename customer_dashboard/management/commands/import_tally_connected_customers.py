import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from customer_dashboard.models import Customer, SalesPerson


class Command(BaseCommand):
    help = "Import customers from Excel into Customer model"

    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            type=str,
            help="Path to Excel file"
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]

        self.stdout.write(self.style.WARNING("📄 Reading Excel file..."))

        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to read Excel: {e}"))
            return

        # Normalize column names
        df.columns = [c.strip().lower() for c in df.columns]

        REQUIRED_COLUMNS = {
            "name", "address", "state", "district", "pincode", "phone"
        }

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            self.stdout.write(
                self.style.ERROR(f"❌ Missing required columns: {missing}")
            )
            return

        created = 0
        skipped = 0
        salesperson_missing = 0

        skipped_rows = []

        with transaction.atomic():
            for index, row in df.iterrows():
                row_number = index + 2  # Excel row number
                row_data = row.to_dict()

                name = str(row.get("name", "")).strip()
                phone = str(row.get("phone", "")).strip()

                # ❌ Missing required fields
                if not name or not phone:
                    skipped += 1
                    skipped_rows.append({
                        "row": row_number,
                        "reason": "Missing name or phone",
                        "data": row_data
                    })
                    continue

                # ❌ Duplicate check
                if Customer.objects.filter(name__iexact=name, phone=phone).exists():
                    skipped += 1
                    skipped_rows.append({
                        "row": row_number,
                        "reason": "Customer already exists (name + phone)",
                        "data": row_data
                    })
                    continue

                # Salesperson lookup
                salesperson_name = str(row.get("salesperson", "")).strip()
                salesperson = None

                if salesperson_name:
                    salesperson = SalesPerson.objects.filter(
                        name__iexact=salesperson_name
                    ).first()

                    if not salesperson:
                        salesperson_missing += 1

                Customer.objects.create(
                    name=name,
                    phone=phone,
                    address=str(row.get("address", "")).strip(),
                    state=str(row.get("state", "")).strip(),
                    district=str(row.get("district", "")).strip(),
                    pincode=str(row.get("pincode", "")).strip(),
                    salesperson=salesperson,
                    latitude=row.get("latitude") if "latitude" in df.columns else None,
                    longitude=row.get("longitude") if "longitude" in df.columns else None,
                )

                created += 1

        # ===============================
        # FINAL SUMMARY
        # ===============================
        self.stdout.write(self.style.SUCCESS("\n✅ Import completed"))
        self.stdout.write(self.style.SUCCESS(f"🟢 Created: {created}"))
        self.stdout.write(self.style.WARNING(f"🟡 Skipped: {skipped}"))
        self.stdout.write(self.style.WARNING(f"🟠 Missing SalesPerson: {salesperson_missing}"))

        # ===============================
        # PRINT SKIPPED ROW DETAILS
        # ===============================
        if skipped_rows:
            self.stdout.write(self.style.WARNING("\n📋 Skipped Row Details:"))
            for item in skipped_rows:
                self.stdout.write(
                    self.style.WARNING(
                        f"\n⏭️ Row {item['row']}\n"
                        f"Reason: {item['reason']}\n"
                        f"Data: {item['data']}"
                    )
                )
