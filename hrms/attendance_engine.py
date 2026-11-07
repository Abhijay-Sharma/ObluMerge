"""
Unified Attendance Engine — the SINGLE source of truth for how a day is classified.

Used by:
  * Attendance Matrix          (views.AttendanceMatrixView)
  * All Records / Punch report (views.AttendanceRecordListView)
  * Monthly Punch Analysis     (views.EmployeePunchReportView)
  * Payroll                    (payroll_logic.compute_attendance_breakdown)

The rules mirror the Attendance Matrix (which HR treats as the reference):

  1. HR manual override (Quick Actions)   -> status chosen by HR is final
  2. Punch present:
       a. record marked ON_LEAVE           -> leave
       b. check-in after grace window      -> Half Day (late arrival)
       c. check-in inside grace window     -> G1..Gn (n = policy grace_allowed_count),
                                              Half Day once the monthly limit is exhausted
       d. worked hours < half-day threshold-> Half Day (short duration)
       e. record marked HALF_DAY           -> Half Day
       f. otherwise                        -> Present
  3. Approved leave (full / half)          -> leave code (CL / EL / LWP ...)
  4. Holiday (employee's calendar)         -> H
  5. Sunday                                -> OFF
  6. Future date                           -> -
  7. Anything else                         -> Absent

Leaves and penalties are independent "overlays": a day can be Half Day + CL (half) + ₹ penalty
at the same time, exactly like Petpooja's quick actions.
"""
import collections
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from . import models as m

MANUAL_PENALTY_SOURCE = 'MANUAL'

# Leave-type code -> EmployeeLeaveBalanceLive field
WALLET_FIELD_MAP = {
    'CL': 'casual_leave', 'EL': 'earned_leave', 'SL': 'sick_leave',
    'ML': 'menstrual_leave', 'MTL': 'menstrual_leave',
    'BL': 'bereavement_leave', 'CO': 'comp_off',
    'PL': 'paternity_leave', 'MATL': 'maternity_leave', 'MATERITY L': 'maternity_leave',
    'MATERNITY': 'maternity_leave',
}

BADGE = {
    'present': 'bg-success-subtle text-success border-success-subtle',
    'grace': 'bg-warning-subtle text-warning-emphasis border-warning-subtle',
    'half_day': 'bg-info-subtle text-info-emphasis border-info-subtle',
    'on_leave': 'bg-primary-subtle text-primary border-primary-subtle',
    'lwp': 'bg-danger-subtle text-danger border-danger-subtle',
    'absent': 'bg-danger-subtle text-danger border-danger-subtle',
    'holiday': 'bg-secondary-subtle text-secondary border-secondary-subtle',
    'week_off': 'bg-light text-muted border',
    'future': 'bg-transparent text-muted',
}

# CSS classes used by the Records page / Punch report (status-mark / status-badge styles)
REPORT_CSS = {
    'present': 'mark-fd', 'grace': 'mark-fd', 'half_day': 'mark-hd', 'on_leave': 'mark-leave',
    'absent': 'mark-abs', 'holiday': 'mark-hol', 'week_off': 'mark-sun', 'future': 'mark-future',
}


def wallet_field_for(leave_type):
    """Returns the EmployeeLeaveBalanceLive field that tracks this leave type (or None)."""
    if not leave_type:
        return None
    code = (leave_type.code or '').upper().strip()
    if code in WALLET_FIELD_MAP:
        return WALLET_FIELD_MAP[code]
    name = (leave_type.name or '').lower()
    for key, field in (('casual', 'casual_leave'), ('earned', 'earned_leave'), ('sick', 'sick_leave'),
                       ('menstrual', 'menstrual_leave'), ('bereave', 'bereavement_leave'),
                       ('breave', 'bereavement_leave'), ('comp', 'comp_off'),
                       ('maternity', 'maternity_leave'), ('materity', 'maternity_leave'),
                       ('paternity', 'paternity_leave')):
        if key in name:
            return field
    return None


def is_auto_text(text):
    t = (text or '').lower()
    return 'auto-approved' in t or 'auto-approval' in t


