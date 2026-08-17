import pandas as pd
from django.core.management.base import BaseCommand
from django.db.models import Q, Sum, Max
from customer_dashboard.models import Customer
from tally_voucher.models import VoucherStockItem


class Command(BaseCommand):
    help = 'Export UNIQUE customers who bought Erkodur, Zendura, or Molecur'

    def handle(self, *args, **options):
        self.stdout.write("Processing unique customer list...")

        brands = ['erkodur', 'zendura', 'molecur']

        # 1. Build the brand query
        brand_query = Q()
        for brand in brands:
            brand_query |= Q(item__name__icontains=brand) | Q(item_name_text__icontains=brand)

        # 2. Get all qualifying transactions (Tax Invoices + Specific Brands)
        qualifying_items = VoucherStockItem.objects.filter(
            voucher__voucher_type__icontains="Tax Invoice"
        ).filter(brand_query).select_related('voucher', 'item')

        # 3. Get unique party names (customer names from Tally)
        unique_party_names = qualifying_items.values_list('voucher__party_name', flat=True).distinct()

        data_list = []

        for name in unique_party_names:
            # Look up the customer profile in your dashboard app
            customer = Customer.objects.filter(name__iexact=name).first()

            # Filter the items just for this specific customer to get stats
            customer_brand_items = qualifying_items.filter(voucher__party_name=name)

            # Calculate stats
            total_brand_spent = customer_brand_items.aggregate(total=Sum('amount'))['total'] or 0
            last_purchase_date = customer_brand_items.aggregate(last_date=Max('voucher__date'))['last_date']

            # Identify which brands they bought
            # Collect all item names and raw text into one string to check for keywords
            item_names = list(customer_brand_items.values_list('item__name', flat=True))
            raw_texts = list(customer_brand_items.values_list('item_name_text', flat=True))
            combined_search_string = " ".join(filter(None, item_names + raw_texts)).lower()

            brands_found = [b.capitalize() for b in brands if b in combined_search_string]

            data_list.append({
                'Customer Name': name,
                'Phone': customer.phone if customer else 'N/A',
                'Email': customer.email if customer else 'N/A',
                'State': customer.state if customer else 'N/A',
                'District': customer.district if customer else 'N/A',
                'Salesperson': customer.salesperson.name if customer and customer.salesperson else 'Unassigned',
                'Brands Purchased': ", ".join(brands_found),
                'Total Brand Spend': float(total_brand_spent),
                'Last Brand Purchase': last_purchase_date,
            })

        if not data_list:
            self.stdout.write(self.style.WARNING("No records found."))
            return

        # 4. Create DataFrame and Export
        df = pd.DataFrame(data_list)

        # Ensure the date is clean
        if 'Last Brand Purchase' in df.columns:
            df['Last Brand Purchase'] = pd.to_datetime(df['Last Brand Purchase']).dt.date

        filename = "Unique_Brand_Customers.xlsx"
        df.to_excel(filename, index=False)

        self.stdout.write(self.style.SUCCESS(f"Successfully exported {len(data_list)} unique customers to {filename}"))