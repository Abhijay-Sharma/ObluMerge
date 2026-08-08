from django.core.management.base import BaseCommand
from customer_dashboard.models import Customer

import requests
import time


class Command(BaseCommand):
    help = "Fetch and update district, latitude, and longitude for customers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update even if latitude/longitude already exist."
        )

        parser.add_argument(
            "--customer",
            type=int,
            help="Update only one customer by ID."
        )

    def geocode_location(self, query):
        """
        Geocode using OpenStreetMap Nominatim.
        Returns latitude, longitude, district and state.
        """

        headers = {
            "User-Agent": "ObluTools Customer Geocoder/1.0"
        }

        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
        }

        try:
            for attempt in range(3):
                try:
                    response = requests.get(
                        "https://nominatim.openstreetmap.org/search",
                        headers=headers,
                        params=params,
                        timeout=20,
                    )

                    response.raise_for_status()
                    results = response.json()
                    break

                except requests.RequestException:

                    if attempt == 2:
                        raise

                    time.sleep(2)

            if not results:
                return None

            result = results[0]
            address = result.get("address", {})

            district = (
                    address.get("state_district")
                    or address.get("county")
                    or address.get("district")
            )

            self.stdout.write(f"Address returned: {address}")

            return {
                "latitude": float(result["lat"]),
                "longitude": float(result["lon"]),
                "district": district,
                "state": address.get("state"),
            }

        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f"Geocoding failed: {e}")
            )

        return None

    def handle(self, *args, **options):

        force = options["force"]
        customer_id = options.get("customer")

        customers = Customer.objects.all().order_by("id")

        if customer_id:
            customers = customers.filter(id=customer_id)

        total = customers.count()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nFound {total} customers.\n"
            )
        )

        # We'll replace this later
        updated = 0
        skipped = 0
        failed = 0

        for index, customer in enumerate(customers, start=1):

            self.stdout.write(
                self.style.NOTICE(
                    f"\n[{index}/{total}] ID={customer.id} | {customer.name}"
                )
            )

            # -------------------------------------------------
            # Skip customers already having coordinates
            # -------------------------------------------------

            if (
                    not force
                    and customer.latitude is not None
                    and customer.longitude is not None
            ):
                self.stdout.write("✓ Already has coordinates. Skipping.")
                skipped += 1
                continue

            # -------------------------------------------------
            # Skip if no pincode and no state
            # -------------------------------------------------

            if not customer.pincode and not customer.state:
                self.stdout.write(
                    self.style.WARNING(
                        "✗ No pincode and no state. Skipped."
                    )
                )
                skipped += 1
                continue

            # -------------------------------------------------
            # Build best search query
            # -------------------------------------------------

            queries = []

            if customer.address and customer.state:
                queries.append(f"{customer.address}, {customer.state}")

            if customer.pincode:
                queries.append(customer.pincode.strip())

            if customer.district and customer.state:
                queries.append(f"{customer.district}, {customer.state}")

            if customer.state:
                queries.append(customer.state)

            # Remove duplicates while preserving order
            queries = list(dict.fromkeys(queries))

            # -------------------------------------------------
            # Fetch coordinates
            # -------------------------------------------------

            location = None

            for query in queries:

                self.stdout.write(f"Trying: {query}")

                location = self.geocode_location(query)

                if location:
                    break

                time.sleep(1)

            if not location:
                self.stdout.write(
                    self.style.ERROR("✗ Location not found.")
                )
                failed += 1
                continue

            # -------------------------------------------------
            # Save
            # -------------------------------------------------

            if (
                    not customer.district
                    or customer.district.strip().upper() == "ABC"
            ):
                customer.district = location["district"]

            if (
                    not customer.state
                    or customer.state.strip().upper() == "ABC"
            ):
                customer.state = location["state"]

            customer.latitude = location["latitude"]
            customer.longitude = location["longitude"]

            customer.save(
                update_fields=[
                    "district",
                    "state",
                    "latitude",
                    "longitude",
                ]
            )
            updated += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Saved "
                    f"({customer.latitude}, {customer.longitude})"
                )
            )

            # Respect Nominatim usage policy
            time.sleep(1)

        self.stdout.write("\n" + "=" * 50)

        self.stdout.write(
            self.style.SUCCESS(f"Updated : {updated}")
        )

        self.stdout.write(
            self.style.WARNING(f"Skipped : {skipped}")
        )

        self.stdout.write(
            self.style.ERROR(f"Failed : {failed}")
        )

        self.stdout.write("=" * 50)