# ---------------------------------------------------------------------------
# Policy cache (avoids one query per employee per day)
# ---------------------------------------------------------------------------
class _PolicyCache:
    def __init__(self):
        self._history = {}

    def get(self, company, day):
        if company is None:
            return None
        if company.id not in self._history:
            self._history[company.id] = list(
                m.AttendancePolicy.objects.filter(company=company).order_by('-effective_from')
            )
        for p in self._history[company.id]:
            if p.effective_from <= day:
                return p
        return company  # Company carries the same timing fields (master settings)


def _policy_values(policy):
    return {
        'start': getattr(policy, 'office_start_time', None) or time(9, 0),
        'end': getattr(policy, 'office_end_time', None) or time(18, 0),
        'grace_mins': int(getattr(policy, 'grace_minutes', 15) or 0),
        'grace_limit': int(getattr(policy, 'grace_allowed_count', 3) or 0),
        'half_thresh': Decimal(str(getattr(policy, 'half_day_threshold_hours', 4) or 4)),
    }


def _aware(day, t):
    dt = datetime.combine(day, t)
    return timezone.make_aware(dt, timezone.get_current_timezone()) if timezone.is_naive(dt) else dt


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def build_attendance(employees, start_date, end_date, today=None):
    """
    Returns {employee_id: {'days': [day_dict, ...], 'by_date': {date: day_dict}, 'summary': {...}}}
    """
    employees = list(employees)
    today = today or timezone.localdate()
    emp_ids = [e.id for e in employees]
    date_list = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    # ---- Prefetch ----
    att_lookup = collections.defaultdict(dict)
    for r in m.AttendanceRecord.objects.filter(
            employee_id__in=emp_ids, attendance_date__range=[start_date, end_date]
    ).select_related('edited_by'):
        att_lookup[r.employee_id][r.attendance_date] = r

    leave_lookup = collections.defaultdict(dict)
    for lv in m.LeaveApplication.objects.filter(
            employee_id__in=emp_ids, status=m.LeaveApplication.Status.APPROVED,
            start_date__lte=end_date, end_date__gte=start_date
    ).select_related('leave_type').order_by('applied_on'):
        cur = max(lv.start_date, start_date)
        while cur <= min(lv.end_date, end_date):
            leave_lookup[lv.employee_id][cur] = lv
            cur += timedelta(days=1)

    cal_ids = {e.holiday_calendar_id for e in employees if e.holiday_calendar_id}
    holiday_lookup = collections.defaultdict(dict)
    if cal_ids:
        for h in m.Holiday.objects.filter(calendar_id__in=cal_ids, date__range=[start_date, end_date]):
            holiday_lookup[h.calendar_id][h.date] = h.name

    penalty_lookup = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in m.AttendancePenalty.objects.filter(
            employee_id__in=emp_ids, penalty_date__range=[start_date, end_date]
    ).exclude(status=m.AttendancePenalty.DeductionStatus.WAIVED):
        penalty_lookup[p.employee_id][p.penalty_date].append(p)

    policies = _PolicyCache()
    result = {}

    for emp in employees:
        grace_counter = collections.defaultdict(int)  # (year, month) -> used
        days = []
        summary = {
            'present': 0, 'grace': 0, 'half_day': 0, 'on_leave': Decimal('0'), 'absent': Decimal('0'),
            'holiday': 0, 'week_off': 0, 'penalty_amount': Decimal('0'), 'penalty_count': 0,
            # payroll buckets
            'full_days': Decimal('0'), 'half_days': Decimal('0'), 'comp_off_days': Decimal('0'),
            'off_days': Decimal('0'), 'paid_leave_days': Decimal('0'), 'unpaid_leave_days': Decimal('0'),
            'absent_days': Decimal('0'),
        }
        is_intern = (getattr(emp, 'employment_type', '') or '') in ('intern', 'trainee')

        for day in date_list:
            pv = _policy_values(policies.get(emp.company, day))
            rec = att_lookup[emp.id].get(day)
            leave = leave_lookup[emp.id].get(day)
            holiday_name = holiday_lookup[emp.holiday_calendar_id].get(day) if emp.holiday_calendar_id else None
            penalties = penalty_lookup[emp.id].get(day, [])

            d = _classify_day(emp, day, rec, leave, holiday_name, pv, grace_counter, today)

            # ---- Penalty overlay ----
            manual_pen = [p for p in penalties if p.deduction_source == MANUAL_PENALTY_SOURCE]
            auto_pen = [p for p in penalties if p.deduction_source != MANUAL_PENALTY_SOURCE]
            d['penalties'] = [{
                'id': p.id, 'amount': p.salary_deduction_amount or Decimal('0'), 'reason': p.reason,
            } for p in manual_pen]
            d['penalty_amount'] = sum((p.salary_deduction_amount or Decimal('0') for p in manual_pen), Decimal('0'))
            d['auto_penalty'] = auto_pen[0].reason if auto_pen else ''
            if d['penalty_amount'] > 0:
                summary['penalty_amount'] += d['penalty_amount']
                summary['penalty_count'] += 1

            _accumulate(summary, d, is_intern)
            days.append(d)

        result[emp.id] = {'days': days, 'by_date': {x['date']: x for x in days}, 'summary': summary}
    return result


