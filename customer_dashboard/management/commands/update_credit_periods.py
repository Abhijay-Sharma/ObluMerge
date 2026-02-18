import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from customer_dashboard.models import Customer, CustomerCreditProfile


class Command(BaseCommand):
    help = "Update customer credit periods from Excel by matching customer name"

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_path",
            type=str,
            help="Path to the Excel file"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        excel_path = options["excel_path"]

        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to read Excel file: {e}"))
            return

        updated_count = 0
        created_profiles = 0
        not_found = []

        for _, row in df.iterrows():
            customer_name = str(row.get("Customer Name", "")).strip()
            credit_days = row.get("Credit Period")

            if not customer_name:
                continue

            if pd.isna(credit_days):
                credit_days = 0

            try:
                credit_days = int(credit_days)
            except ValueError:
                credit_days = 0

            customer = Customer.objects.filter(
                name__iexact=customer_name
            ).first()

            if not customer:
                not_found.append(customer_name)
                continue

            profile, created = CustomerCreditProfile.objects.get_or_create(
                customer=customer
            )

            if created:
                created_profiles += 1

            profile.credit_period_days = credit_days
            profile.save()

            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nUpdated {updated_count} customers."
        ))

        self.stdout.write(self.style.SUCCESS(
            f"Created {created_profiles} new credit profiles."
        ))

        if not_found:
            self.stdout.write(self.style.WARNING(
                f"\nCustomers not found ({len(not_found)}):"
            ))
            for name in not_found:
                self.stdout.write(f" - {name}")
