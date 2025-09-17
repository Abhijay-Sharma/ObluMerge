from django.core.management.base import BaseCommand
from inventory.models import InventoryItem
import pprint

class Command(BaseCommand):
    help = "Generate min qty report grouped by category"

    def handle(self, *args, **kwargs):
        data = {}

        # Fetch all items, order by category for cleaner grouping
        items = InventoryItem.objects.select_related('category').order_by('category__name', 'name')

        for item in items:
            category_name = item.category.name if item.category else "Uncategorized"
            if category_name not in data:
                data[category_name] = []

            data[category_name].append({
                "id": item.id,
                "name": item.name,
                "min_closing": item.min_quantity_closing,
                "min_outwards": item.min_quantity_outwards,
                "min_average": item.min_quantity_average,
                "min_average_three": item.min_quantity_average_three,
                "entries": item.total_historical_entries
            })

        # Print the dictionary so you can capture it
        pprint.pprint(data)