def _leave_info(leave):
    if not leave:
        return None
    code = (leave.leave_type.code or 'LV').upper()
    return {
        'id': leave.id,
        'code': code,
        'name': leave.leave_type.name,
        'is_half': (getattr(leave, 'day_type', 'full') or 'full') == 'half',
        'is_paid': bool(leave.leave_type.is_paid) and code != 'LWP',
        'is_auto': is_auto_text(leave.reason),
        'is_single_day': leave.start_date == leave.end_date,
        'leave_type_id': leave.leave_type_id,
        'reason': leave.reason or 'Approved Leave',
    }


def _classify_day(emp, day, rec, leave, holiday_name, pv, grace_counter, today):
    li = _leave_info(leave)
    d = {
        'date': day, 'status': 'absent', 'badge': 'A', 'label': 'Absent',
        'record_id': rec.id if rec else None,
        'check_in': None, 'check_out': None, 'in_str': '', 'out_str': '', 'hours': Decimal('0'),
        'grace_no': None, 'leave': li, 'is_off_day': bool(holiday_name) or day.weekday() == 6,
        'holiday_name': holiday_name or '',
        'manual_override': bool(rec and getattr(rec, 'manual_override', False)),
        'is_edited': bool(rec and rec.edited_by_id),
        'edit_tooltip': '', 'is_auto_leave': False,
        'rec_status': rec.status if rec else '',
        'late_minutes': int(rec.late_minutes or 0) if rec else 0,
        'remarks': (rec.remarks or '') if rec else '',
        'record': rec,
    }
    edit_reason_val = ''
    if rec:
        if rec.edit_reason:
            edit_reason_val = rec.edit_reason.strip()
            if rec.remarks and rec.remarks.strip() and rec.remarks.strip() not in edit_reason_val:
                edit_reason_val = f"{edit_reason_val}\n---\nRemark: {rec.remarks.strip()}"
        elif rec.remarks:
            edit_reason_val = f"Remark: {rec.remarks.strip()}"

    d['edit_reason'] = edit_reason_val
    d['edited_by_name'] = (rec.edited_by.get_full_name() or rec.edited_by.username) if (rec and rec.edited_by) else ''
    d['edited_on_str'] = timezone.localtime(rec.edited_on).strftime('%d %b %Y, %I:%M %p') if (rec and rec.edited_on) else ''

    if rec and rec.edited_by_id:
        when = timezone.localtime(rec.edited_on).strftime('%d %b %H:%M') if rec.edited_on else ''
        who = rec.edited_by.username if rec.edited_by else ''
        d['edit_tooltip'] = f"Edited by {who} on {when}: {edit_reason_val}"
    elif edit_reason_val:
        d['edit_tooltip'] = f"Remarks: {edit_reason_val}"

    if rec and is_auto_text(rec.remarks):
        d['is_auto_leave'] = True
    if li and li['is_auto']:
        d['is_auto_leave'] = True

    local_in = timezone.localtime(rec.check_in) if (rec and rec.check_in) else None
    local_out = timezone.localtime(rec.check_out) if (rec and rec.check_out) else None
    if local_in:
        d['check_in'], d['in_str'] = local_in, local_in.strftime('%H:%M')
    if local_out:
        d['check_out'], d['out_str'] = local_out, local_out.strftime('%H:%M')
    if rec:
        hrs = Decimal(str(rec.total_hours or 0))
        if hrs == 0 and local_in and local_out:
            hrs = Decimal(str(round((local_out - local_in).total_seconds() / 3600, 2)))
        d['hours'] = hrs

    # ---------- 1. HR manual override (Quick Actions) ----------
    if d['manual_override']:
        st = rec.status
        if st == m.AttendanceRecord.Status.PRESENT:
            _set(d, 'present', 'P', 'Full Day (marked by HR)')
        elif st == m.AttendanceRecord.Status.HALF_DAY:
            _set_half(d, li, 'Half Day (marked by HR)')
        elif st == m.AttendanceRecord.Status.ON_LEAVE:
            _set_leave(d, li, fallback_label='On Leave (marked by HR)')
        elif st == m.AttendanceRecord.Status.WEEK_OFF:
            _set(d, 'week_off', 'OFF', 'Week Off (marked by HR)')
        elif st == m.AttendanceRecord.Status.HOLIDAY:
            _set(d, 'holiday', 'H', holiday_name or 'Holiday (marked by HR)')
        else:  # absent
            if li:
                _set_leave(d, li)
            else:
                _set(d, 'absent', 'A', 'Absent (marked by HR)')
        return d

    # ---------- 2. Punch based evaluation (matrix rules) ----------
    if local_in:
        sched_start = _aware(day, pv['start'])
        grace_cutoff = sched_start + timedelta(minutes=pv['grace_mins'])

        if rec.status == m.AttendanceRecord.Status.ON_LEAVE:
            _set_leave(d, li, fallback_label='On Leave')
        elif local_in > grace_cutoff:
            _set_half(d, li, f"Half Day: Late arrival after grace window (> {grace_cutoff.strftime('%I:%M %p')})")
        elif sched_start < local_in <= grace_cutoff:
            key = (day.year, day.month)
            grace_counter[key] += 1
            if grace_counter[key] <= pv['grace_limit']:
                n = grace_counter[key]
                d['grace_no'] = n
                _set(d, 'grace', f'G{n}', f"Grace #{n} of {pv['grace_limit']} used (arrived {local_in.strftime('%H:%M')})")
            else:
                _set_half(d, li, f"Half Day: Grace limit exhausted (G{pv['grace_limit']} crossed)")
        elif Decimal('0') < d['hours'] < pv['half_thresh']:
            _set_half(d, li, f"Half Day: Short duration ({d['hours']}h < {pv['half_thresh']}h)")
        elif rec.status == m.AttendanceRecord.Status.HALF_DAY:
            _set_half(d, li, 'Half Day')
        else:
            _set(d, 'present', 'P', 'Present')
        return d

    # ---------- 3. Leave ----------
    if li:
        _set_leave(d, li)
        return d
    if rec and rec.status == m.AttendanceRecord.Status.ON_LEAVE:
        _set_leave(d, None, fallback_label=rec.remarks or 'On Leave')
        return d

    # ---------- 4-7. Calendar ----------
    if holiday_name:
        _set(d, 'holiday', 'H', holiday_name)
    elif day.weekday() == 6:
        _set(d, 'week_off', 'OFF', 'Sunday')
    elif day > today:
        _set(d, 'future', '-', 'Future')
    else:
        _set(d, 'absent', 'A', 'Absent')
    return d


