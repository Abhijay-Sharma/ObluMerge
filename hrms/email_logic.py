"""
Email notification system for Oblu HRMS:
1. Hiring & Recruitment Pipeline:
   - Interview invitations & day-of calendar reminders (Candidate & Interviewer)
   - Offer letters
2. Leave Management Workflow:
   - Employee applies -> Manager receives detailed notification (+ CC Employee)
   - Manager approves -> Employee notified & HR notified for final review
   - Manager rejects -> Employee notified with reason
   - HR approves/rejects -> Employee notified (+ CC Manager)

Uses dynamic system emails from Employee, Reporting Manager, and Company/HR records.
All email transmissions are wrapped safely so SMTP errors never break requests.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import date


def _send_rich_email(subject, text_body, html_body, to_emails, cc_emails=None):
    """Safe multi-part email sender with list deduplication and cleanup."""
    if not to_emails:
        return False, 'No recipient email on file.'
    
    # Filter valid non-empty strings
    clean_to = [e.strip() for e in to_emails if e and e.strip()]
    clean_cc = [e.strip() for e in (cc_emails or []) if e and e.strip() and e.strip() not in clean_to]
    
    if not clean_to:
        return False, 'No recipient email on file.'

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@oblu.com')
    try:
        msg = EmailMultiAlternatives(subject, text_body, from_email, clean_to, cc=clean_cc)
        if html_body:
            msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        return True, None
    except Exception as e:
        return False, str(e)


# ==============================================================================
# 1. LEAVE NOTIFICATION SYSTEM (Employee -> Manager -> HR Workflow)
# ==============================================================================

def get_company_hr_emails(company):
    """Resolves HR emails dynamically for a company."""
    emails = []
    if company and company.email:
        emails.append(company.email.strip())
    
    # Also find active HR employees belonging to this company
    try:
        from . import models as m
        hr_emps = m.Employee.objects.filter(
            company=company, is_hr=True, status=m.Employee.Status.ACTIVE
        ).exclude(email='').values_list('email', flat=True)
        emails.extend([e.strip() for e in hr_emps if e and e.strip()])
    except Exception:
        pass
    
    # Fallback to default admin email if nothing found
    if not emails and getattr(settings, 'DEFAULT_FROM_EMAIL', None):
        emails.append(settings.DEFAULT_FROM_EMAIL)
    return list(set(emails))


def send_leave_notification_email(leave_app, event_type='APPLIED', reason=None):
    """
    Handles end-to-end leave email notifications dynamically:
      - 'APPLIED': Employee applied -> Sent to Reporting Manager (CC: Employee)
      - 'MANAGER_APPROVED': Manager approved -> Sent to Employee & forwarded to HR
      - 'MANAGER_REJECTED': Manager rejected -> Sent to Employee (CC: Manager)
      - 'HR_APPROVED': HR approved -> Sent to Employee (CC: Manager)
      - 'HR_REJECTED' or 'REJECTED': HR rejected -> Sent to Employee (CC: Manager)
    """
    emp = leave_app.employee
    manager = emp.reporting_manager
    company = emp.company
    company_name = company.name if company else 'Oblu HRMS'
    
    emp_email = emp.email.strip() if emp.email else None
    manager_email = manager.email.strip() if manager and manager.email else None
    hr_emails = get_company_hr_emails(company)

    # Calculate leave balance snapshot
    balance_left = None
    try:
        from .leave_logic import get_monthly_available_days
        balance_left = get_monthly_available_days(emp, leave_app.leave_type, leave_app.start_date)
    except Exception:
        pass

    results = []

    # ----------------- CASE 1: EMPLOYEE APPLIED -----------------
    if event_type == 'APPLIED':
        subject = f"📋 Leave Request: {emp.full_name} ({leave_app.leave_type.name}) — {leave_app.total_days} Day(s)"
        to_recipients = [manager_email] if manager_email else hr_emails
        cc_recipients = [emp_email] if emp_email else []

        greeting = (
            f"Dear {manager.full_name if manager else 'HR Team'},"
            f"<br>{emp.full_name} has submitted a new leave application and requires your review."
        )
        instruction = (
            "Please review this request in your HRMS Manager Portal to approve or reject."
        )
        context = {
            'title': 'New Leave Application Submitted',
            'company_name': company_name,
            'badge_text': 'Pending Manager Review',
            'badge_class': 'status-applied',
            'greeting_message': greeting,
            'employee': emp,
            'application': leave_app,
            'reporting_manager': manager,
            'balance_left': balance_left,
            'next_step_instruction': instruction,
        }
        text_body = (
            f"Dear {manager.full_name if manager else 'HR Team'},\n\n"
            f"{emp.full_name} ({emp.employee_code}) has applied for leave.\n"
            f"Leave Type: {leave_app.leave_type.name} ({leave_app.total_days} Days, {leave_app.get_day_type_display()})\n"
            f"Dates: {leave_app.start_date:%d %b %Y} to {leave_app.end_date:%d %b %Y}\n"
            f"Reason: {leave_app.reason or 'Not specified'}\n\n"
            f"Please log in to your HRMS portal to take action."
        )
        try:
            html_body = render_to_string('hrms/email/leave_notification.html', context)
        except Exception:
            html_body = None

        ok, err = _send_rich_email(subject, text_body, html_body, to_recipients, cc_recipients)
        results.append(('applied_notice', to_recipients, ok, err))
        return ok, err

    # ----------------- CASE 2: MANAGER APPROVED -----------------
    elif event_type == 'MANAGER_APPROVED':
        # 1. Notify Employee that Manager Approved & escalated to HR
        if emp_email:
            subject_emp = f"✅ Manager Approved: Leave Request ({leave_app.leave_type.name}) Forwarded to HR"
            greeting_emp = (
                f"Dear {emp.full_name},<br>"
                f"Your reporting manager ({manager.full_name if manager else 'Manager'}) has approved your leave application. "
                f"It has now been forwarded to the HR department for final sign-off."
            )
            ctx_emp = {
                'title': 'Leave Request Approved by Manager',
                'company_name': company_name,
                'badge_text': 'Pending HR Sign-Off',
                'badge_class': 'status-mgr-appr',
                'greeting_message': greeting_emp,
                'employee': emp,
                'application': leave_app,
                'reporting_manager': manager,
                'balance_left': balance_left,
                'next_step_instruction': "You will receive another update once HR gives final sign-off.",
            }
            try:
                html_emp = render_to_string('hrms/email/leave_notification.html', ctx_emp)
            except Exception:
                html_emp = None
            text_emp = f"Hi {emp.full_name},\nYour manager has approved your leave ({leave_app.start_date} to {leave_app.end_date}). It has been forwarded to HR for final review."
            _send_rich_email(subject_emp, text_emp, html_emp, [emp_email])

        # 2. Notify HR that Manager approved and HR review is required
        subject_hr = f"🔔 Action Required: Leave Approved by Manager — {emp.full_name} ({leave_app.leave_type.name})"
        greeting_hr = (
            f"Dear HR Team,<br>"
            f"Reporting Manager <strong>{manager.full_name if manager else 'Manager'}</strong> has approved the leave application for "
            f"<strong>{emp.full_name}</strong>. Please review and grant final approval."
        )
        ctx_hr = {
            'title': 'Manager Approved — Final HR Review Required',
            'company_name': company_name,
            'badge_text': 'Pending HR Final Approval',
            'badge_class': 'status-mgr-appr',
            'greeting_message': greeting_hr,
            'employee': emp,
            'application': leave_app,
            'reporting_manager': manager,
            'balance_left': balance_left,
            'next_step_instruction': "Please log in to the HR Portal to give final approval or rejection.",
        }
        try:
            html_hr = render_to_string('hrms/email/leave_notification.html', ctx_hr)
        except Exception:
            html_hr = None
        text_hr = (
            f"Dear HR Team,\n"
            f"Manager has approved leave for {emp.full_name} ({leave_app.start_date} to {leave_app.end_date}).\n"
            f"Please review and finalize in the HR portal."
        )
        cc_list = [manager_email] if manager_email else []
        ok, err = _send_rich_email(subject_hr, text_hr, html_hr, hr_emails, cc_list)
        return ok, err

    # ----------------- CASE 3: MANAGER REJECTED -----------------
    elif event_type == 'MANAGER_REJECTED':
        if not emp_email:
            return False, 'No employee email on file'
        subject = f"❌ Leave Application Rejected by Manager: {leave_app.leave_type.name}"
        greeting = (
            f"Dear {emp.full_name},<br>"
            f"Your reporting manager ({manager.full_name if manager else 'Manager'}) has reviewed and declined your leave request."
        )
        ctx = {
            'title': 'Leave Request Rejected by Manager',
            'company_name': company_name,
            'badge_text': 'Rejected by Manager',
            'badge_class': 'status-mgr-rej',
            'greeting_message': greeting,
            'employee': emp,
            'application': leave_app,
            'reporting_manager': manager,
            'action_reason': reason or 'No specific reason provided.',
            'next_step_instruction': "Please contact your reporting manager if you have any questions.",
        }
        try:
            html = render_to_string('hrms/email/leave_notification.html', ctx)
        except Exception:
            html = None
        text = f"Dear {emp.full_name},\nYour leave application for {leave_app.start_date} to {leave_app.end_date} has been rejected by your manager.\nReason: {reason or 'Not specified'}"
        cc_list = [manager_email] if manager_email else []
        return _send_rich_email(subject, text, html, [emp_email], cc_list)

    # ----------------- CASE 4: HR APPROVED -----------------
    elif event_type == 'HR_APPROVED':
        if not emp_email:
            return False, 'No employee email on file'
        subject = f"🎉 Leave Application Approved: {leave_app.leave_type.name} ({leave_app.start_date:%d %b %Y})"
        greeting = (
            f"Dear {emp.full_name},<br>"
            f"Your leave application has received final approval from the HR department."
        )
        ctx = {
            'title': 'Leave Application Officially Approved',
            'company_name': company_name,
            'badge_text': 'Officially Approved',
            'badge_class': 'status-hr-appr',
            'greeting_message': greeting,
            'employee': emp,
            'application': leave_app,
            'reporting_manager': manager,
            'balance_left': balance_left,
            'next_step_instruction': "Your leave balance and attendance calendar have been updated accordingly.",
        }
        try:
            html = render_to_string('hrms/email/leave_notification.html', ctx)
        except Exception:
            html = None
        text = f"Dear {emp.full_name},\nYour leave application for {leave_app.start_date} to {leave_app.end_date} has been officially approved by HR."
        cc_list = [manager_email] if manager_email else []
        return _send_rich_email(subject, text, html, [emp_email], cc_list)

    # ----------------- CASE 5: HR REJECTED -----------------
    elif event_type in ['HR_REJECTED', 'REJECTED']:
        if not emp_email:
            return False, 'No employee email on file'
        subject = f"❌ Leave Application Rejected by HR: {leave_app.leave_type.name}"
        greeting = (
            f"Dear {emp.full_name},<br>"
            f"Your leave application has been reviewed and rejected by the HR department."
        )
        ctx = {
            'title': 'Leave Application Rejected by HR',
            'company_name': company_name,
            'badge_text': 'Rejected by HR',
            'badge_class': 'status-hr-rej',
            'greeting_message': greeting,
            'employee': emp,
            'application': leave_app,
            'reporting_manager': manager,
            'action_reason': reason or 'No specific reason provided.',
            'next_step_instruction': "Please contact the HR department for further details.",
        }
        try:
            html = render_to_string('hrms/email/leave_notification.html', ctx)
        except Exception:
            html = None
        text = f"Dear {emp.full_name},\nYour leave request for {leave_app.start_date} to {leave_app.end_date} was rejected by HR.\nReason: {reason or 'Not specified'}"
        cc_list = [manager_email] if manager_email else []
        return _send_rich_email(subject, text, html, [emp_email], cc_list)

    return False, 'Unknown event type'


# ==============================================================================
# 2. INTERVIEW SCHEDULING SYSTEM (Google Calendar Style Notifications)
# ==============================================================================

def send_interview_emails(interview):
    """
    Sends Google Calendar style invitation emails to Candidate and Interviewer.
    Includes rich date/time card, round, position, and instructions.
    """
    application = interview.application
    candidate = application.candidate
    job = application.job_posting
    company = job.company
    company_name = company.name if company else 'Oblu Healthcare'
    interviewer = interview.interviewer
    results = []

    when_str = timezone.localtime(interview.scheduled_on).strftime('%d %b %Y at %I:%M %p')
    meeting_link = getattr(interview, 'meeting_link', None) or getattr(interview, 'location', '')

    # 1. Candidate Invitation
    if candidate.email:
        subject_c = f"📅 Interview Invitation: {interview.interview_round} — {job.title} at {company_name}"
        ctx_c = {
            'event_title': f"Interview Invitation: {interview.interview_round}",
            'company_name': company_name,
            'recipient_name': f"{candidate.first_name} {candidate.last_name}",
            'is_interviewer': False,
            'interview': interview,
            'candidate': candidate,
            'job_title': job.title,
            'interviewer': interviewer,
            'meeting_link': meeting_link,
        }
        try:
            html_c = render_to_string('hrms/email/interview_invite.html', ctx_c)
        except Exception:
            html_c = None
        text_c = (
            f"Dear {candidate.first_name},\n\n"
            f"You are invited to an interview for {job.title} at {company_name}.\n"
            f"Round: {interview.interview_round}\n"
            f"Date & Time: {when_str}\n"
            f"Mode: {interview.get_mode_display()}\n\n"
            f"Best regards,\n{company_name} Hiring Team"
        )
        ok, err = _send_rich_email(subject_c, text_c, html_c, [candidate.email])
        results.append(('candidate', candidate.email, ok, err))

    # 2. Interviewer Notice
    if interviewer and interviewer.email:
        subject_i = f"📅 Interview Scheduled: {candidate.first_name} {candidate.last_name} ({job.title})"
        ctx_i = {
            'event_title': f"Interview Assignment: {interview.interview_round}",
            'company_name': company_name,
            'recipient_name': interviewer.first_name,
            'is_interviewer': True,
            'interview': interview,
            'candidate': candidate,
            'job_title': job.title,
            'interviewer': interviewer,
            'meeting_link': meeting_link,
        }
        try:
            html_i = render_to_string('hrms/email/interview_invite.html', ctx_i)
        except Exception:
            html_i = None
        text_i = (
            f"Hi {interviewer.first_name},\n\n"
            f"You have an interview scheduled with candidate {candidate.first_name} {candidate.last_name} for the {job.title} position.\n"
            f"Round: {interview.interview_round}\n"
            f"Date & Time: {when_str}\n"
            f"Mode: {interview.get_mode_display()}\n"
            f"Candidate Contact: {candidate.email} / {candidate.phone or '—'}\n\n"
            f"Thanks,\nHR Team"
        )
        ok, err = _send_rich_email(subject_i, text_i, html_i, [interviewer.email])
        results.append(('interviewer', interviewer.email, ok, err))

    return results


def send_interview_day_reminder(target_date=None):
    """
    Day-of morning reminder for all interviews scheduled on target_date (defaults to today).
    Notifies interviewer and candidate: 'Today at [time] your interview is scheduled'.
    """
    from . import models as m
    chk_date = target_date or timezone.localdate()
    interviews = m.Interview.objects.filter(
        scheduled_on__date=chk_date,
        status=m.Interview.Status.SCHEDULED
    ).select_related('application__candidate', 'application__job_posting__company', 'interviewer')

    count = 0
    for iv in interviews:
        send_interview_emails(iv)
        count += 1
    return count


def send_offer_emails(offer):
    """Offer letter dual email dispatch."""
    application = offer.application
    candidate = application.candidate
    company = application.job_posting.company
    results = []

    subject_c = f'Offer Letter — {application.job_posting.title} at {company.name}'
    body_c = (
        f'Dear {candidate.first_name},\n\n'
        f'Congratulations! We are pleased to offer you the position of '
        f'{application.job_posting.title} at {company.name}.\n\n'
        f'  CTC Offered:           {offer.ctc_offered}\n'
        f'  Proposed Joining Date: {offer.joining_date or "To be confirmed"}\n'
        + (f'  Offer Valid Until:     {offer.expiry_date}\n' if offer.expiry_date else '')
        + f'\nPlease reply to this email to confirm your acceptance.\n\n'
        f'Best regards,\n{company.name} Hiring Team'
    )
    ok, err = _send_rich_email(subject_c, body_c, None, [candidate.email])
    results.append(('candidate', candidate.email, ok, err))

    last_interview = application.interviews.order_by('-scheduled_on').first()
    manager = last_interview.interviewer if last_interview else None
    if manager and manager.email:
        subject_m = f'Offer Sent — {candidate} ({application.job_posting.title})'
        body_m = (
            f'Hi {manager.first_name},\n\n'
            f'An offer letter has been sent to {candidate} for the {application.job_posting.title} role '
            f'(CTC: {offer.ctc_offered}).\n\nThanks,\nHR Team'
        )
        ok, err = _send_rich_email(subject_m, body_m, None, [manager.email])
        results.append(('manager', manager.email, ok, err))

    return results
