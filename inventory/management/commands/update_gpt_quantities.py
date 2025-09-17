from django.core.management.base import BaseCommand
from inventory.models import InventoryItem
from openai import OpenAI
import json
import os


class Command(BaseCommand):
    help = "Predict next month's outward demand using GPT for thermoforming sheets"

    def handle(self, *args, **options):
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

        items = InventoryItem.objects.all()

        for item in items:
            # --- Build full month-wise outward history ---
            history = item.get_monthly_outwards_history()
            # Must return: [{"month": "2024-01", "outward_qty": 120}, ...]

            if not history or len(history) < 3:
                self.stdout.write(self.style.WARNING(
                    f"Skipping {item.name} (not enough data)"
                ))
                continue

            data = {
                "item_id": item.id,
                "item_name": item.name,
                "history": history
            }

            prompt = f"""
            You are an inventory forecasting expert.
            Below is the complete monthly outward quantity history for one product.

            {json.dumps(data)}

            Based only on the trend of outward_qty, predict the outward quantity for the next month.
            Output ONLY a plain integer with no explanation or words.
            """

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=10
                )

                result = response.choices[0].message.content.strip()

                predicted = int(''.join(ch for ch in result if ch.isdigit()))
                predicted_with_buffer = int(predicted * 1.1)

                # --- Save prediction ---
                item.min_quantity_gpt = predicted_with_buffer
                item.save(update_fields=["min_quantity_gpt"])

                self.stdout.write(self.style.SUCCESS(
                    f"{item.name}: predicted {predicted} → saved {predicted_with_buffer}"
                ))

            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"Error predicting {item.name}: {e}"
                ))

        self.stdout.write(self.style.SUCCESS("✅ Prediction run complete"))