def _set(d, status, badge, label):
    d['status'], d['badge'], d['label'] = status, badge, label
    d['badge_class'] = BADGE[status]
    d['css'] = REPORT_CSS[status]


def _set_half(d, li, label):
    if li:
        _set(d, 'half_day', f"HD+{li['code']}", f"{label} + {li['code']} ({'half' if li['is_half'] else 'full'} leave)")
    else:
        _set(d, 'half_day', 'HD', label)


def _set_leave(d, li, fallback_label='On Leave'):
    if not li:
        _set(d, 'on_leave', 'LV', fallback_label)
        return
    if li['is_half']:
        _set(d, 'on_leave', f"½{li['code']}", f"Half-day {li['name']} (other half absent)")
    else:
        _set(d, 'on_leave', li['code'], f"{li['name']}{' (Auto-Approved)' if li['is_auto'] else ''}")
    if not li['is_paid']:
        d['badge_class'] = BADGE['lwp']


def _accumulate(s, d, is_intern):
    """Adds a classified day into the display stats and the payroll buckets."""
    st, li = d['status'], d['leave']
    one, half = Decimal('1'), Decimal('0.5')
    paid_leave = bool(li and li['is_paid'] and not is_intern)

    if st in ('present', 'grace'):
        s['present'] += 1
        if st == 'grace':
            s['grace'] += 1
        if d['is_off_day'] and d['check_in']:
            s['comp_off_days'] += one
        else:
            s['full_days'] += one
    elif st == 'half_day':
        s['half_day'] += 1
        s['half_days'] += one
        if li:
            s['on_leave'] += half
            if paid_leave:
                s['paid_leave_days'] += half
            else:
                s['unpaid_leave_days'] += half
                s['absent_days'] += half
        else:
            s['absent_days'] += half
    elif st == 'on_leave':
        amt = half if (li and li['is_half']) else one
        s['on_leave'] += amt
        if li and paid_leave:
            s['paid_leave_days'] += amt
        else:
            s['unpaid_leave_days'] += amt
            s['absent_days'] += amt
        if amt == half:  # the other half is an absence
            s['absent'] += half
            s['absent_days'] += half
    elif st == 'holiday':
        s['holiday'] += 1
        s['off_days'] += one
    elif st == 'week_off':
        s['week_off'] += 1
        s['off_days'] += one
    elif st == 'absent':
        s['absent'] += one
        s['absent_days'] += one
    elif st == 'future':
        s['absent_days'] += one  # payroll treats unworked future days as unpaid


