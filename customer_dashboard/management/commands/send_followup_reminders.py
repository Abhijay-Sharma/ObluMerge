from datetime import date

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

from customer_dashboard.models import CustomerFollowUp
from customer_dashboard.models import SalesPerson


class Command(BaseCommand):
    help = "Send daily follow-up reminders to salespersons"

    def handle(self, *args, **options):
        today = date.today()

        missing_email_salespersons = []

        salespersons = SalesPerson.objects.all().select_related("user")

        for sp in salespersons:

            # ----------------------------
            # FOLLOW UPS FOR THIS SALESPERSON
            # ----------------------------
            overdue_followups = CustomerFollowUp.objects.filter(
                salesperson=sp,
                followup_date__lt=today,
                is_completed=False
            ).select_related("customer")

            today_followups = CustomerFollowUp.objects.filter(
                salesperson=sp,
                followup_date=today,
                is_completed=False
            ).select_related("customer")

            if not overdue_followups.exists() and not today_followups.exists():
                continue  # nothing to mail this person

            # ----------------------------
            # EMAIL ID CHECK
            # ----------------------------
            if not sp.user or not sp.user.email:
                missing_email_salespersons.append(sp.name)
                continue

            # ----------------------------
            # EMAIL CONTEXT
            # ----------------------------
            context = {
                "salesperson": sp,
                "overdue_followups": overdue_followups,
                "today_followups": today_followups,
                "today": today,
            }

            html_content = render_to_string(
                "customers/followup_reminder_email.html",
                context
            )

            subject = "🔔 Customer Follow-up Reminder"

            from_email = "crm@oblutools.com"
            to_emails = [sp.user.email]
            cc_emails = ["abhijay.obluhc@gmail.com","swasti.obluhc@gmail.com","nitin.a@obluhc.com","raman.obluhc@gmail.com","akshay@obluhc.com","bhavya.obluhc@gmail.com","vibhuti.obluhc@gmail.com"]


            msg = EmailMultiAlternatives(subject, "", from_email, to_emails,cc=cc_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            self.stdout.write(self.style.SUCCESS(
                f"✅ Follow-up mail sent to {sp.name} ({sp.user.email})"
            ))

        # ----------------------------
        # MISSING EMAIL REPORT
        # ----------------------------
        if missing_email_salespersons:
            self.stdout.write(self.style.WARNING(
                "⚠️ No email found for these salespersons:"
            ))
            for name in missing_email_salespersons:
                self.stdout.write(self.style.WARNING(f"   - {name}"))
