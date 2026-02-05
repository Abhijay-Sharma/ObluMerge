from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

from customer_dashboard.models import (
    SalesPerson,
    Customer,
    CustomerFollowUp,
    CustomerRemark,
)

from tally_voucher.models import Voucher


class Command(BaseCommand):
    help = "Send inactive customer report to each salesperson"

    def handle(self, *args, **options):
        today = date.today()
        cutoff_date = today - timedelta(days=90)

        missing_email_salespersons = []

        salespersons = SalesPerson.objects.all().select_related("user")

        for sp in salespersons:
            if not sp.user or not sp.user.email:
                missing_email_salespersons.append(sp.name)
                continue

            inactive_rows = []

            customers = Customer.objects.filter(salesperson=sp)

            for customer in customers:
                # ----------------------------
                # LAST TAX INVOICE DATE
                # ----------------------------
                tax_invoice = (
                    Voucher.objects.filter(
                        party_name__iexact=customer.name,
                        voucher_type__iexact="TAX INVOICE",
                    )
                    .order_by("-date")
                    .first()
                )

                last_invoice_date = tax_invoice.date if tax_invoice else None

                # ACTIVE → skip
                if last_invoice_date and last_invoice_date >= cutoff_date:
                    continue

                # ----------------------------
                # LATEST REMARK
                # ----------------------------
                latest_remark = (
                    customer.remarks.select_related("salesperson")
                    .order_by("-created_at")
                    .first()
                )

                # ----------------------------
                # LATEST FOLLOW-UP
                # ----------------------------
                latest_followup = (
                    CustomerFollowUp.objects.filter(
                        customer=customer,
                        salesperson=sp,
                    )
                    .order_by("-followup_date")
                    .first()
                )

                # FOLLOW-UP STATUS COLOR
                followup_status = "none"
                if latest_followup:
                    if latest_followup.is_completed:
                        followup_status = "completed"  # green
                    elif latest_followup.followup_date < today:
                        followup_status = "overdue"    # red
                    else:
                        followup_status = "upcoming"   # no color

                inactive_rows.append({
                    "customer": customer,
                    "last_invoice_date": last_invoice_date,
                    "inactive_since": last_invoice_date,
                    "remark": latest_remark,
                    "followup": latest_followup,
                    "followup_status": followup_status,
                })

            if not inactive_rows:
                continue  # nothing to mail this salesperson

            # ----------------------------
            # EMAIL
            # ----------------------------
            context = {
                "salesperson": sp,
                "rows": inactive_rows,
                "today": today,
            }

            html_content = render_to_string(
                "customers/inactive_customers_report_email.html",
                context,
            )

            subject = "📉 Inactive Customers – Action Required"

            msg = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[sp.user.email],
                cc=[
                    "swasti.obluhc@gmail.com",
                    "nitin.a@obluhc.com",
                    "raman.obluhc@gmail.com",
                    "akshay.obluhc@gmail.com",
                    "bhavya.obluhc@gmail.com",
                ],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            self.stdout.write(self.style.SUCCESS(
                f"📨 Inactive customer report sent to {sp.name}"
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