def payroll_breakdown(employee, year, month):
    """Payroll-ready breakdown computed from the same engine the matrix uses."""
    import calendar
    total = calendar.monthrange(year, month)[1]
    start, end = date(year, month, 1), date(year, month, total)
    data = build_attendance([employee], start, end)[employee.id]
    s = data['summary']
    max_days = Decimal(str(total))
    paid = s['full_days'] + s['half_days'] * Decimal('0.5') + s['comp_off_days'] + s['off_days'] + s['paid_leave_days']
    paid = min(max_days, max(Decimal('0'), paid))
    return {
        'full_days': s['full_days'],
        'half_days': s['half_days'],
        'comp_off_days': s['comp_off_days'],
        'off_days': s['off_days'],
        'paid_leave_days': s['paid_leave_days'],
        'unpaid_leave_days': s['unpaid_leave_days'],
        'absent_days': max(Decimal('0'), max_days - paid),
        'paid_days': paid,
        'total_days_in_month': total,
        'penalty_amount': s['penalty_amount'],
    }


def manual_penalty_total(employee, year, month):
    from django.db.models import Sum
    agg = m.AttendancePenalty.objects.filter(
        employee=employee, penalty_date__year=year, penalty_date__month=month,
        deduction_source=MANUAL_PENALTY_SOURCE,
    ).exclude(status=m.AttendancePenalty.DeductionStatus.WAIVED).aggregate(t=Sum('salary_deduction_amount'))
    return agg['t'] or Decimal('0')


def leave_wallet_options(employee):
    """Leave types available to the employee with remaining live balance (for 'Use Leave')."""
    wallet, _ = m.EmployeeLeaveBalanceLive.objects.get_or_create(e_name=employee)
    qs = m.LeaveType.objects.filter(company=employee.company).order_by('-is_paid', 'code')
    out = []
    gender = getattr(employee, 'gender', '') or ''
    for lt in qs:
        if lt.applicable_gender not in ('all', '', None) and gender and lt.applicable_gender != gender:
            continue
        field = wallet_field_for(lt)
        code = (lt.code or '').upper()
        balance = None
        if field and hasattr(wallet, field):
            balance = float(getattr(wallet, field) or 0)
        out.append({
            'id': lt.id, 'code': code, 'name': lt.name, 'is_paid': bool(lt.is_paid) and code != 'LWP',
            'balance': balance, 'tracked': balance is not None,
        })
    return out
