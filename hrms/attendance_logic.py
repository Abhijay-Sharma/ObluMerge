"""
Attendance business logic — kept out of views.py so it's independently testable.

Business rules (the "3-strike" grace rule), driven entirely by AttendancePolicy
fields per company — no hardcoded times:

  - Official start   = policy.office_start_time              (e.g. 10:00 AM)
  - Grace window      = policy.grace_window_minutes minutes  (e.g. 15 -> up to 10:15 AM)
  - Grace allowance   = policy.max_grace_per_month instances  (e.g. 3 per month)

CHECK-IN, evaluated immediately on arrival:
  - On time or early (arrival <= start):
      -> Present. early_minutes recorded, late_minutes = 0.
  - Late, but within the grace window (0 < late <= grace_window_minutes):
      Case A — grace instances used this month < max_grace_per_month:
          -> Present. Consume one grace instance (GraceUsageTracker.usage_count += 1).
      Case B — grace instances already exhausted for the month:
          -> Half-Day. (This late arrival does NOT itself consume a grace instance —
             the quota was already spent by earlier late arrivals this month.)
  - Late, beyond the grace window (late > grace_window_minutes):
      Case C — strict penalty:
          -> Half-Day, automatically, regardless of grace quota. Does NOT count
             towards (consume) the monthly grace allowance.
  In every "late" case, the actual late_minutes is still recorded for reporting,
  even when the arrival is forgiven (Case A) — only the *status* reflects forgiveness.

CHECK-OUT:
  - total_hours = check_out - check_in.
  - overtime flag if total_hours > policy.overtime_threshold_hours.
  - If check-in already decided Half-Day (Case B/C), that status is preserved.
    Otherwise, if the employee leaves early enough that total_hours still falls
    under policy.half_day_threshold_hours, it's marked Half-Day at checkout too.
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from . import models as m


class AttendanceError(Exception):
    """Raised for invalid check-in/check-out attempts (already checked in, etc)."""


def get_policy_for_employee(employee):
    return getattr(employee.company, 'attendance_policy', None)
def get_policy_for_employee(employee, target_date=None):
    """Dynamically resolves the active company policy for the given date."""
    d = target_date or timezone.localdate()
    comp = getattr(employee, 'company', None)
    if not comp:
        return None
    if hasattr(comp, 'get_policy_for_date'):
        return comp.get_policy_for_date(d)
    return getattr(comp, 'attendance_policy', None)


def _to_local_aware(naive_dt):
    if timezone.is_naive(naive_dt):
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())
    return naive_dt


def _get_or_create_tracker(employee, today):
    tracker, _ = m.GraceUsageTracker.objects.get_or_create(
        employee=employee, month=today.month, year=today.year,
        defaults={'usage_count': 0},
    )
    return tracker


def evaluate_arrival(record, policy):
    """Applies the 3-strike grace rule to a record that already has
    `employee`, `attendance_date` and `check_in` set. Mutates the record's
    late_minutes / early_minutes / status / is_half_day / remarks in place.
    Does NOT save. Shared by both self-service check-in and HR manual entry."""
    if not policy or not record.check_in:
        return record

    employee = record.employee
    today = record.attendance_date
    work_start_naive = datetime.combine(today, policy.office_start_time)
    work_start = _to_local_aware(work_start_naive)
    check_in_local = timezone.localtime(record.check_in) if timezone.is_aware(record.check_in) else record.check_in
    delta_minutes = int((check_in_local - work_start).total_seconds() // 60)

    if delta_minutes <= 0:
        record.late_minutes = 0
        record.early_minutes = abs(delta_minutes)
        record.status = m.AttendanceRecord.Status.PRESENT
        record.is_half_day = False

    elif delta_minutes <= policy.grace_window_minutes:
        record.late_minutes = delta_minutes
        tracker = _get_or_create_tracker(employee, today)

        if tracker.usage_count < policy.max_grace_per_month:
            tracker.usage_count += 1
            tracker.save(update_fields=['usage_count', 'updated_at'])
            record.status = m.AttendanceRecord.Status.PRESENT
            record.is_half_day = False
            record.remarks = (
                f'Grace applied ({tracker.usage_count}/{policy.max_grace_per_month} used this month)'
            )
        else:
            record.status = m.AttendanceRecord.Status.HALF_DAY
            record.is_half_day = True
            record.remarks = (
                f'Grace allowance exhausted ({tracker.usage_count}/{policy.max_grace_per_month} '
                f'already used this month) — marked Half-Day'
            )
    else:
        record.late_minutes = delta_minutes
        record.status = m.AttendanceRecord.Status.HALF_DAY
        record.is_half_day = True
        record.remarks = 'Arrived beyond the grace window — marked Half-Day (strict policy)'

    return record

def evaluate_arrival(record, policy):
    """
    Applies grace rules dynamically using models.py fields.
    Does NOT mark Half-Day if arrival is within grace window and strikes remain.
    """
    if not policy or not record.check_in:
        return record

    employee = record.employee
    today = record.attendance_date

    off_start = policy.office_start_time
    grace_mins = getattr(policy, 'grace_minutes', getattr(policy, 'grace_window_minutes', 15))
    grace_limit = getattr(policy, 'grace_allowed_count', getattr(policy, 'max_grace_per_month', 3))

    work_start_naive = datetime.combine(today, off_start)
    work_start = _to_local_aware(work_start_naive)
    check_in_local = timezone.localtime(record.check_in) if timezone.is_aware(record.check_in) else record.check_in
    delta_minutes = int((check_in_local - work_start).total_seconds() // 60)

    if delta_minutes <= 0:
        record.late_minutes = 0
        record.early_minutes = abs(delta_minutes)
        record.status = m.AttendanceRecord.Status.PRESENT
        record.is_half_day = False
        record.remarks = ''

    elif delta_minutes <= grace_mins:
        record.late_minutes = delta_minutes
        record.early_minutes = 0
        tracker = _get_or_create_tracker(employee, today)

        # Grace allowance check: If strikes remain, it is 100% PRESENT (FD)
        if tracker.usage_count < grace_limit:
            tracker.usage_count += 1
            tracker.save(update_fields=['usage_count', 'updated_at'])
            record.status = m.AttendanceRecord.Status.PRESENT
            record.is_half_day = False
            record.remarks = f'Grace applied ({tracker.usage_count}/{grace_limit} used this month)'
        else:
            # Only when grace is exhausted does it become a Half Day
            record.status = m.AttendanceRecord.Status.HALF_DAY
            record.is_half_day = True
            record.remarks = f'Grace allowance exhausted ({tracker.usage_count}/{grace_limit} used) — marked Half-Day'
    else:
        # Arrived past grace window -> Strict Half Day
        record.late_minutes = delta_minutes
        record.early_minutes = 0
        record.status = m.AttendanceRecord.Status.HALF_DAY
        record.is_half_day = True
        record.remarks = 'Arrived beyond grace window — marked Half-Day'

    return record

def check_in(employee, at=None):
    """Creates (or reuses) today's AttendanceRecord and stamps check_in,
    applying the 3-strike grace-window rule. Returns the record."""
    at = at or timezone.now()
    today = timezone.localtime(at).date()

    record, _ = m.AttendanceRecord.objects.get_or_create(
        employee=employee, attendance_date=today,
        defaults={'status': m.AttendanceRecord.Status.PRESENT},
    )
    if record.check_in:
        raise AttendanceError('Already checked in today.')

    record.check_in = at
    policy = get_policy_for_employee(employee)

    if not policy:
        record.status = m.AttendanceRecord.Status.PRESENT
        record.remarks = 'No attendance policy configured for this company.'
        record.save()
        return record

    evaluate_arrival(record, policy)
    record.save()

    # Trigger automatic penalty tracking & leave deduction for late arrival half-days
    if record.status == m.AttendanceRecord.Status.HALF_DAY and record.late_minutes > 0:
        try:
            from .services import process_late_arrival_penalty
            process_late_arrival_penalty(record)
        except Exception:
            pass

    return record


def check_out(employee, at=None):
    """Stamps check_out on today's record and computes total_hours / overtime.
    Half-Day status decided at check-in (Cases B/C) is preserved; an on-time
    check-in can still end up Half-Day here if the employee leaves too early."""
    at = at or timezone.now()
    today = timezone.localtime(at).date()

    try:
        record = m.AttendanceRecord.objects.get(employee=employee, attendance_date=today)
    except m.AttendanceRecord.DoesNotExist:
        raise AttendanceError('Cannot check out before checking in.')

    if not record.check_in:
        raise AttendanceError('Cannot check out before checking in.')
    if record.check_out:
        raise AttendanceError('Already checked out today.')

    record.check_out = at
    duration_hours = Decimal((at - record.check_in).total_seconds() / 3600)
    record.total_hours = duration_hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    policy = get_policy_for_employee(employee)
    if policy:
        already_half_day = record.status == m.AttendanceRecord.Status.HALF_DAY
        if not already_half_day and record.total_hours < policy.half_day_threshold_hours:
            record.is_half_day = True
            record.status = m.AttendanceRecord.Status.HALF_DAY
        record.is_overtime = record.total_hours > policy.overtime_threshold_hours

    record.save()
    return record
