"""
Payroll processing engine — kept out of views.py.
Synchronized with attendance_logic.py, leave_logic.py, and AttendancePenalty.
"""
import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import models as m


class PayrollError(Exception):
    """Raised for run-level problems."""


TWO_DP = Decimal('0.01')


def _q(value, places=TWO_DP):
    """Rounds Decimal value to two decimal places."""
    return Decimal(str(value or 0.0)).quantize(places, rounding=ROUND_HALF_UP)


def get_active_salary(employee, as_of):
    """Returns the EmployeeSalary row active on the given date."""
    return (
        m.EmployeeSalary.objects
        .filter(employee=employee, is_active=True, effective_from__lte=as_of)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of))
        .order_by('-effective_from')
        .first()
    )


def get_employee_holidays(employee, year, month):
    """Fetches holiday dates specific to the employee's assigned regional calendar or company."""
    if employee.holiday_calendar:
        return set(
            m.Holiday.objects.filter(
                calendar=employee.holiday_calendar,
                date__year=year,
                date__month=month
            ).values_list('date', flat=True)
        )
    elif employee.company:
        return set(
            m.Holiday.objects.filter(
                calendar__company=employee.company,
                date__year=year,
                date__month=month
            ).values_list('date', flat=True)
        )
    return set()


def compute_attendance_breakdown(employee, year, month):
    """
    Computes exact paid days and absence breakdown based on attendance records,
    holidays, approved leaves, and company policies.
    """
    total_days_in_month = calendar.monthrange(year, month)[1]
    comp = employee.company
    holidays = get_employee_holidays(employee, year, month)

    month_start = date(year, month, 1)
    month_end = date(year, month, total_days_in_month)

    records = {
        r.attendance_date: r
        for r in m.AttendanceRecord.objects.filter(
            employee=employee,
            attendance_date__year=year,
            attendance_date__month=month
        )
    }

    # Pre-fetch approved leaves covering this month
    leaves = m.LeaveApplication.objects.filter(
        employee=employee,
        status=m.LeaveApplication.Status.APPROVED,
        start_date__lte=month_end,
        end_date__gte=month_start
    ).select_related('leave_type')

    leave_map = {}
    for l in leaves:
        c = max(l.start_date, month_start)
        while c <= min(l.end_date, month_end):
            is_half = getattr(l, 'day_type', 'full') == 'half'
            leave_map[c] = {
                'code': (l.leave_type.code or '').upper(),
                'is_paid': l.leave_type.is_paid,
                'is_half': is_half,
            }
            c += timedelta(days=1)

    full_days = Decimal('0.0')
    half_days = Decimal('0.0')
    comp_off_days = Decimal('0.0')
    off_days = Decimal('0.0')
    paid_leave_days = Decimal('0.0')
    unpaid_leave_days = Decimal('0.0')
    absent_days = Decimal('0.0')

    grace_used_count = 0

    for day_num in range(1, total_days_in_month + 1):
        d = date(year, month, day_num)

        policy = comp.get_policy_for_date(d) if (comp and hasattr(comp, 'get_policy_for_date')) else comp
        off_start = getattr(policy, 'office_start_time', None) or time(10, 0)
        off_end = getattr(policy, 'office_end_time', None) or time(18, 0)
        grace_mins = getattr(policy, 'grace_minutes', 15)
        grace_limit = getattr(policy, 'grace_allowed_count', 3)
        full_threshold = float(getattr(policy, 'full_day_threshold_hours', 8.0) or 8.0)
        half_threshold = float(getattr(policy, 'half_day_threshold_hours', 4.0) or 4.0)

        is_holiday_or_sun = (d.weekday() == 6) or (d in holidays)
        record = records.get(d)
        leave_info = leave_map.get(d)

        # 1. Weekly Offs (Sundays) and Holidays
        if is_holiday_or_sun:
            if record and record.check_in:
                comp_off_days += Decimal('1.0')
            else:
                off_days += Decimal('1.0')
            continue

        # 2. Approved Leaves without punches
        if not (record and record.check_in) and leave_info:
            if leave_info['is_paid'] and employee.employment_type != 'intern':
                if leave_info['is_half']:
                    paid_leave_days += Decimal('0.5')
                    absent_days += Decimal('0.5')
                else:
                    paid_leave_days += Decimal('1.0')
            else:
                unpaid_leave_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
                absent_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
            continue

        # 3. Working Days with Attendance Punches
        if record and record.check_in:
            if record.status == m.AttendanceRecord.Status.ON_LEAVE:
                if leave_info and leave_info['is_paid'] and employee.employment_type != 'intern':
                    paid_leave_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
                else:
                    unpaid_leave_days += Decimal('1.0')
                    absent_days += Decimal('1.0')
                continue

            local_in = timezone.localtime(record.check_in).time() if timezone.is_aware(record.check_in) else record.check_in.time()
            local_out = timezone.localtime(record.check_out).time() if (record.check_out and timezone.is_aware(record.check_out)) else (record.check_out.time() if record.check_out else None)

            p_in = local_in
            p_out = local_out if local_out else off_start

            eff_s = max(p_in, off_start)
            eff_e = min(p_out, off_end) if local_out else off_start
            eff_hours = max(0.0, (datetime.combine(d, eff_e) - datetime.combine(d, eff_s)).total_seconds() / 3600)
            grace_deadline = (datetime.combine(d, off_start) + timedelta(minutes=grace_mins)).time()

            is_fd = False
            if p_in <= off_start:
                is_fd = (eff_hours >= full_threshold) or (local_out and p_out >= off_end)
            elif p_in <= grace_deadline:
                if local_out and p_out >= off_end:
                    if grace_used_count < grace_limit:
                        grace_used_count += 1
                        is_fd = True
                    else:
                        is_fd = False
                else:
                    is_fd = False
            else:
                is_fd = False

            if is_fd:
                full_days += Decimal('1.0')
            else:
                half_days += Decimal('1.0')
                # Check if other half was covered by an approved half-day leave
                if leave_info and leave_info['is_paid'] and leave_info['is_half'] and employee.employment_type != 'intern':
                    paid_leave_days += Decimal('0.5')
                else:
                    absent_days += Decimal('0.5')
        else:
            # 4. No Punch and No Leave = Full Absent
            absent_days += Decimal('1.0')

    max_days = Decimal(str(total_days_in_month))
    total_paid = full_days + (half_days * Decimal('0.5')) + comp_off_days + off_days + paid_leave_days
    paid_days = min(max_days, max(Decimal('0.0'), total_paid))
    final_absent_days = max(Decimal('0.0'), max_days - paid_days)

    return {
        'full_days': full_days,
        'half_days': half_days,
        'comp_off_days': comp_off_days,
        'off_days': off_days,
        'paid_leave_days': paid_leave_days,
        'unpaid_leave_days': unpaid_leave_days,
        'absent_days': final_absent_days,
        'paid_days': paid_days,
        'total_days_in_month': total_days_in_month,
    }
