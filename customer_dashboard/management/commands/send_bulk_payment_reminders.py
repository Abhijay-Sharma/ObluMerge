from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from datetime import date, timedelta
from customer_dashboard.models import CustomerVoucherStatus


class Command(BaseCommand):
    help = 'Send a single grouped internal payment alert email per Salesperson'

    def handle(self, *args, **options):
        today = date.today()
        milestones = [-14, -7, -2, 0, 3, 7, 14]

        # 1. Fetch all unpaid vouchers
        qs = CustomerVoucherStatus.objects.filter(
            is_unpaid=True
        ).select_related(
            'customer',
            'voucher',
            'customer__salesperson__user',
            'customer__salesperson__manager__user'
        )

        # 2. Group relevant invoices by Salesperson
        # Structure: { sp_object: [vs1, vs2, vs3...] }
        alerts_by_sp = {}

        for vs in qs:
            credit_days = vs.customer.credit_profile.credit_period_days if hasattr(vs.customer,
                                                                                   'credit_profile') and vs.customer.credit_profile else 0
            due_date = vs.voucher_date + timedelta(days=credit_days)
            diff = (today - due_date).days

            if diff in milestones:
                sp = vs.customer.salesperson
                if sp not in alerts_by_sp:
                    alerts_by_sp[sp] = []

                # Attach the diff/status to the object temporarily for the table
                if diff < 0:
                    vs.temp_status = f"Due in {abs(diff)} days"
                elif diff == 0:
                    vs.temp_status = "DUE TODAY"
                else:
                    vs.temp_status = f"OVERDUE ({diff} days)"

                alerts_by_sp[sp].append(vs)

        # 3. Send one email per Salesperson
        if not alerts_by_sp:
            self.stdout.write(self.style.WARNING('No milestones hit today. No emails sent.'))
            return

        for sp, vouchers in alerts_by_sp.items():
            self.send_grouped_email(sp, vouchers)

    def send_grouped_email(self, sp, vouchers):
        if not sp.user or not sp.user.email:
            return

        # Recipients
        to_email = [sp.user.email]
        cc_emails = ["nitin.a@obluhc.com","abhijay.obluhc@gmail.com","bhavya@obluhc.com","vibhuti@obluhc.com"]
        if sp.manager and sp.manager.user and sp.manager.user.email:
            cc_emails.append(sp.manager.user.email)

        # Build HTML Table Rows
        table_rows = ""
        for vs in vouchers:
            table_rows += f"""
            <tr>
                <td style="border: 1px solid #ddd; padding: 8px;">{vs.customer.name}</td>
                <td style="border: 1px solid #ddd; padding: 8px;">{vs.voucher.voucher_number}</td>
                <td style="border: 1px solid #ddd; padding: 8px;">{vs.voucher_date.strftime('%d-%m-%Y')}</td>
                <td style="border: 1px solid #ddd; padding: 8px; text-align: right;">₹{vs.unpaid_amount}</td>
                <td style="border: 1px solid #ddd; padding: 8px; font-weight: bold; color: {'#d93025' if 'OVERDUE' in vs.temp_status or 'TODAY' in vs.temp_status else '#1a73e8'};">
                    {vs.temp_status}
                </td>
            </tr>
            """

        # Dashboard Link (Update the domain to your actual live URL)
        dashboard_url = "http://127.0.0.1:8000/customers/payment-followups/"

        # HTML Body
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <p>Hi {sp.name},</p>
            <p>This is an automated summary of pending payments requiring your immediate follow-up:</p>

            <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
                <thead>
                    <tr style="background-color: #f8f9fa;">
                        <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Customer</th>
                        <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Voucher</th>
                        <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Date</th>
                        <th style="border: 1px solid #ddd; padding: 12px; text-align: right;">Amount</th>
                        <th style="border: 1px solid #ddd; padding: 12px; text-align: left;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>

            <p style="margin-top: 30px;">
                <strong>Action Required:</strong> Please contact these customers and update your follow-up remarks here: <br>
                <a href="{dashboard_url}" style="background-color: #1a73e8; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block; margin-top: 10px;">
                    Open Follow-Up Dashboard
                </a>
            </p>

            <hr style="border: 0; border-top: 1px solid #eee; margin-top: 40px;">
            <p style="font-size: 11px; color: #888;">This is a system-generated alert from OBLU Accounts.</p>
        </body>
        </html>
        """

        # Text Version for fallback
        text_content = f"Hi {sp.name}, you have {len(vouchers)} payments requiring follow-up. Please check the dashboard."

        subject = f"PAYMENT ALERT: {len(vouchers)} Pending Follow-ups for {sp.name}"

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email="OBLU Payment Alerts <payments@oblutools.com>",
            to=to_email,
            cc=cc_emails,
        )
        email.attach_alternative(html_content, "text/html")

        try:
            email.send()
            self.stdout.write(self.style.SUCCESS(f"Sent summary email to {sp.name} ({len(vouchers)} invoices)"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error sending to {sp.name}: {str(e)}"))