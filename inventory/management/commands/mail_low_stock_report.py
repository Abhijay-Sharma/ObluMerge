from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.db import models
from inventory.models import Category, InventoryItem
from django.db.models import Prefetch

class Command(BaseCommand):
    help = "Sends full inventory report email with low-stock highlights"

    def handle(self, *args, **options):
        EXCLUDED_PRODUCT_NAMES = [
    "Mini 8K NFEP",
    "Phrozen ACF Release Film- mega 8k (pack of 3)",
    "Magnetic Aligner Box With Silicon Base with Mirror",
    "3Shape Ortho Suite EDU - Single Seat",
    "Ackuretta Curo Model Element Beige 1 Kg",
    "Ackuretta Curie Plus",
    "Ackuretta Curie",
    "Ackuretta Curo ProDenture Light Pink 500gm",
    "Ackuretta Curo ProSplint 500gram",
    "Dentiq Lcd Screen",
    "Ackuretta SOL - Vat Assembly with Vat BoxVat Assembly with Vat Box",
    "Ackuretta Curie Plus Top Cover",
    "SOL BUILD PLATFORM LARGE",
    "Ackuretta Dentiq",
    "Erkodent,Erkodur, 1.50 mm, Ø 125 mm, clear",
    "Erkodent,Erkodur, 4.00 mm, Ø 120 mm, clear",
    "Erkodent,Erkoflex, 1.50 mm, Ø 120 mm, transparent",
    "Erkodent,Erkoflex, 3.00 mm, Ø 120 mm, transparent",
    "Erkodent,Erkoflex, 4.00 mm, Ø 120 mm, Night Blue",
    "Erkodent,Erkoflex-bleach, 1.00 mm, Ø 125 mm, transparent",
    "Erkodent, Erkogum Blocking Out Material",
    "Erkoloc Pro 1.0 x 120 (1)",
    "Erkodent,Erkoloc-pro, 2.00mm, Ø 125 Ø 125 mm, transparent",
    "Erkodent,Erkoloc-pro, 2.00mm, Ø 125 Ø 125 mm, Blue",
    "Erkodent,Erkoloc-pro, 2.00mm, Ø 125 Ø 125 mm, Green",
    "Erkodent,Erkoflex 4.00 mm, Ø 120 mm, Pure White",
    "Anycubic m7 max ACF sheet pack of 5",
    "Zendura FLX 0.76 x 125 (Pack of 01)",
    "Erkoflex-95, 4.00 mm, Ø 125 mm, transparent",
    "Erkodent,Erkodur, 0.80 mm, Ø 240 mm, clear",
    "Erkodent,Erkodur al, 0.80 mm, Ø 240 mm, transparent",
    "Phrozen Standard TR250 LV Deep Gray Resin",
    "Bambu Lab P1S Combo (With AMS) 3D Printer",
    "Bambu Lab H2D 3D Printer (Without AMS)",
    "Finishing Set Quick 3",
    "Finishing Set",
    "Uniformation Wash 3 Ultra",
    "Phrozen cure v 2 with ultrasonic cleaner",
    "Dtech 3D Accuprint C&B Resin A1 Shade 500G",
    "Dtech Model Standard",
    "Dtech 3D Accuprint C&B Resin B1 Shade 500G",
    "D-Tech Custom Tray Resin 500 Gm",
    "D-tech Standard Model Resin Tray",
    "Elegoo Saturn 2 8K",
    "Elegoo Saturn 4k",
    "Elegoo Saturn Ultra 3",
    "ELEGOO NEPTUNE 4 MAX",
    "ELEGOO Neptune 4 plus",
    "Elegoo Saturn 4 12k",
    "Elegoo Saturn 3 ultra",
    "elegoo lcd for saturn 3 ultra 12k",
    "Elegoo Saturn 1 PFA Flim (5 Pcs)",
    "Elegoo PLA+ Filament  1.75mm Translucent",
    "ELEGOO MATTE PLA Filament 1.75mm Sakura Pink",
    "ELEGOO Silk PLA Filament 1.75mm Red",
    "Mercury Plus V3.0 wash and cure station",
    "Envisiontec Vida Psa",
    "Spare Part- Steel Spring",
    "Foil Securing Ring Erkopress",
    "LCD -DISPLAY 3D MOTION",
    "Formlabs, Form 4B Basic Package",
    "Formlabs, Form 4B Complete Package (With OMM )",
    "Formlabs Open Material Mode(OMM) License Upgradation",
    "Formlabs Grey V5 5 ltr can (For Form 4)",
    "Resin Pump (Form 4) Maintenance Kit",
    "Phrozen Sonic Mega 8k V2",
    "Uniformation GK3 Ultra- 16K resolution",
    "Pacdent Rodin Titan C&B [600g] A1 Shade",
    "Pacdent Rodin Sculpture 2.0 C&B 600g A1 shade",
    "Sunlu ABS-Like Resin GRAY",
    "Sunlu standard black resin",
    "Draft Resin (Cartridgev2)RS-F2-DRDR-02",
    "Draft Resin (Form 3) RS-RPS -DRGR-02 5 Ltr",
    "Fast Model Resin 5Ltr",
    "Fast Model Resin 1 Ltr",
    "compressor 1 hp",
    "Resin Tank of Formlab 3 RT- F3-02-01",
    "Resin Pump (Form 4)",
    "Kevin Peter smile 2 scanner",
    "Jacket",
    "Jacket (L)",
    "Jacket (M)",
    "Jacket (S)",
    "Jacket (XL)",
    "Jacket (XXL)",
    "ELEGOO RAPID PLA+ Filament 1.75mm Gray",
    "Slimline 2 Door",
    "TALLY SUBSCRIPTION",
    "Exocad Core",
    "Exocad Unlimited Bundle",
    "O-Ring",
    "ANYCUBIC 3D PRINTER PHOTON MONO 4",
    "Jamghe Eco Resin Grey",
    "Jamghe Eco Stand Resin Almond",
    "Jamghe Eco Resin Skin",
    "jamghe resin beige",
    "Pacdent N2 Free Palette Naturalization Kit"
]

        # 🧾 Get all categories with related inventory items
        categories = Category.objects.prefetch_related(
            Prefetch(
                'inventoryitem_set',
                queryset=InventoryItem.objects.exclude(
                    name__in=EXCLUDED_PRODUCT_NAMES
                )
            )
        ).all()

        if not any(c.inventoryitem_set.exists() for c in categories):
            self.stdout.write(self.style.WARNING("⚠️ No inventory items found."))
            return

        # 🧮 Identify low stock items
        low_stock_items = InventoryItem.objects.exclude(
            name__in=EXCLUDED_PRODUCT_NAMES
        ).filter(
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
        # to_emails = ["nitin.a@obluhc.com","swasti.obluhc@gmail.com","accounts@obluhc.com","sanyam.obluhc@gmail.com","sujal.obluhc@gmail.com","raman.obluhc@gmail.com","vibhuti.obluhc@gmail.com"]  # 👈 your target email
        to_emails=["madderladder68@gmail.com","swasti.obluhc@gmail.com","sanyam.obluhc@gmail.com"]
        msg = EmailMultiAlternatives(subject, "", from_email, to_emails)
        msg.attach_alternative(html_content, "text/html")
        msg.send()

        self.stdout.write(self.style.SUCCESS("✅ Inventory report email sent to madderladder68@gmail.com"))
