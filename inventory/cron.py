from django.core.mail import send_mail
from django.conf import settings
from django.db.models import F
from .models import InventoryItem

def check_min_quantity():
    items_below_limit = InventoryItem.objects.filter(
        min_quantity__gt=-1,  # ignore -1 (no limit set)
        min_quantity__isnull=False,
        quantity__lt=F('min_quantity')
    )

    if not items_below_limit.exists():
        return  # nothing to report

    # Create email body
    product_list = "\n".join(
        f"{item.name} — Qty: {item.quantity}, Min: {item.min_quantity}"
        for item in items_below_limit
    )

    send_mail(
        subject="Low Stock Alert",
        message=f"The following products are below the minimum quantity:\n\n{product_list}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=["swasti.obluhc@gmail.com"],  # replace with recipients
        fail_silently=False,
    )
