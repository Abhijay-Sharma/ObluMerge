from django.core.management.base import BaseCommand
from inventory.models import InventoryItem, DailyStockData
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import calendar

class Command(BaseCommand):
    help = "Recalculate demand+buffer minimum stock for all inventory items"

    def handle(self, *args, **kwargs):
        updated = 0

        for product in InventoryItem.objects.all():
            # Fetch daily data
            daily_data = DailyStockData.objects.filter(product=product)

            if not daily_data.exists():
                self.stdout.write(self.style.WARNING(f"No data for {product.name}"))
                continue

            # Convert to DataFrame
            df = pd.DataFrame.from_records(
                daily_data.values(
                    "date", "outwards_quantity", "closing_quantity"
                )
            )
            df["date"] = pd.to_datetime(df["date"])
            df["month"] = df["date"].dt.month
            df["year"] = df["date"].dt.year

            # Group monthly
            monthly_summary = df.groupby(["year", "month"]).agg({
                "outwards_quantity": "sum",
                "closing_quantity": "last"
            }).reset_index()

            if monthly_summary.empty:
                continue

            # Month index for regression
            monthly_summary["MonthIndex"] = np.arange(len(monthly_summary)).reshape(-1, 1)

            # Demand-based regression
            model_demand = LinearRegression()
            model_demand.fit(monthly_summary[["MonthIndex"]], monthly_summary["outwards_quantity"])

            # Predict for next 3 months
            future_months = np.array([len(monthly_summary)+i for i in range(3)]).reshape(-1, 1)
            demand_predictions = model_demand.predict(future_months)

            # Suggested min stock = max future demand + 10% buffer
            min_stock_demand = int(max(demand_predictions) * 1.1)

            # Update product
            product.min_quantity = min_stock_demand
            product.save(update_fields=["min_quantity"])
            updated += 1

            self.stdout.write(self.style.SUCCESS(f"Updated {product.name} → {min_stock_demand}"))

        self.stdout.write(self.style.SUCCESS(f"✅ Done! Updated {updated} products."))
