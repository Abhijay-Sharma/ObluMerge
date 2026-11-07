"""
Hiring pipeline business logic — same pattern as attendance_logic.py /
leave_logic.py / payroll_logic.py, kept out of views.py.

FLOW
  Application (APPLIED) -> SHORTLISTED -> INTERVIEWING -> OFFERED -> HIRED
                                                        └-> REJECTED

AUDIT TRAIL
  Every mutating action here writes a RecruitmentAuditLog row: from/to status,
  a human-readable action label, WHO did it (the logged-in User), and an
  optional note. This is unconditional — there's no path to change an
  application's status that skips logging, since every view-facing action in
  this module funnels through log_action().

LOCKING ("Confirmed" outcome protection)
  There's no separate 'confirmed' status in the Application model — HIRED and
  REJECTED are already the two terminal/confirmed outcomes of the pipeline, so
  reaching either one automatically locks the record (is_locked=True,
  locked_by, locked_at). Once locked, ensure_unlocked() blocks every further
  mutation UNLESS the acting user is_superuser — matching "only is_superuser
  can modify a locked record". A superuser can also explicitly unlock() one.

EMAILS
  schedule_interview() and send_offer() both call into email_logic to fire the
  dual notifications (candidate + interviewer/manager) asked for. Email
  failures are captured and returned, not raised — a bad SMTP config shouldn't
  block the HR action that triggered it.
"""
from django.utils import timezone

from . import models as m
from . import email_logic


class HiringError(Exception):
    """Raised for invalid pipeline transitions or locked-record violations."""


CLOSED_STATUSES = (m.Application.Status.HIRED, m.Application.Status.REJECTED)


# ---------------------------------------------------------------------------
# Audit trail + locking
# ---------------------------------------------------------------------------
def log_action(application, user, action, to_status=None, note=''):
    m.RecruitmentAuditLog.objects.create(
        application=application,
        from_status=application.status if to_status is None else '',
        to_status=to_status or application.status,
        action=action,
        performed_by=user if (user and user.is_authenticated) else None,
        note=note,
    )


def ensure_unlocked(application, user):
    if application.is_locked and not (user and user.is_authenticated and user.is_superuser):
        raise HiringError(
            'This application is locked (a confirmed Hired/Rejected outcome) — '
            'only a Super Admin can modify it further.'
        )


def _lock(application, user):
    application.is_locked = True
    application.locked_by = user if (user and user.is_authenticated) else None
    application.locked_at = timezone.now()
    application.save(update_fields=['is_locked', 'locked_by', 'locked_at', 'updated_at'])
    log_action(application, user, 'Application locked — confirmed outcome reached', to_status=application.status)


def unlock_application(application, user):
    if not (user and user.is_authenticated and user.is_superuser):
        raise HiringError('Only a Super Admin can unlock a confirmed application.')
    application.is_locked = False
    application.locked_by = None
    application.locked_at = None
    application.save(update_fields=['is_locked', 'locked_by', 'locked_at', 'updated_at'])
    log_action(application, user, 'Application unlocked by Super Admin', to_status=application.status)
    return application


def _set_status(application, user, new_status, action_label, note=''):
    ensure_unlocked(application, user)
    old_status = application.status
    application.status = new_status
    application.save(update_fields=['status', 'updated_at'])
    m.RecruitmentAuditLog.objects.create(
        application=application, from_status=old_status, to_status=new_status,
        action=action_label, performed_by=user if (user and user.is_authenticated) else None, note=note,
    )
    if new_status in CLOSED_STATUSES:
        _lock(application, user)
    return application


# ---------------------------------------------------------------------------
# Pipeline actions
# ---------------------------------------------------------------------------
def shortlist_application(application, user):
    if application.status != m.Application.Status.APPLIED:
        raise HiringError('Only a freshly-applied candidate can be shortlisted.')
    return _set_status(application, user, m.Application.Status.SHORTLISTED, 'Shortlisted')


def reject_application(application, user, reason=''):
    if application.status in CLOSED_STATUSES:
        raise HiringError(f'This application is already {application.get_status_display()}.')
    return _set_status(application, user, m.Application.Status.REJECTED, 'Rejected', note=reason)


def schedule_interview(application, user, interview_round, scheduled_on, interviewer=None,
                        mode=m.Interview.Mode.ONLINE):
    """Creates the Interview, advances the pipeline to INTERVIEWING, logs it,
    and fires the dual candidate/interviewer emails notification."""
    ensure_unlocked(application, user)

    interview = m.Interview.objects.create(
        application=application, interview_round=interview_round, scheduled_on=scheduled_on,
        interviewer=interviewer, mode=mode, status=m.Interview.Status.SCHEDULED,
    )
    if application.status in (m.Application.Status.APPLIED, m.Application.Status.SHORTLISTED):
        application.status = m.Application.Status.INTERVIEWING
        application.save(update_fields=['status', 'updated_at'])

    log_action(
        application, user,
        f'Interview scheduled ({interview_round}, {scheduled_on:%d %b %Y %H:%M})',
        to_status=application.status,
    )
    email_results = email_logic.send_interview_emails(interview)
    return interview, email_results


