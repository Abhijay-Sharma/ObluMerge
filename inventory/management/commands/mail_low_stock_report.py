from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.db import models
from inventory.models import Category, InventoryItem

class Command(BaseCommand):
    help = "Sends full inventory report email with low-stock highlights"

    def handle(self, *args, **options):
        # 🧾 Get all categories with related inventory items
        categories = Category.objects.prefetch_related('inventoryitem_set').all()

        if not any(c.inventoryitem_set.exists() for c in categories):
            self.stdout.write(self.style.WARNING("⚠️ No inventory items found."))
            return

        # 🧮 Identify low stock items
        low_stock_items = InventoryItem.objects.filter(
            quantity__lt=models.F('min_quantity_outwards')
        )

        # 📦 Prepare context for email template
        context = {
            "categories": categories,
            "low_stock_items": low_stock_items,
        }

        # 🧩 Render HTML email content
        html_content = render_to_string('inventory/low_stock_email.html', context)

        # ✉️ Prepare email
        subject = "📊 Daily Inventory Report (Low Stock Alerts Included)"
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None)
        to_emails = ["nitin.a@obluhc.com","swasti.obluhc@gmail.com","accounts@obluhc.com","sanyam.obluhc@gmail.com","sujal.obluhc@gmail.com","raman.obluhc@gmail.com","vibhuti.obluhc@gmail.com"]  # 👈 your target email

        msg = EmailMultiAlternatives(subject, "", from_email, to_emails)
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        self.stdout.write(self.style.SUCCESS("✅ Inventory report email sent to madderladder68@gmail.com"))
