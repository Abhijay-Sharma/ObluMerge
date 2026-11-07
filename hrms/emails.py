from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings


def send_leave_notification_email(application, event_type, reason=""):
    """
    Handles all leave email notifications.
    event_type options:
      - 'APPLIED': Employee applied -> Sent to Manager
      - 'MANAGER_APPROVED': Manager approved -> Sent to HR
      - 'MANAGER_REJECTED': Manager rejected -> Sent to Employee
      - 'HR_APPROVED': HR approved -> Sent to Employee & Manager
      - 'HR_REJECTED': HR rejected -> Sent to Employee & Manager
    """
    emp = application.employee
    manager = emp.reporting_manager
    manager_email = manager.email if manager and manager.email else None
    emp_email = emp.email if emp.email else None

    # HR contact (fallbacks to company email or admin)
    hr_email = emp.company.email if emp.company and emp.company.email else "hr@obluhc.com"

    to_emails = []
    cc_emails = ["swasti.obluhc@emails.com"]  # Always kept in CC
    subject = ""

    if event_type == 'APPLIED':
        if manager_email:
            to_emails.append(manager_email)
        else:
            to_emails.append(hr_email)
        subject = f"📋 Leave Application Submitted: {emp.full_name} ({application.leave_type.code})"

    elif event_type == 'MANAGER_APPROVED':
        to_emails.append(hr_email)
        if manager_email:
            cc_emails.append(manager_email)
        subject = f"✅ Manager Approved - Leave Application: {emp.full_name} ({application.leave_type.code})"

    elif event_type == 'MANAGER_REJECTED':
        if emp_email:
            to_emails.append(emp_email)
        if manager_email:
            cc_emails.append(manager_email)
        subject = f"❌ Leave Application Rejected by Manager: {application.leave_type.name}"

    elif event_type == 'HR_APPROVED':
        if emp_email:
            to_emails.append(emp_email)
        if manager_email:
            cc_emails.append(manager_email)
        subject = f"🎉 Leave Request Approved: {emp.full_name} ({application.leave_type.code})"

    elif event_type == 'HR_REJECTED':
        if emp_email:
            to_emails.append(emp_email)
        if manager_email:
            cc_emails.append(manager_email)
        subject = f"❌ Leave Request Rejected by HR: {application.leave_type.name}"

    if not to_emails:
        return

    email_context = {
        "application": application,
        "employee": emp,
        "manager": manager,
        "event_type": event_type,
        "reason": reason or application.rejection_reason or application.reason,
        "portal_url": "https://oblutools.com/hrms/leave/applications/",
    }

    html_content = render_to_string("hrms/emails/leave_notification_email.html", email_context)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'hrms@oblutools.com')

    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=from_email,
        to=to_emails,
        cc=cc_emails
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send(fail_silently=True)