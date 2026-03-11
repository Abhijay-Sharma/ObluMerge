from datetime import date

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse

from customer_dashboard.models import (
    SalesPerson,
    CustomerVoucherStatus,
    PaymentRemark,
    PaymentExpectedDateHistory,
)


class Command(BaseCommand):
    help = "Send unpaid/partial invoice report to each salesperson"

    def handle(self, *args, **options):

        today = date.today()

        TEST_USERNAMES = {"test1", "raman_sharma", "Nimit_Sharma"}

        salespersons = SalesPerson.objects.select_related("user")

        for sp in salespersons:

            if not sp.user or not sp.user.email:
                continue

            if sp.user.username in TEST_USERNAMES:
                continue

            rows = []

            voucher_statuses = (
                CustomerVoucherStatus.objects
                .filter(customer__salesperson=sp)
                .filter(
                    is_unpaid=True
                ) | CustomerVoucherStatus.objects.filter(
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

                pending_amount = vs.unpaid_amount

                # ---------------------------
                # CREDIT PERIOD
                # ---------------------------

                credit_crossed = vs.is_credit_period_crossed
                days_elapsed = vs.credit_days_elapsed or 0

                credit_period = 0
                if hasattr(vs.customer, "credit_profile"):
                    credit_period = vs.customer.credit_profile.credit_period_days

                if credit_crossed:
                    credit_status = "crossed"
                    credit_display = days_elapsed
                else:
                    credit_status = "remaining"
                    credit_display = max(credit_period - days_elapsed, 0)

                # ---------------------------
                # THREAD
                # ---------------------------

                thread = getattr(vs, "payment_thread", None)

                latest_remark = None
                latest_expected_date = None

                if thread:

                    latest_remark_obj = thread.remarks.order_by("-created_at").first()
                    latest_expected_obj = thread.expected_date_history.first()

                    if latest_remark_obj:
                        latest_remark = latest_remark_obj.remark

                    if latest_expected_obj:
                        latest_expected_date = latest_expected_obj.expected_date

                # ---------------------------
                # LINKS
                # ---------------------------

                voucher_link = reverse(
                    "voucher_detail",
                    args=[vs.voucher.pk]
                )

                thread_link = reverse(
                    "customers:payment_thread_detail",
                    args=[vs.pk]
                )

                rows.append({
                    "customer": vs.customer.name,
                    "invoice_number": vs.voucher.voucher_number,
                    "invoice_date": vs.voucher_date,
                    "voucher_link": voucher_link,
                    "pending_amount": pending_amount,
                    "credit_status": credit_status,
                    "credit_days": credit_display,

                    "ticket_status": thread.ticket_status if thread else None,
                    "ticket_raised_by": thread.raised_by.username if thread and thread.raised_by else None,
                    "ticket_raised_at": thread.raised_at if thread else None,

                    "remark": latest_remark,
                    "expected_date": latest_expected_date,
                    "thread_link": thread_link,
                })

            if not rows:
                continue

            # ---------------------------
            # EMAIL
            # ---------------------------

            context = {
                "salesperson": sp,
                "rows": rows,
                "today": today,
                "domain": "https://oblutools.com"
            }

            html_content = render_to_string(
                "customers/payment_pending_report_email.html",
                context
            )

            subject = "💰 Pending Payments – Action Required"

            msg = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email="crm@oblutools.com",
                # to=[sp.user.email],
                to=["madderladder68@gmail.com","vibhuti.obluhc@gmail.com"],
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
                self.style.SUCCESS(f"📨 Pending payment report sent to {sp.name}")
            )