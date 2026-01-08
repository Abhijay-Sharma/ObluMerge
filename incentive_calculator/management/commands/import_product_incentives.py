import pandas as pd

from django.core.management.base import BaseCommand
from decimal import Decimal

from inventory.models import InventoryItem
from incentive_calculator.models import (
    ProductIncentive,
    ProductIncentiveTier,
)


THERMO_CATEGORY_NAME = "thermoforming sheets"
DYNAMIC_MIN_QTY = 3000
DYNAMIC_ASM_INCENTIVE = Decimal("4")
DYNAMIC_RSM_INCENTIVE = Decimal("1")


class Command(BaseCommand):
    help = "Import Product Incentives from Excel and populate ProductIncentive models."

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_path",
            type=str,
            help="Path to incentive Excel file",
        )

    def handle(self, *args, **options):
        excel_path = options["excel_path"]

        self.stdout.write(self.style.WARNING(f"Reading Excel: {excel_path}"))

        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to read Excel: {e}"))
            return

        created_count = 0
        updated_count = 0
        skipped_count = 0
        tier_created = 0

        for idx, row in df.iterrows():
            product_name = str(row.get("Product_Name")).strip()

            # Skip blank rows
            if not product_name or product_name.lower() == "nan":
                skipped_count += 1
                continue

            asm_value = row.get("Product_incentive")
            rsm_value = row.get("RSM INCENTIVE")

            if pd.isna(asm_value) or pd.isna(rsm_value):
                self.stdout.write(
                    self.style.WARNING(f"Skipping row {idx}: incentive missing for {product_name}")
                )
                skipped_count += 1
                continue

            # Find InventoryItem
            product = InventoryItem.objects.filter(name__iexact=product_name).first()
            if not product:
                self.stdout.write(
                    self.style.ERROR(f"Product not found in DB: {product_name}")
                )
                skipped_count += 1
                continue

            # Detect thermoforming category
            is_thermo = (
                product.category
                and product.category.name
                and product.category.name.strip().lower() == THERMO_CATEGORY_NAME
            )

            # Create / Update ProductIncentive
            incentive_obj, created = ProductIncentive.objects.update_or_create(
                product=product,
                defaults={
                    "ASM_incentive": Decimal(str(asm_value)),
                    "RSM_incentive": Decimal(str(rsm_value)),
                    "has_dynamic_price": is_thermo,
                },
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

            # Handle dynamic tier only for thermoforming sheets
            if is_thermo:
                tier_obj, tier_created_flag = ProductIncentiveTier.objects.update_or_create(
                    Product_Incentive=incentive_obj,
                    min_quantity=DYNAMIC_MIN_QTY,
                    defaults={
                        "ASM_incentive": DYNAMIC_ASM_INCENTIVE,
                        "RSM_incentive": DYNAMIC_RSM_INCENTIVE,
                    },
                )

                if tier_created_flag:
                    tier_created += 1

        self.stdout.write(self.style.SUCCESS("======================================"))
        self.stdout.write(self.style.SUCCESS(f"ProductIncentive created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"ProductIncentive updated: {updated_count}"))
        self.stdout.write(self.style.SUCCESS(f"Tier created/updated: {tier_created}"))
        self.stdout.write(self.style.SUCCESS(f"Rows skipped: {skipped_count}"))
        self.stdout.write(self.style.SUCCESS("Import completed successfully ✅"))