def move_to_offer(application, user, offer_date, ctc_offered, joining_date=None, expiry_date=None):
    """THE flow the prompt asked for: move a Candidate/Application to OfferLetter status."""
    ensure_unlocked(application, user)
    if application.status in CLOSED_STATUSES:
        raise HiringError(f'This application is already {application.get_status_display()} — cannot make a new offer.')
    if hasattr(application, 'offer_letter'):
        raise HiringError('An offer letter already exists for this application.')

    offer = m.OfferLetter.objects.create(
        application=application, offer_date=offer_date, ctc_offered=ctc_offered,
        joining_date=joining_date, expiry_date=expiry_date, status=m.OfferLetter.Status.DRAFT,
    )
    application.status = m.Application.Status.OFFERED
    application.save(update_fields=['status', 'updated_at'])
    log_action(application, user, f'Offer letter drafted (CTC {ctc_offered})', to_status=application.status)
    return offer


def send_offer(offer, user):
    """Marks the offer SENT and fires the dual candidate/manager emails."""
    ensure_unlocked(offer.application, user)
    if offer.status != m.OfferLetter.Status.DRAFT:
        raise HiringError('Only a draft offer can be sent.')
    offer.status = m.OfferLetter.Status.SENT
    offer.save(update_fields=['status', 'updated_at'])
    log_action(offer.application, user, 'Offer letter sent to candidate')
    email_results = email_logic.send_offer_emails(offer)
    return offer, email_results


def accept_offer(offer, user):
    ensure_unlocked(offer.application, user)
    if offer.status not in (m.OfferLetter.Status.DRAFT, m.OfferLetter.Status.SENT):
        raise HiringError('Only a draft/sent offer can be accepted.')
    offer.status = m.OfferLetter.Status.ACCEPTED
    offer.save(update_fields=['status', 'updated_at'])
    return _finish_offer(offer, user, m.Application.Status.HIRED, 'Offer accepted — Hired')


def decline_offer(offer, user):
    ensure_unlocked(offer.application, user)
    if offer.status not in (m.OfferLetter.Status.DRAFT, m.OfferLetter.Status.SENT):
        raise HiringError('Only a draft/sent offer can be declined.')
    offer.status = m.OfferLetter.Status.DECLINED
    offer.save(update_fields=['status', 'updated_at'])
    return _finish_offer(offer, user, m.Application.Status.REJECTED, 'Offer declined — Rejected')


def expire_offer(offer, user):
    ensure_unlocked(offer.application, user)
    if offer.status not in (m.OfferLetter.Status.DRAFT, m.OfferLetter.Status.SENT):
        raise HiringError('Only a draft/sent offer can expire.')
    offer.status = m.OfferLetter.Status.EXPIRED
    offer.save(update_fields=['status', 'updated_at'])
    log_action(offer.application, user, 'Offer expired')
    return offer


def _finish_offer(offer, user, application_status, action_label):
    application = offer.application
    application.status = application_status
    application.save(update_fields=['status', 'updated_at'])
    log_action(application, user, action_label, to_status=application_status)
    if application_status in CLOSED_STATUSES:
        _lock(application, user)
    return offer


def convert_to_employee(offer, user, company, department, designation, employee_code):
    """Turns an ACCEPTED offer's candidate into a real Employee record,
    and seeds an EmployeeSalary from the offered CTC. Note: converting is
    allowed even on a locked (Hired) application by design — locking protects
    the *recruitment outcome* from being reversed, not the onboarding step
    that follows it."""
    if offer.status != m.OfferLetter.Status.ACCEPTED:
        raise HiringError('Only an accepted offer can be converted to an employee.')

    candidate = offer.application.candidate
    if m.Employee.objects.filter(email=candidate.email).exists():
        raise HiringError(f'An employee with emails {candidate.email} already exists.')

    employee = m.Employee.objects.create(
        company=company, department=department, designation=designation, employee_code=employee_code,
        first_name=candidate.first_name, last_name=candidate.last_name, email=candidate.email,
        phone=candidate.phone, date_of_joining=offer.joining_date or timezone.localdate(),
        employment_type=m.Employee.EmploymentType.FULL_TIME, status=m.Employee.Status.ACTIVE,
    )
    m.EmployeeSalary.objects.create(
        employee=employee, ctc_annual=offer.ctc_offered,
        effective_from=offer.joining_date or timezone.localdate(), is_active=True,
    )
    log_action(offer.application, user, f'Converted to Employee ({employee.employee_code})')
    return employee