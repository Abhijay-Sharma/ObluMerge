"""
comp_off_logic.py — Isolated Compensatory Off (CO) business logic.

Rules:
  - An employee earns a Comp Off credit when they punch in on a Sunday
    or on a Company Holiday assigned to their calendar.
  - Credit is 1.0 day by default at punch-in.  At punch-out the actual
    hours_worked on the CompOffRecord is updated (does NOT change credits_earned
    because the balance was already credited; HR can manually adjust if needed).
  - Redemption: When a CO LeaveApplication is approved, CompOffRecord rows are
    consumed FIFO (oldest first) until total_days is covered.
  - Reversal: Reject/Cancel restores consumed records back to AVAILABLE.

This module is completely independent of attendance_logic.py.
All writes are wrapped in try/except at the call sites in views.py so they
can NEVER block the core punch-in flow.
"""

from decimal import Decimal
from datetime import date as dt_date

from django.db import transaction
from django.utils import timezone

from . import models as m


# ---------------------------------------------------------------------------
# HELPER: Is a given date an "off day" for this employee?
# ---------------------------------------------------------------------------

def is_off_day(employee, target_date: dt_date):
    """
    Returns (True, reason) if target_date is a Sunday or a Company Holiday
    in the employee's assigned holiday calendar.
    Returns (False, '') otherwise.
    """
    # Sunday check (weekday() == 6)
    if target_date.weekday() == 6:
        return True, 'Sunday / Weekly Off'

    # Company Holiday check via the employee's assigned HolidayCalendar
    if employee.holiday_calendar:
        holiday = m.Holiday.objects.filter(
            calendar=employee.holiday_calendar,
            date=target_date,
        ).first()
        if holiday:
            return True, holiday.name

    return False, ''


# ---------------------------------------------------------------------------
# CREDIT: Award CO credit at punch-in on an off day
# ---------------------------------------------------------------------------

def credit_comp_off(employee, attendance_record):
    """
    Creates (or updates) a CompOffRecord for the worked off-day and
    increments both EmployeeLeaveBalance.comp_off and
    EmployeeLeaveBalanceLive.comp_off by credits_earned.

    Idempotent: uses get_or_create so re-calling on the same day is safe.
    Returns the CompOffRecord instance, or None on error.
    """
    try:
        worked_date = (
            attendance_record.attendance_date
            if attendance_record
            else timezone.localdate()
        )

        # Determine holiday name
        _, holiday_name = is_off_day(employee, worked_date)

        with transaction.atomic():
            rec, created = m.CompOffRecord.objects.get_or_create(
                employee=employee,
                worked_date=worked_date,
                defaults={
                    'attendance_record': attendance_record,
                    'holiday_name': holiday_name or 'Sunday / Weekly Off',
                    'hours_worked': Decimal('0.00'),
                    'credits_earned': Decimal('1.0'),
                    'status': m.CompOffRecord.Status.AVAILABLE,
                },
            )

            if not created:
                # Already credited (e.g., called twice) — do nothing
                return rec

            credits = rec.credits_earned  # 1.0 by default

            # 1. Increment Master Leave Bank
            bank = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()
            if bank:
                bank.comp_off = float(
                    Decimal(str(bank.comp_off or 0)) + credits
                )
                bank.save(update_fields=['comp_off'])

            # 2. Increment Live Balance (the field actually used for redemption checks)
            live, _ = m.EmployeeLeaveBalanceLive.objects.get_or_create(e_name=employee)
            live.comp_off = float(
                Decimal(str(live.comp_off or 0)) + credits
            )
            live.save(update_fields=['comp_off'])

        return rec

    except Exception:
        # Never surface CO errors to the caller — log silently
        import logging
        logging.getLogger(__name__).exception(
            'comp_off_logic.credit_comp_off failed for employee=%s date=%s',
            getattr(employee, 'pk', '?'),
            getattr(attendance_record, 'attendance_date', '?'),
        )
        return None


# ---------------------------------------------------------------------------
# UPDATE HOURS at checkout (optional refinement)
# ---------------------------------------------------------------------------

def update_comp_off_hours(employee, attendance_record):
    """
    Called after punch-out on an off-day to sync the actual hours_worked.
    Does NOT change credits_earned (balance was already credited at punch-in).
    Safe to call even if the record doesn't exist.
    """
    try:
        worked_date = attendance_record.attendance_date
        m.CompOffRecord.objects.filter(
            employee=employee,
            worked_date=worked_date,
            status=m.CompOffRecord.Status.AVAILABLE,
        ).update(hours_worked=attendance_record.total_hours or Decimal('0.00'))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# DEDUCT: Consume credits when a CO leave is approved (FIFO)
# ---------------------------------------------------------------------------

def deduct_comp_off_credits(application):
    """
    Called when a CO leave is approved or applied.
    Consumes CompOffRecord rows oldest-first (FIFO by worked_date) until
    the total_days of the application are covered.
    """
    employee = application.employee
    days_to_consume = Decimal(str(application.total_days or 0))
    availed_date = application.start_date

    available_records = m.CompOffRecord.objects.filter(
        employee=employee,
        worked_date__year=availed_date.year,
        worked_date__month=availed_date.month,
        is_encashed=False,
        status__in=[m.CompOffRecord.Status.AVAILABLE, m.CompOffRecord.Status.PARTIAL],
    ).order_by('worked_date')  # FIFO — oldest credits used first within month

    with transaction.atomic():
        for record in available_records:
            if days_to_consume <= Decimal('0'):
                break

            rem = record.credits_remaining
            if rem <= Decimal('0'):
                continue

            if rem <= days_to_consume:
                # Fully consume remaining on this record
                days_to_consume -= rem
                record.credits_used = (record.credits_used or Decimal('0')) + rem
                record.status = m.CompOffRecord.Status.AVAILED
                record.availed_on_date = availed_date
                record.availed_leave_app = application
                record.save(update_fields=[
                    'credits_used', 'status', 'availed_on_date', 'availed_leave_app', 'updated_at'
                ])
            else:
                # Partial consumption
                record.credits_used = (record.credits_used or Decimal('0')) + days_to_consume
                record.status = m.CompOffRecord.Status.PARTIAL
                record.availed_on_date = availed_date
                record.availed_leave_app = application
                record.save(update_fields=[
                    'credits_used', 'status', 'availed_on_date', 'availed_leave_app', 'updated_at'
                ])
                days_to_consume = Decimal('0')


# ---------------------------------------------------------------------------
# RESTORE: Re-open credits when a CO leave is rejected or cancelled
# ---------------------------------------------------------------------------

def restore_comp_off_credits(application):
    """
    Called inside reject_leave() / cancel_leave() when leave_type.code == 'CO'.
    Sets all CompOffRecord rows linked to this application back to AVAILABLE.
    """
    with transaction.atomic():
        recs = m.CompOffRecord.objects.filter(availed_leave_app=application)
        days_to_restore = Decimal(str(application.total_days or 0))
        for rec in recs:
            used = rec.credits_used or Decimal('0')
            restore_amt = min(used, days_to_restore)
            rec.credits_used = max(Decimal('0'), used - restore_amt)
            days_to_restore -= restore_amt
            if rec.credits_used == Decimal('0'):
                rec.status = m.CompOffRecord.Status.AVAILABLE
                rec.availed_on_date = None
                rec.availed_leave_app = None
            else:
                rec.status = m.CompOffRecord.Status.PARTIAL
            rec.save(update_fields=['credits_used', 'status', 'availed_on_date', 'availed_leave_app', 'updated_at'])
