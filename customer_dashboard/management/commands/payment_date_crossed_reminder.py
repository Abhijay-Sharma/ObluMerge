from datetime import date

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from customer_dashboard.models import (
    SalesPerson,
    CustomerVoucherStatus,
)


class Command(BaseCommand):
    help = "Send expected payment follow-up emails"

    def handle(self, *args, **options):

        today = date.today()

        TEST_USERNAMES = {"test1", "raman_sharma", "Nimit_Sharma"}

        salespersons = SalesPerson.objects.select_related("user")

        for sp in salespersons:

            if not sp.user or not sp.user.email:
                continue

            if sp.user.username in TEST_USERNAMES:
                continue

            overdue_rows = []
            today_rows = []
            no_date_rows = []

            voucher_statuses = (
                CustomerVoucherStatus.objects
                .filter(customer__salesperson=sp)
                .filter(is_unpaid=True) |
                CustomerVoucherStatus.objects.filter(
                    customer__salesperson=sp,
                    is_partially_paid=True
                )
            )

            voucher_statuses = voucher_statuses.select_related(
                "customer",
                "voucher",
                "payment_thread"
            )

            for vs in voucher_statuses:

                thread = getattr(vs, "payment_thread", None)
                expected_date = None
                remark = None

                if thread:
                    latest_expected = thread.expected_date_history.first()
                    latest_remark = thread.remarks.order_by("-created_at").first()

                    if latest_expected:
                        expected_date = latest_expected.expected_date

                    if latest_remark:
                        remark = latest_remark.remark

                row = {
                    "customer": vs.customer.name,
                    "invoice_number": vs.voucher.voucher_number,
                    "invoice_date": vs.voucher_date,
                    "pending_amount": vs.unpaid_amount,
                    "expected_date": expected_date,
                    "remark": remark,
                }

                # ---------------------------
                # CLASSIFICATION
                # ---------------------------

                if expected_date:
                    if expected_date < today:
                        overdue_rows.append(row)
                    elif expected_date == today:
                        today_rows.append(row)
                else:
                    no_date_rows.append(row)

            # If nothing to send → skip
            if not (overdue_rows or today_rows or no_date_rows):
                continue

            # ---------------------------
            # EMAIL CONTEXT
            # ---------------------------

            context = {
                "salesperson": sp,
                "overdue_rows": overdue_rows,
                "today_rows": today_rows,
                "no_date_rows": no_date_rows,
                "today": today,
                "payment_page": "https://oblutools.com/customers/payment-followups/"
            }

            html_content = render_to_string(
                "customers/payment_expected_followup_email.html",
                context
            )

            subject = "📅 Payment Follow-ups – Action Required"

            msg = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email="crm@oblutools.com",
                # to=[sp.user.email],
                to=["abhijay.obluhc@gmail.com"]
                # cc=[
                #     "abhijay.obluhc@gmail.com",
                #     "swasti.obluhc@gmail.com",
                #     "nitin.a@obluhc.com",
                #     "raman.obluhc@gmail.com",
                #     "akshay@obluhc.com",
                #     "bhavya.obluhc@gmail.com",
                #     "vibhuti.obluhc@gmail.com"
                # ],
            )

            msg.attach_alternative(html_content, "text/html")
            msg.send()

            self.stdout.write(
                self.style.SUCCESS(f"📨 Follow-up email sent to {sp.name}")
            )