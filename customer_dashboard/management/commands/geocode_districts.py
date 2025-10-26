import requests
import time
from django.core.management.base import BaseCommand
from django.db import transaction
from customers.models import Customer

class Command(BaseCommand):
    help = "Fetch latitude and longitude for each unique district using OpenStreetMap Nominatim API"

    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

    def handle(self, *args, **options):
        # Get unique state-district pairs where lat/lon are missing
        districts = (
            Customer.objects
            .filter(latitude__isnull=True, longitude__isnull=True)
            .values("state", "district")
            .distinct()
        )

        self.stdout.write(self.style.NOTICE(f"Found {districts.count()} districts to geocode."))

        for d in districts:
            state = d["state"]
            district = d["district"]
            query = f"{district}, {state}, India"

            self.stdout.write(f"🌐 Geocoding {query} ...")

            try:
                lat, lon = self.get_coordinates(query)
                if lat and lon:
                    with transaction.atomic():
                        Customer.objects.filter(state=state, district=district).update(
                            latitude=lat, longitude=lon
                        )
                    self.stdout.write(self.style.SUCCESS(f"✅ {district}, {state} → {lat}, {lon}"))
                else:
                    self.stdout.write(self.style.WARNING(f"⚠️ No results for {district}, {state}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Error geocoding {query}: {e}"))

            # Respect Nominatim’s usage policy (1 request per second)
            time.sleep(1)

        self.stdout.write(self.style.SUCCESS("🎯 Geocoding completed!"))

    def get_coordinates(self, query):
        """Call the Nominatim API to get coordinates for a query string."""
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
        }

        headers = {
            "User-Agent": "DjangoGeocoder/1.0 (contact@example.com)"
        }

        resp = requests.get(self.NOMINATIM_URL, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return None, None

        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return lat, lon
