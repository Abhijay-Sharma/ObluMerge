# inventory/management/commands/calculate_min_quantities.py
from django.core.management.base import BaseCommand
from inventory.models import InventoryItem, DailyStockData

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
import calendar


class Command(BaseCommand):
    help = "Calculate and update min stock quantities and total historical entries for each inventory item"

    def handle(self, *args, **options):
        items = InventoryItem.objects.all()
        updated_count = 0

        for product in items:
            daily_qs = DailyStockData.objects.filter(product_id=product.id)

            if not daily_qs.exists():
                continue

            df = pd.DataFrame.from_records(
                daily_qs.values('date', 'outwards_quantity', 'closing_quantity')
            )

            if df.empty:
                continue

            df['date'] = pd.to_datetime(df['date'])
            df['month'] = df['date'].dt.month
            df['year'] = df['date'].dt.year

            monthly_summary = df.groupby(['year', 'month']).agg({
                'outwards_quantity': 'sum',
                'closing_quantity': 'last',
            }).reset_index()

            monthly_summary['MonthIndex'] = np.arange(len(monthly_summary))

            # --- Linear Regression: Closing Trend ---
            try:
                model_trend = LinearRegression()
                model_trend.fit(monthly_summary[['MonthIndex']], monthly_summary['closing_quantity'])
                future_months = np.array([len(monthly_summary)+i for i in range(3)]).reshape(-1, 1)
                trend_predictions = model_trend.predict(future_months)
                min_stock_trend = int(min(trend_predictions))
            except Exception:
                min_stock_trend = -1

            # --- Linear Regression: Outwards Demand ---
            try:
                model_demand = LinearRegression()
                model_demand.fit(monthly_summary[['MonthIndex']], monthly_summary['outwards_quantity'])
                demand_predictions = model_demand.predict(future_months)
                min_stock_demand = int(max(demand_predictions) * 1.1)
            except Exception:
                min_stock_demand = -1

            # --- NEW: Average of all months ---
            try:
                min_stock_avg_all = int(monthly_summary['outwards_quantity'].mean() * 1.1)
            except Exception:
                min_stock_avg_all = -1

            # --- NEW: Average of last 3 months ---
            try:
                last_3 = monthly_summary.tail(3)['outwards_quantity']
                min_stock_avg_3 = int(last_3.mean() * 1.1)
            except Exception:
                min_stock_avg_3 = -1

            # --- Save results ---
            product.min_quantity_closing = min_stock_trend
            product.min_quantity_outwards = min_stock_demand
            product.min_quantity_average = min_stock_avg_all
            product.min_quantity_average_three = min_stock_avg_3
            product.total_historical_entries = daily_qs.count()
            product.save(update_fields=[
                'min_quantity_closing',
                'min_quantity_outwards',
                'min_quantity_average',
                'min_quantity_average_three',
                'total_historical_entries'
            ])
            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Updated {updated_count} inventory items with min quantities and total entry counts."
        ))