def compute_attendance_breakdown(employee, year, month):
    """
    Computes exact paid days and absence breakdown based on attendance records,
    holidays, approved leaves, and company policies.
    """
    total_days_in_month = calendar.monthrange(year, month)[1]
    comp = employee.company
    holidays = get_employee_holidays(employee, year, month)

    month_start = date(year, month, 1)
    month_end = date(year, month, total_days_in_month)

    records = {
        r.attendance_date: r
        for r in m.AttendanceRecord.objects.filter(
            employee=employee,
            attendance_date__year=year,
            attendance_date__month=month
        )
    }

    # Pre-fetch approved leaves covering this month
    leaves = m.LeaveApplication.objects.filter(
        employee=employee,
        status=m.LeaveApplication.Status.APPROVED,
        start_date__lte=month_end,
        end_date__gte=month_start
    ).select_related('leave_type')

    leave_map = {}
    for l in leaves:
        c = max(l.start_date, month_start)
        while c <= min(l.end_date, month_end):
            is_half = getattr(l, 'day_type', 'full') == 'half'
            leave_map[c] = {
                'code': (l.leave_type.code or '').upper(),
                'is_paid': l.leave_type.is_paid,
                'is_half': is_half,
            }
            c += timedelta(days=1)

    full_days = Decimal('0.0')
    half_days = Decimal('0.0')
    comp_off_days = Decimal('0.0')
    off_days = Decimal('0.0')
    paid_leave_days = Decimal('0.0')
    unpaid_leave_days = Decimal('0.0')
    absent_days = Decimal('0.0')

    grace_used_count = 0

    for day_num in range(1, total_days_in_month + 1):
        d = date(year, month, day_num)

        policy = comp.get_policy_for_date(d) if (comp and hasattr(comp, 'get_policy_for_date')) else comp
        off_start = getattr(policy, 'office_start_time', None) or time(10, 0)
        off_end = getattr(policy, 'office_end_time', None) or time(18, 0)
        grace_mins = getattr(policy, 'grace_minutes', 15)
        grace_limit = getattr(policy, 'grace_allowed_count', 3)
        full_threshold = float(getattr(policy, 'full_day_threshold_hours', 8.0) or 8.0)
        half_threshold = float(getattr(policy, 'half_day_threshold_hours', 4.0) or 4.0)

        is_holiday_or_sun = (d.weekday() == 6) or (d in holidays)
        record = records.get(d)
        leave_info = leave_map.get(d)

        # 1. Weekly Offs (Sundays) and Holidays
        if is_holiday_or_sun:
            if record and record.check_in:
                comp_off_days += Decimal('1.0')
            else:
                off_days += Decimal('1.0')
            continue

        # 2. Approved Leaves without punches
        if not (record and record.check_in) and leave_info:
            if leave_info['is_paid'] and employee.employment_type != 'intern':
                if leave_info['is_half']:
                    paid_leave_days += Decimal('0.5')
                    absent_days += Decimal('0.5')
                else:
                    paid_leave_days += Decimal('1.0')
            else:
                unpaid_leave_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
                absent_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
            continue

        # 3. Working Days with Attendance Punches
        if record and record.check_in:
            if record.status == m.AttendanceRecord.Status.ON_LEAVE:
                if leave_info and leave_info['is_paid'] and employee.employment_type != 'intern':
                    paid_leave_days += Decimal('0.5' if leave_info['is_half'] else '1.0')
                else:
                    unpaid_leave_days += Decimal('1.0')
                    absent_days += Decimal('1.0')
                continue

            local_in = timezone.localtime(record.check_in).time() if timezone.is_aware(record.check_in) else record.check_in.time()
            local_out = timezone.localtime(record.check_out).time() if (record.check_out and timezone.is_aware(record.check_out)) else (record.check_out.time() if record.check_out else None)

            p_in = local_in
            p_out = local_out if local_out else off_start

            eff_s = max(p_in, off_start)
            eff_e = min(p_out, off_end) if local_out else off_start
            eff_hours = max(0.0, (datetime.combine(d, eff_e) - datetime.combine(d, eff_s)).total_seconds() / 3600)
            grace_deadline = (datetime.combine(d, off_start) + timedelta(minutes=grace_mins)).time()

            is_fd = False
            if p_in <= off_start:
                is_fd = (eff_hours >= full_threshold) or (local_out and p_out >= off_end)
            elif p_in <= grace_deadline:
                if local_out and p_out >= off_end:
                    if grace_used_count < grace_limit:
                        grace_used_count += 1
                        is_fd = True
                    else:
                        is_fd = False
                else:
                    is_fd = False
            else:
                is_fd = False

            if is_fd:
                full_days += Decimal('1.0')
            else:
                half_days += Decimal('1.0')
                if leave_info and leave_info['is_paid'] and leave_info['is_half'] and employee.employment_type != 'intern':
                    paid_leave_days += Decimal('0.5')
                else:
                    absent_days += Decimal('0.5')
        else:
            # 4. No punch and no leave is treated as an absent day
            absent_days += Decimal('1.0')

    max_days = Decimal(str(total_days_in_month))
    total_paid = full_days + (half_days * Decimal('0.5')) + comp_off_days + off_days + paid_leave_days
    paid_days = min(max_days, max(Decimal('0.0'), total_paid))
    final_absent_days = max(Decimal('0.0'), max_days - paid_days)

    return {
        'full_days': full_days,
        'half_days': half_days,
        'comp_off_days': comp_off_days,
        'off_days': off_days,
        'paid_leave_days': paid_leave_days,
        'unpaid_leave_days': unpaid_leave_days,
        'absent_days': final_absent_days,
        'paid_days': paid_days,
        'total_days_in_month': total_days_in_month,
    }


def get_loan_deduction(employee):
    """Sums this month's installment across active loans: returns (total_deduction, [(loan, installment)])."""
    loans = list(m.LoanAdvance.objects.filter(employee=employee, is_active=True))
    total = Decimal('0.0')
    touched = []
    for loan in loans:
        installment = min(Decimal(str(loan.monthly_installment or 0.0)), Decimal(str(loan.remaining_balance or 0.0)))
        if installment > Decimal('0.0'):
            total += installment
            touched.append((loan, installment))
    return total, touched


def get_pending_extras(employee):
    """Returns unconsumed extras/incentives for the employee: (total_extra, [extra_objects])."""
    extras = list(m.PayrollExtra.objects.filter(employee=employee, is_consumed=False))
    total = sum((Decimal(str(e.amount or 0.0)) for e in extras), Decimal('0.0'))
    return total, extras


def get_unsettled_penalties(employee, year, month):
    """
    Penalties should not be deducted as direct salary cuts.
    Absences and late joinings are already accounted for through paid days proration.
    """
    return Decimal('0.0')


def process_employee(employee, payroll_run, year, month):
    """
    Computes and saves (create-or-update) one employee's PaySlip for this run.
    Returns (payslip, warning_or_None).
    """
    period_end = date(year, month, calendar.monthrange(year, month)[1])
    salary = get_active_salary(employee, period_end)
    if salary is None:
        return None, f'{employee.full_name} ({employee.employee_code}) — no active salary structure configured, skipped.'

    breakdown = compute_attendance_breakdown(employee, year, month)
    total_days = breakdown['total_days_in_month']
    paid_days = breakdown['paid_days']

    # Proration multiplier based strictly on active paid days
    proration_factor = (paid_days / Decimal(str(total_days))) if total_days > 0 else Decimal('0.0')

    monthly_ctc = Decimal(str(salary.ctc_annual or 0.0)) / Decimal('12.0')
    daily_wage = _q(monthly_ctc / Decimal(str(total_days)))
    absence_penalty = _q(breakdown['absent_days'] * daily_wage)

    loan_deduction, touched_loans = get_loan_deduction(employee)
    extra_earning, touched_extras = get_pending_extras(employee)

    # ── Monthly Comp-Off Encashment ──
    # Comp-off is valid for the current calendar month only. Any unused comp-off at month end
    # is encashed and added directly into that month's salary.
    co_records = list(m.CompOffRecord.objects.filter(
        employee=employee,
        worked_date__year=year,
        worked_date__month=month,
    ))
    co_earned = sum((Decimal(str(r.credits_earned or 0.0))) for r in co_records)
    co_used = sum((Decimal(str(r.credits_used or 0.0))) for r in co_records)
    co_remaining = max(Decimal('0.0'), co_earned - co_used)

    co_encashment = Decimal('0.00')
    if co_remaining > Decimal('0.0'):
        co_encashment = _q(co_remaining * daily_wage)
        extra_earning += co_encashment

    is_intern = (employee.employment_type in ['intern', 'trainee'])

    if is_intern:
        # Intern Stipend Model: Pro-rated stipend by paid days
        earned_stipend = _q(monthly_ctc * proration_factor)
        total_earnings = _q(earned_stipend + extra_earning)
        # Only statutory TDS and active loans apply; no duplicate absence penalties
        total_deductions = _q(Decimal(str(salary.tds or 0.0)) + loan_deduction)
        net_pay = _q(max(Decimal('0.0'), total_earnings - total_deductions))
    else:
        # Full-time Employee Model
        earned_basic = _q(Decimal(str(salary.basic or 0.0)) * proration_factor)
        earned_hra = _q(Decimal(str(salary.hra or 0.0)) * proration_factor)
        earned_special = _q(Decimal(str(salary.special_allowance or 0.0)) * proration_factor)

        total_earnings = _q(earned_basic + earned_hra + earned_special + extra_earning)

        # Statutory deductions (pro-rated)
        pf = _q(Decimal(str(salary.pf_employee or 0.0)) * proration_factor)
        esic = _q(Decimal(str(salary.esic_employee or 0.0)) * proration_factor)
        pt = Decimal(str(salary.professional_tax or 0.0))
        tds = Decimal(str(salary.tds or 0.0))

        total_deductions = _q(pf + esic + pt + tds + loan_deduction)
        net_pay = _q(max(Decimal('0.0'), total_earnings - total_deductions))

    with transaction.atomic():
        payslip, _ = m.PaySlip.objects.update_or_create(
            payroll_run=payroll_run,
            employee=employee,
            defaults={
                'full_days': breakdown['full_days'],
                'half_days': breakdown['half_days'],
                'comp_off_days': breakdown['comp_off_days'],
                'off_days': breakdown['off_days'],
                'paid_leave_days': breakdown['paid_leave_days'],
                'absent_days': breakdown['absent_days'],
                'paid_days': breakdown['paid_days'],
                'total_days_in_month': total_days,
                'daily_wage': daily_wage,
                'penalty_deduction': absence_penalty,
                'loan_deduction': _q(loan_deduction),
                'extra_earning': _q(extra_earning),
                'comp_off_earned': co_earned,
                'comp_off_used': co_used,
                'comp_off_remaining': co_remaining,
                'comp_off_encashment': co_encashment,
                'total_earnings': total_earnings,
                'total_deductions': total_deductions,
                'net_pay': net_pay,
            },
        )

        # Mark month's comp-off records as encashed so they don't roll over
        for rec in co_records:
            if not rec.is_encashed:
                rec.is_encashed = True
                rec.save(update_fields=['is_encashed'])

        # Deduct installment from active loans
        for loan, installment in touched_loans:
            loan.remaining_balance = max(Decimal('0.0'), Decimal(str(loan.remaining_balance)) - installment)
            if loan.remaining_balance <= Decimal('0.0'):
                loan.remaining_balance = Decimal('0.0')
                loan.is_active = False
            loan.save(update_fields=['remaining_balance', 'is_active', 'updated_at'])

        # Mark extra earnings as consumed in this payroll run
        for extra in touched_extras:
            extra.is_consumed = True
            extra.payroll_run = payroll_run
            extra.save(update_fields=['is_consumed', 'payroll_run', 'updated_at'])

    return payslip, None


def process_payroll(company, year, month, user=None):
    """
    Processes payroll for all active employees of the given company.
    Creates or updates the PayrollRun batch and returns (payroll_run, warnings).
    """
    payroll_run, _ = m.PayrollRun.objects.get_or_create(
        company=company,
        month=month,
        year=year,
        defaults={
            'status': m.PayrollRun.Status.PROCESSING,
            'created_by': user,
        },
    )
    payroll_run.status = m.PayrollRun.Status.PROCESSING
    if user:
        payroll_run.created_by = user
    payroll_run.save(update_fields=['status', 'created_by', 'updated_at'])

    warnings = []
    employees = m.Employee.objects.filter(company=company, status=m.Employee.Status.ACTIVE)

    for employee in employees:
        _, warning = process_employee(employee, payroll_run, year, month)
        if warning:
            warnings.append(warning)

    payroll_run.status = m.PayrollRun.Status.COMPLETED
    payroll_run.payroll_date = date.today()
    payroll_run.save(update_fields=['status', 'payroll_date', 'updated_at'])

    return payroll_run, warnings