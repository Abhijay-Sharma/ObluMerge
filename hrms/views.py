from datetime import date, datetime, timedelta
from decimal import Decimal
import calendar
import collections
import logging

import io
import json
import os
import zipfile

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, UpdateView, ListView, DetailView, FormView, View, CreateView, DeleteView
from django.forms import modelformset_factory
from django.db import models, transaction

from django.core.files.storage import default_storage
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.db.models import Q, Count, Max, Sum, Prefetch
from . import forms as f
from . import models as m
from . import leave_logic as lv
from . import payroll_logic as pay
from . import attendance_logic as att_logic
from . import hiring_logic as hire
from .permissions import get_role, is_hr_or_above, get_employee_profile, ROLE_SUPERADMIN, ROLE_HR, ROLE_EMPLOYEE, \
    HRRequiredMixin
from .email_logic import send_leave_notification_email
from access_control.mixins import RBACViewMixin
from access_control.services import can, scope_queryset


@login_required
@require_POST
def bulk_download_resumes(request):
    """Download selected candidate resumes as a ZIP archive."""
    if not is_hr_or_above(request.user):
        raise PermissionDenied('HR or admin access is required to download resumes.')

    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except (TypeError, ValueError):
        payload = {}

    def parse_ids(raw_value):
        if raw_value in (None, '', [], {}):
            return []
        if isinstance(raw_value, str):
            values = [item.strip() for item in raw_value.split(',') if item.strip()]
            return [int(value) for value in values]
        if isinstance(raw_value, (list, tuple)):
            return [int(item) for item in raw_value if str(item).strip()]
        return [int(raw_value)]

    application_ids = parse_ids(payload.get('application_ids') or request.POST.getlist('application_ids'))
    candidate_ids = parse_ids(payload.get('candidate_ids') or request.POST.getlist('candidate_ids'))

    if application_ids:
        applications = m.Application.objects.filter(pk__in=application_ids).select_related('candidate')
        candidate_ids.extend(app.candidate_id for app in applications if app.candidate_id)

    candidate_ids = list(dict.fromkeys(filter(None, candidate_ids)))
    if not candidate_ids:
        return HttpResponse('No candidates selected for resume download.', status=400)

    candidates = m.Candidate.objects.filter(pk__in=candidate_ids).exclude(resume='').exclude(resume__isnull=True)
    resume_paths = []
    seen = set()

    for candidate in candidates:
        resume_name = candidate.resume.name
        if not resume_name or not candidate.resume.storage.exists(resume_name):
            continue
        if resume_name not in seen:
            seen.add(resume_name)
            resume_paths.append(resume_name)

    if not resume_paths:
        return HttpResponse('No valid resume files were found for the selected candidates.', status=400)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for resume_path in resume_paths:
            try:
                with default_storage.open(resume_path, 'rb') as file_handle:
                    content = file_handle.read()
            except Exception:
                continue
            archive.writestr(os.path.basename(resume_path), content)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="resumes.zip"'
    return response


# mixins.py or views.py
class CompanyFilterMixin:
    """Filters querysets based on the session's active_company_id and user role."""

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return queryset.none()

        active_id = self.request.session.get('active_company_id')

        # 1. If a specific company is selected in session
        if active_id and active_id != 'all':
            if hasattr(self.model, 'company'):
                return queryset.filter(company_id=active_id)
            elif hasattr(self.model, 'employee'):
                return queryset.filter(employee__company_id=active_id)
            elif hasattr(self.model, 'e_name'):
                return queryset.filter(e_name__company_id=active_id)

        # 2. If 'All' / global view is selected and user is Super Admin
        if user.is_superuser:
            return queryset

        # 3. Non-Superuser (HR / Manager / Employee) — filter by managed/assigned companies
        profile = get_employee_profile(user)
        if profile:
            first_managed = profile.managed_companies.first() if hasattr(profile, 'managed_companies') else None
            locked = first_managed or profile.company
            if locked:
                if hasattr(self.model, 'company'):
                    return queryset.filter(company_id=locked.id)
                elif hasattr(self.model, 'employee'):
                    return queryset.filter(employee__company_id=locked.id)
                elif hasattr(self.model, 'e_name'):
                    return queryset.filter(e_name__company_id=locked.id)

        return queryset


class HRMSLoginView(LoginView):
    template_name = 'hrms/login.html'

    def get_success_url(self):
        return reverse_lazy('hrms:dashboard')


def set_active_company(request):
    """Handles both POST and GET company switching."""
    company_id = request.POST.get('company_id') or request.GET.get('company_id')
    if company_id == 'all':
        request.session['active_company_id'] = 'all'
    elif company_id:
        request.session['active_company_id'] = str(company_id)
    return redirect(request.META.get('HTTP_REFERER', 'hrms:dashboard'))


class SidebarContextMixin:
    """Injects the active_group/active_item so the sidebar highlights correctly."""
    active_group = 'dashboard'
    active_item = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_group'] = self.active_group
        ctx['active_item'] = self.active_item
        return ctx


class AttendanceDailyMonitorView(HRRequiredMixin, SidebarContextMixin, TemplateView):
    """
    Dedicated Full-Page Daily Attendance & Punch Monitor.
    Provides complete punch timestamps, first punch, last punch, working hours,
    live clock status, and an interactive calendar to inspect any date.
    """
    template_name = 'hrms/attendance/daily_monitor.html'
    active_group, active_item = 'attendance', 'attendance_records'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        request = self.request
        user = request.user

        # 1. Calendar date selection (defaults to today)
        target_date_str = request.GET.get('date')
        if target_date_str:
            try:
                target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            except ValueError:
                target_date = date.today()
        else:
            target_date = date.today()

        today = date.today()
        is_today = (target_date == today)

        # 2. Company permissions & filter
        if user.is_superuser:
            available_companies = m.Company.objects.all()
        else:
            employee_profile = get_employee_profile(user)
            if employee_profile and employee_profile.company:
                available_companies = m.Company.objects.filter(
                    Q(id=employee_profile.company_id) | Q(managers=employee_profile)
                ).distinct()
            else:
                available_companies = m.Company.objects.none()

        active_company_id = request.session.get('active_company_id')
        emp_filter = Q(status=m.Employee.Status.ACTIVE)
        attend_filter = Q(attendance_date=target_date)

        if active_company_id and active_company_id != 'all':
            emp_filter &= Q(company_id=active_company_id)
            attend_filter &= Q(employee__company_id=active_company_id)
        elif not user.is_superuser:
            allowed_ids = list(available_companies.values_list('id', flat=True))
            emp_filter &= Q(company_id__in=allowed_ids)
            attend_filter &= Q(employee__company_id__in=allowed_ids)

        active_employees = m.Employee.objects.filter(emp_filter).select_related(
            'designation', 'department', 'company'
        ).order_by('first_name', 'last_name')

        attendance_records = m.AttendanceRecord.objects.filter(attend_filter).select_related('employee')
        attendance_map = {r.employee_id: r for r in attendance_records}

        on_leave_emp_ids = set(
            m.LeaveApplication.objects.filter(
                status=m.LeaveApplication.Status.APPROVED,
                start_date__lte=target_date,
                end_date__gte=target_date,
                employee__in=active_employees
            ).values_list('employee_id', flat=True)
        )

        all_detailed = []
        present_detailed = []
        absent_detailed = []
        on_leave_detailed = []
        currently_working_count = 0

        for emp in active_employees:
            record = attendance_map.get(emp.id)
            policy = (
                emp.company.get_policy_for_date(target_date)
                if hasattr(emp.company, 'get_policy_for_date')
                else None
            )
            grace_minutes = policy.grace_minutes if policy else 15

            if record and (record.check_in or record.status in [m.AttendanceRecord.Status.PRESENT,
                                                                m.AttendanceRecord.Status.HALF_DAY]):
                is_grace = False
                is_late = False
                if record.late_minutes > 0:
                    if record.late_minutes <= grace_minutes:
                        is_grace = True
                    else:
                        is_late = True
                elif record.check_in and policy and policy.office_start_time:
                    local_in = timezone.localtime(record.check_in).time()
                    if local_in > policy.office_start_time:
                        diff_sec = (datetime.combine(date(2000, 1, 1), local_in) - datetime.combine(date(2000, 1, 1),
                                                                                                    policy.office_start_time)).total_seconds()
                        if int(diff_sec / 60) <= grace_minutes:
                            is_grace = True
                        else:
                            is_late = True

                is_running = False
                if record.check_in and not record.check_out:
                    currently_working_count += 1
                    if is_today:
                        is_running = True

                total_hours_display = ''
                if record.total_hours and record.total_hours > 0:
                    hrs = int(record.total_hours)
                    mins = int((record.total_hours - hrs) * 60)
                    total_hours_display = f'{hrs:02d}h {mins:02d}m'
                elif record.check_in and record.check_out:
                    diff_sec = (record.check_out - record.check_in).total_seconds()
                    hrs = int(diff_sec // 3600)
                    mins = int((diff_sec % 3600) // 60)
                    total_hours_display = f'{hrs:02d}h {mins:02d}m'

                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'present',
                    'check_in': record.check_in,
                    'check_out': record.check_out,
                    'check_in_iso': record.check_in.isoformat() if record.check_in else '',
                    'total_hours_display': total_hours_display,
                    'is_running': is_running,
                    'is_grace': is_grace,
                    'is_late': is_late,
                    'late_minutes': record.late_minutes,
                }
                present_detailed.append(emp_item)
                all_detailed.append(emp_item)

            elif emp.id in on_leave_emp_ids or (record and record.status == m.AttendanceRecord.Status.ON_LEAVE):
                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'on_leave',
                    'check_in': None,
                    'check_out': None,
                    'check_in_iso': '',
                    'total_hours_display': '--',
                    'is_running': False,
                    'is_grace': False,
                    'is_late': False,
                    'late_minutes': 0,
                }
                on_leave_detailed.append(emp_item)
                all_detailed.append(emp_item)

            else:
                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'absent',
                    'check_in': None,
                    'check_out': None,
                    'check_in_iso': '',
                    'total_hours_display': '--',
                    'is_running': False,
                    'is_grace': False,
                    'is_late': False,
                    'late_minutes': 0,
                }
                absent_detailed.append(emp_item)
                all_detailed.append(emp_item)

        total_count = active_employees.count()
        present_count = len(present_detailed)
        absent_count = len(absent_detailed)
        time_off_count = len(on_leave_detailed)
        attendance_pct = round((present_count / total_count) * 100) if total_count > 0 else 0

        ctx.update({
            'selected_date': target_date.strftime('%Y-%m-%d'),
            'display_date': target_date.strftime('%d %b, %Y'),
            'is_today': is_today,
            'available_companies': available_companies,
            'active_company_id': active_company_id,
            'total_employees': total_count,
            'present_count': present_count,
            'absent_count': absent_count,
            'time_off_count': time_off_count,
            'currently_working': currently_working_count,
            'attendance_percentage': attendance_pct,
            'all_employees_detailed': all_detailed,
            'present_employees_detailed': present_detailed,
            'absent_employees': absent_detailed,
            'on_leave_employees': on_leave_detailed,
        })
        return ctx


@login_required
def dashboard(request):
    import calendar
    user = request.user
    role = get_role(user)

    # 1. Date Filtering (Calendar Support via GET query param)
    target_date_str = request.GET.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = date.today()
    else:
        target_date = date.today()

    today = date.today()
    is_today = (target_date == today)

    context = {
        'active_group': 'dashboard',
        'active_item': 'dashboard',
        'selected_date': target_date.strftime('%Y-%m-%d'),
        'display_date': target_date.strftime('%d %b, %Y'),
        'is_today': is_today,
    }

    if is_hr_or_above(user):
        # 2. Permission-Based Company List
        if user.is_superuser:
            available_companies = m.Company.objects.all()
        else:
            employee_profile = get_employee_profile(user)
            if employee_profile and employee_profile.company:
                available_companies = m.Company.objects.filter(
                    Q(id=employee_profile.company_id) | Q(managers=employee_profile)
                ).distinct()
            else:
                available_companies = m.Company.objects.none()

        active_company_id = request.session.get('active_company_id')
        current_company = None
        if active_company_id and active_company_id != 'all':
            current_company = available_companies.filter(id=active_company_id).first()
            current_company_name = current_company.name if current_company else "Unit"
        else:
            current_company_name = "All Global Units"

        # 3. Robust Permission & Company-Based Filters
        emp_filter = Q(status=m.Employee.Status.ACTIVE)
        leave_filter = Q(status=m.LeaveApplication.Status.PENDING)
        job_filter = Q(is_active=True)
        attend_filter = Q(attendance_date=target_date)
        asset_filter = Q()
        policy_filter = Q()
        app_filter = Q()

        if active_company_id and active_company_id != 'all':
            emp_filter &= Q(company_id=active_company_id)
            leave_filter &= Q(employee__company_id=active_company_id)
            job_filter &= Q(company_id=active_company_id)
            attend_filter &= Q(employee__company_id=active_company_id)
            asset_filter &= Q(company_id=active_company_id)
            policy_filter &= Q(company_id=active_company_id)
            app_filter &= Q(job_posting__company_id=active_company_id)
        elif not user.is_superuser:
            allowed_ids = list(available_companies.values_list('id', flat=True))
            emp_filter &= Q(company_id__in=allowed_ids)
            leave_filter &= Q(employee__company_id__in=allowed_ids)
            job_filter &= Q(company_id__in=allowed_ids)
            attend_filter &= Q(employee__company_id__in=allowed_ids)
            asset_filter &= Q(company_id__in=allowed_ids)
            policy_filter &= Q(company_id__in=allowed_ids)
            app_filter &= Q(job_posting__company_id__in=allowed_ids)

        # 4. Fetch Optimized Data & Sub-metrics
        active_employees = m.Employee.objects.filter(emp_filter).select_related(
            'designation', 'department', 'company'
        )
        total_employees = active_employees.count()

        if active_company_id and active_company_id != 'all':
            total_departments = m.Department.objects.filter(company_id=active_company_id).count()
            total_designations = m.Designation.objects.filter(department__company_id=active_company_id).count()
        else:
            total_departments = m.Department.objects.count()
            total_designations = m.Designation.objects.count()

        curr_month_start = today.replace(day=1)
        new_joiners_count = active_employees.filter(date_of_joining__gte=curr_month_start).count()

        # 5. Attendance & Live Clock Processing
        attendance_records = m.AttendanceRecord.objects.filter(attend_filter).select_related('employee')
        attendance_map = {r.employee_id: r for r in attendance_records}

        on_leave_emp_ids = set(
            m.LeaveApplication.objects.filter(
                status=m.LeaveApplication.Status.APPROVED,
                start_date__lte=target_date,
                end_date__gte=target_date,
                employee__in=active_employees
            ).values_list('employee_id', flat=True)
        )

        present_employees = []
        absent_employees = []
        on_leave_employees = []
        all_employees_detailed = []
        currently_working_count = 0
        recent_punches = []

        for emp in active_employees:
            record = attendance_map.get(emp.id)
            policy = (
                emp.company.get_policy_for_date(target_date)
                if hasattr(emp.company, 'get_policy_for_date')
                else None
            )
            grace_minutes = policy.grace_minutes if policy else 15

            if record and (record.check_in or record.status in [m.AttendanceRecord.Status.PRESENT,
                                                                m.AttendanceRecord.Status.HALF_DAY]):
                is_grace = False
                is_late = False
                if record.late_minutes > 0:
                    if record.late_minutes <= grace_minutes:
                        is_grace = True
                    else:
                        is_late = True
                elif record.check_in and policy and policy.office_start_time:
                    local_in = timezone.localtime(record.check_in).time()
                    if local_in > policy.office_start_time:
                        diff_sec = (datetime.combine(date(2000, 1, 1), local_in) - datetime.combine(date(2000, 1, 1),
                                                                                                    policy.office_start_time)).total_seconds()
                        if int(diff_sec / 60) <= grace_minutes:
                            is_grace = True
                        else:
                            is_late = True

                is_running = False
                if record.check_in and not record.check_out:
                    currently_working_count += 1
                    if is_today:
                        is_running = True

                total_hours_display = ''
                if record.total_hours and record.total_hours > 0:
                    hrs = int(record.total_hours)
                    mins = int((record.total_hours - hrs) * 60)
                    total_hours_display = f'{hrs:02d}h {mins:02d}m'
                elif record.check_in and record.check_out:
                    diff_sec = (record.check_out - record.check_in).total_seconds()
                    hrs = int(diff_sec // 3600)
                    mins = int((diff_sec % 3600) // 60)
                    total_hours_display = f'{hrs:02d}h {mins:02d}m'

                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'present',
                    'check_in': record.check_in,
                    'check_out': record.check_out,
                    'check_in_iso': record.check_in.isoformat() if record.check_in else '',
                    'total_hours_display': total_hours_display,
                    'is_running': is_running,
                    'is_grace': is_grace,
                    'is_late': is_late,
                    'late_minutes': record.late_minutes,
                }
                present_employees.append(emp_item)
                all_employees_detailed.append(emp_item)
                if record.check_in:
                    recent_punches.append(emp_item)

            elif emp.id in on_leave_emp_ids or (record and record.status == m.AttendanceRecord.Status.ON_LEAVE):
                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'on_leave',
                    'check_in': None,
                    'check_out': None,
                    'check_in_iso': '',
                    'total_hours_display': '--',
                    'is_running': False,
                    'is_grace': False,
                    'is_late': False,
                    'late_minutes': 0,
                }
                on_leave_employees.append(emp_item)
                all_employees_detailed.append(emp_item)

            else:
                emp_item = {
                    'emp': emp,
                    'record': record,
                    'status_category': 'absent',
                    'check_in': None,
                    'check_out': None,
                    'check_in_iso': '',
                    'total_hours_display': '--',
                    'is_running': False,
                    'is_grace': False,
                    'is_late': False,
                    'late_minutes': 0,
                }
                absent_employees.append(emp_item)
                all_employees_detailed.append(emp_item)

        recent_punches.sort(key=lambda x: x['check_in'] or timezone.now(), reverse=True)
        recent_punches = recent_punches[:5]

        present_count = len(present_employees)
        absent_count = len(absent_employees)
        time_off_count = len(on_leave_employees)
        attendance_pct = round((present_count / total_employees) * 100) if total_employees > 0 else 0

        # 6. Biometrics (Company-Filtered!)
        enrolled_biometrics_count = m.EmployeeBiometric.objects.filter(employee__in=active_employees).count()
        pending_biometrics_count = max(0, total_employees - enrolled_biometrics_count)
        biometric_pct = round((enrolled_biometrics_count / total_employees) * 100) if total_employees > 0 else 0

        # 7. Assets (Company-Filtered!)
        asset_qs = m.Asset.objects.filter(asset_filter)
        total_assets = asset_qs.count()
        assigned_assets = asset_qs.filter(status=m.Asset.Status.ASSIGNED).count()
        unassigned_assets = asset_qs.filter(status=m.Asset.Status.AVAILABLE).count()

        # 8. Leaves (Company-Filtered!)
        pending_leaves_count = m.LeaveApplication.objects.filter(leave_filter).count()
        approved_leaves_month = m.LeaveApplication.objects.filter(
            status=m.LeaveApplication.Status.APPROVED,
            start_date__gte=curr_month_start,
            employee__in=active_employees
        ).count()

        # 9. Hiring & Jobs (Company-Filtered!)
        open_jobs_count = m.JobPosting.objects.filter(job_filter).count()
        applications_qs = m.Application.objects.filter(app_filter)
        total_applications_count = applications_qs.count()
        shortlisted_candidates_count = applications_qs.filter(status='shortlisted').count()
        interviewing_candidates_count = applications_qs.filter(status='interviewing').count()
        open_jobs_list = m.JobPosting.objects.filter(job_filter).annotate(
            app_count=Count('applications')
        ).order_by('-created_at')[:4]

        # 10. Policies & Shift Rules (Company-Filtered!)
        policy_qs = m.AttendancePolicy.objects.filter(policy_filter)
        total_policies_count = policy_qs.count()
        first_policy = policy_qs.order_by('-effective_from').first()
        active_shift_start = first_policy.office_start_time.strftime(
            '%I:%M %p') if first_policy and first_policy.office_start_time else "09:30 AM"
        active_shift_end = first_policy.office_end_time.strftime(
            '%I:%M %p') if first_policy and first_policy.office_end_time else "06:30 PM"
        active_grace_minutes = first_policy.grace_minutes if first_policy else 15

        upcoming_birthdays = active_employees.filter(
            date_of_birth__month=target_date.month
        ).order_by('date_of_birth')[:4]

        context.update({
            'available_companies': available_companies,
            'active_company_id': active_company_id,
            'current_company_name': current_company_name,

            # Power Card 1: Workforce & Directory
            'total_employees': total_employees,
            'total_departments': total_departments,
            'total_designations': total_designations,
            'new_joiners_count': new_joiners_count,

            # Power Card 2: Live Clock & Attendance
            'currently_working': currently_working_count,
            'present_today': present_count,
            'absent_count': absent_count,
            'time_off_count': time_off_count,
            'attendance_percentage': attendance_pct,
            'recent_punches': recent_punches,

            # Power Card 3: Leave Applications
            'pending_leaves': pending_leaves_count,
            'on_leave_today': time_off_count,
            'approved_leaves_month': approved_leaves_month,

            # Power Card 4: Recruitment & Jobs
            'open_jobs': open_jobs_count,
            'total_applications': total_applications_count,
            'shortlisted_candidates': shortlisted_candidates_count,
            'interviewing_candidates': interviewing_candidates_count,
            'open_jobs_list': open_jobs_list,

            # Power Card 5: Face Biometrics
            'enrolled_biometrics': enrolled_biometrics_count,
            'pending_biometrics': pending_biometrics_count,
            'biometric_percentage': biometric_pct,

            # Power Card 6: Company Policies
            'total_policies_count': total_policies_count,
            'active_shift_start': active_shift_start,
            'active_shift_end': active_shift_end,
            'active_grace_minutes': active_grace_minutes,

            # Power Card 7: Company Assets
            'total_assets': total_assets,
            'assigned_assets': assigned_assets,
            'unassigned_assets': unassigned_assets,

            # Feed & lists
            'upcoming_birthdays': upcoming_birthdays,
            'all_employees_detailed': all_employees_detailed,
            'present_employees_detailed': present_employees,
            'absent_employees': absent_employees,
            'on_leave_employees': on_leave_employees,
        })
        template = 'hrms/dashboard_hr.html'

    else:
        # Standard Employee Dashboard Logic
        employee = get_employee_profile(user)
        if employee:
            curr_month = today.month
            curr_year = today.year
            first_weekday, num_days = calendar.monthrange(curr_year, curr_month)
            leading_empty_range = list(range(first_weekday))
            trailing_empty_count = (7 - ((first_weekday + num_days) % 7)) % 7
            trailing_empty_range = list(range(trailing_empty_count))
            month_days = [
                date(curr_year, curr_month, d) for d in range(1, num_days + 1)
            ]

            today_record = m.AttendanceRecord.objects.filter(
                employee=employee, attendance_date=today
            ).first()

            month_records = m.AttendanceRecord.objects.filter(
                employee=employee,
                attendance_date__range=[month_days[0], month_days[-1]]
            )
            rec_map = {r.attendance_date: r for r in month_records}

            app_leaves = m.LeaveApplication.objects.filter(
                employee=employee,
                status=m.LeaveApplication.Status.APPROVED,
                start_date__lte=month_days[-1],
                end_date__gte=month_days[0]
            ).select_related('leave_type')
            leave_day_map = {}
            for l in app_leaves:
                c = max(l.start_date, month_days[0])
                while c <= min(l.end_date, month_days[-1]):
                    leave_day_map[c] = l.leave_type.code
                    c += timedelta(days=1)

            holidays = m.Holiday.objects.filter(
                calendar=employee.holiday_calendar,
                date__range=[month_days[0], month_days[-1]]
            ) if employee.holiday_calendar else []
            holiday_map = {h.date: h.name for h in holidays}

            present_days = 0
            half_days = 0
            absent_days = 0
            calendar_cells = []

            for d in month_days:
                r = rec_map.get(d)
                l_code = leave_day_map.get(d)
                h_name = holiday_map.get(d)

                cell_status = 'absent'
                badge_color = 'danger'
                label = 'Absent'

                if r and r.check_in:
                    if r.status == m.AttendanceRecord.Status.PRESENT:
                        cell_status = 'present'
                        badge_color = 'success'
                        label = 'Present'
                        present_days += 1
                    elif r.status == m.AttendanceRecord.Status.HALF_DAY:
                        cell_status = 'half_day'
                        badge_color = 'warning'
                        label = 'Half Day'
                        half_days += 1
                elif l_code:
                    cell_status = 'leave'
                    badge_color = 'primary'
                    label = f'Leave ({l_code})'
                elif h_name:
                    cell_status = 'holiday'
                    badge_color = 'info'
                    label = f'Holiday: {h_name}'
                elif d.weekday() == 6:
                    cell_status = 'week_off'
                    badge_color = 'secondary'
                    label = 'Sunday Off'
                elif d > today:
                    cell_status = 'future'
                    badge_color = 'light text-muted'
                    label = 'Upcoming'
                else:
                    absent_days += 1

                calendar_cells.append({
                    'date': d,
                    'status': cell_status,
                    'badge_color': badge_color,
                    'label': label,
                    'record': r,
                    'is_past': (d <= today and d.weekday() != 6 and not h_name and not l_code),
                })

            working_days_so_far = max(1, len([
                c for c in calendar_cells
                if c['date'] <= today and c['status'] not in ('week_off', 'holiday')
            ]))
            att_pct = round(((present_days + 0.5 * half_days) / working_days_so_far) * 100)

            upcoming_holidays = (
                m.Holiday.objects.filter(
                    calendar=employee.holiday_calendar, date__gte=today
                ).order_by('date')[:4]
                if employee.holiday_calendar
                else []
            )

            leave_live = getattr(m, 'EmployeeLeaveBalanceLive', None)
            leave_live_obj = (
                leave_live.objects.filter(e_name=employee).first()
                if leave_live
                else None
            )

            context.update({
                'employee': employee,
                'today_record': today_record,
                'leave_balances': m.EmployeeLeaveBalance.objects.filter(e_name=employee).first(),
                'leave_live': leave_live_obj,
                'recent_attendance': month_records.order_by('-attendance_date')[:7],
                'recent_notices': m.CompanyNotice.objects.filter(
                    company=employee.company, is_active=True
                ).order_by('-notice_date')[:5],
                'calendar_cells': calendar_cells,
                'leading_empty_range': leading_empty_range,
                'trailing_empty_range': trailing_empty_range,
                'monthly_attendance_pct': min(100, att_pct),
                'present_days': present_days,
                'half_days': half_days,
                'absent_days': absent_days,
                'pending_leaves_count': m.LeaveApplication.objects.filter(
                    employee=employee,
                    status__in=['pending', 'pending_manager', 'pending_hr'],
                ).count(),
                'pending_regularizations_count': (
                    m.PunchRegularizationRequest.objects.filter(
                        employee=employee, status='pending'
                    ).count()
                    if hasattr(m, 'PunchRegularizationRequest')
                    else 0
                ),
                'upcoming_holidays': upcoming_holidays,
                'current_month_name': calendar.month_name[curr_month],
                'current_year': curr_year,
            })
        template = 'hrms/dashboard_employee.html'

    return render(request, template, context)


@login_required
def coming_soon(request, active_group='dashboard', active_item='', title='Coming soon'):
    """Placeholder for modules not yet built out. Sidebar links all resolve
    so the navigation is fully clickable while each module gets built."""
    return render(request, 'hrms/coming_soon.html', {
        'active_group': active_group,
        'active_item': active_item,
        'module_title': title,
    })


# ===========================================================================
# ORGANISATION STRUCTURE + EMPLOYEE MANAGEMENT — Generic Class-Based Views
# ===========================================================================
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View

from . import forms as f
from .permissions import HRRequiredMixin, SuperAdminRequiredMixin, EmployeeSelfOrHRMixin, get_employee_profile


class SidebarContextMixin:
    """Injects the active_group/active_item so the sidebar highlights correctly."""
    active_group = 'dashboard'
    active_item = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_group'] = self.active_group
        ctx['active_item'] = self.active_item
        return ctx


# ---------------------------------------------------------------------------
# Company (Org Settings) — Super Admin manages, HR can view
# ---------------------------------------------------------------------------
class CompanyListView(RBACViewMixin, SidebarContextMixin, ListView):
    model = m.Company
    template_name = 'hrms/org/company_list.html'
    context_object_name = 'companies'
    active_group, active_item = 'organisation', 'company'
    rbac_permission = "org.manage"

    def get_queryset(self):
        # Prefetch policy history to avoid N+1 database queries
        qs = m.Company.objects.prefetch_related('policy_history').order_by('name')
        today = date.today()

        for company in qs:
            # We attach a dynamic property 'active_policy' to each company object
            company.active_policy = company.get_policy_for_date(today)
        return qs


class CompanyUpdateView(SuperAdminRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Company
    form_class = f.CompanyForm
    template_name = 'hrms/org/company_form.html'
    success_url = reverse_lazy('hrms:company_list')
    active_group, active_item = 'organisation', 'company'

    def form_valid(self, form):
        messages.success(self.request, 'Company profile updated.')
        return super().form_valid(form)


class CompanyCreateView(SuperAdminRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Company
    form_class = f.CompanyForm
    template_name = 'hrms/org/company_form.html'
    success_url = reverse_lazy('hrms:company_list')
    active_group, active_item = 'organisation', 'company'

    def form_valid(self, form):
        messages.success(self.request, 'Company created.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Department — HR / Super Admin
# ---------------------------------------------------------------------------
class DepartmentListView(CompanyFilterMixin, HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.Department
    template_name = 'hrms/org/department_list.html'
    context_object_name = 'companies'
    active_group, active_item = 'organisation', 'department'
    paginate_by = 50

    def get_queryset(self):
        # Prefetching everything down to employees in one go
        return m.Company.objects.prefetch_related(
            'departments__designations__employees'
        ).all()


class DepartmentCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Department
    form_class = f.DepartmentForm
    template_name = 'hrms/org/department_form.html'
    success_url = reverse_lazy('hrms:department_list')
    active_group, active_item = 'organisation', 'department'

    def form_valid(self, form):
        messages.success(self.request, 'Department created.')
        return super().form_valid(form)


class DepartmentUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Department
    form_class = f.DepartmentForm
    template_name = 'hrms/org/department_form.html'
    success_url = reverse_lazy('hrms:department_list')
    active_group, active_item = 'organisation', 'department'

    def form_valid(self, form):
        messages.success(self.request, 'Department updated.')
        return super().form_valid(form)


class DepartmentDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.Department
    template_name = 'hrms/org/department_confirm_delete.html'
    success_url = reverse_lazy('hrms:department_list')
    active_group, active_item = 'organisation', 'department'

    def form_valid(self, form):
        messages.success(self.request, 'Department deleted.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Designation — HR / Super Admin
# ---------------------------------------------------------------------------
class DesignationListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.Designation
    template_name = 'hrms/org/designation_list.html'  # ensure this matches your path
    context_object_name = 'designations'
    active_group, active_item = 'organisation', 'designation'
    paginate_by = 30

    def get_queryset(self):
        # 1. Start with the base manager
        # 2. Use select_related for ForeignKeys (Company, Dept)
        # 3. Use prefetch_related for the Employee count logic
        # 4. CRITICAL: Put .order_by() HERE, before it leaves this function
        return m.Designation.objects.select_related(
            'company',
            'department'
        ).prefetch_related(
            'employees'
        ).order_by('company', 'department', 'level', 'title')


class DesignationCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Designation
    form_class = f.DesignationForm
    template_name = 'hrms/org/designation_form.html'
    success_url = reverse_lazy('hrms:designation_list')
    active_group, active_item = 'organisation', 'designation'

    def form_valid(self, form):
        messages.success(self.request, 'Designation created.')
        return super().form_valid(form)


class DesignationUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Designation
    form_class = f.DesignationForm
    template_name = 'hrms/org/designation_form.html'
    success_url = reverse_lazy('hrms:designation_list')
    active_group, active_item = 'organisation', 'designation'

    def form_valid(self, form):
        messages.success(self.request, 'Designation updated.')
        return super().form_valid(form)


class DesignationDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.Designation
    template_name = 'hrms/org/designation_confirm_delete.html'
    success_url = reverse_lazy('hrms:designation_list')
    active_group, active_item = 'organisation', 'designation'

    def form_valid(self, form):
        messages.success(self.request, 'Designation deleted.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Employee — HR/Admin manage all; Employee can view only their own profile
# ---------------------------------------------------------------------------
from django.db.models import Q, Count


class EmployeeListView(RBACViewMixin, CompanyFilterMixin, SidebarContextMixin, ListView):
    model = m.Employee
    template_name = 'hrms/employee/employee_list.html'
    context_object_name = 'employees'
    active_group, active_item = 'employee', 'employee_list'
    rbac_permission = "employee.manage"
    paginate_by = 65

    def get_queryset(self):
        # CompanyFilterMixin handles multi-tenancy / active_company_id filtering
        qs = super().get_queryset().select_related('company', 'department', 'designation')

        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()

        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q) |
                Q(employee_code__icontains=q) | Q(email__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)

        return qs.order_by('employee_code')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Base company-filtered employee queryset for stats (ignoring search filters & pagination)
        base_emp_qs = super().get_queryset()
        user = self.request.user
        active_company_id = self.request.session.get('active_company_id')

        # Calculate Department count based on active company
        if active_company_id and active_company_id != 'all':
            dept_count = m.Department.objects.filter(company_id=active_company_id).count()
        elif not user.is_superuser:
            profile = get_employee_profile(user)
            if profile:
                first_managed = profile.managed_companies.first() if hasattr(profile, 'managed_companies') else None
                locked = first_managed or profile.company
                dept_count = m.Department.objects.filter(company=locked).count() if locked else 0
            else:
                dept_count = 0
        else:
            dept_count = m.Department.objects.count()

        # Stats Cards Data (Reflects selected company)
        ctx['total_count'] = base_emp_qs.count()
        ctx['active_count'] = base_emp_qs.filter(status=m.Employee.Status.ACTIVE).count()
        ctx['dept_count'] = dept_count
        ctx['exited_count'] = base_emp_qs.filter(
            status__in=[m.Employee.Status.RELIEVED, m.Employee.Status.SUSPENDED]).count()
        ctx['leave_count'] = base_emp_qs.filter(status=m.Employee.Status.ON_LEAVE).count()

        ctx['q'] = self.request.GET.get('q', '')
        ctx['status'] = self.request.GET.get('status', '')
        ctx['status_choices'] = m.Employee.Status.choices
        return ctx


class EmployeeDetailView(EmployeeSelfOrHRMixin, SidebarContextMixin, DetailView):
    model = m.Employee
    template_name = 'hrms/employee/employee_detail.html'
    context_object_name = 'employee'
    active_group, active_item = 'employee', 'employee_list'

    def get_queryset(self):
        return m.Employee.objects.select_related(
            'company', 'department', 'designation', 'bank_detail', 'holiday_calendar'
        ).prefetch_related(
            'documents', 'notices', 'assigned_leaves', 'live_balances', 'salaries'
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        emp = self.object

        # 1. Fetch both Master Entitlement Bank and Live Wallet
        leave_bank = m.EmployeeLeaveBalance.objects.filter(e_name=emp).first()
        leave_live = m.EmployeeLeaveBalanceLive.objects.filter(e_name=emp).first()
        ctx['leave_bank'] = leave_bank
        ctx['leave_live'] = leave_live
        ctx['recent_leaves'] = emp.leave_applications.select_related('leave_type').order_by('-applied_on')[:10]

        # 2. Check if Live Wallet has been initialized (total > 0).
        # If live wallet is uninitialized/zero but bank has leaves, fallback to bank.
        live_total = sum([
            getattr(leave_live, 'casual_leave', 0.0) or 0.0,
            getattr(leave_live, 'sick_leave', 0.0) or 0.0,
            getattr(leave_live, 'earned_leave', 0.0) or 0.0,
            getattr(leave_live, 'menstrual_leave', 0.0) or 0.0,
            getattr(leave_live, 'bereavement_leave', 0.0) or 0.0,
            getattr(leave_live, 'comp_off', 0.0) or 0.0,
        ]) if leave_live else 0.0

        source_bal = leave_live if (leave_live and live_total > 0) else leave_bank

        if source_bal:
            ctx['leave_map'] = {
                'CL': getattr(source_bal, 'casual_leave', 0.0) or 0.0,
                'SL': getattr(source_bal, 'sick_leave', 0.0) or 0.0,
                'EL': getattr(source_bal, 'earned_leave', 0.0) or 0.0,
                'ML': getattr(source_bal, 'menstrual_leave', 0.0) or 0.0,
                'COMP_OFF': getattr(source_bal, 'comp_off', 0.0) or 0.0,
                'BL': getattr(source_bal, 'bereavement_leave', 0.0) or 0.0,
            }
        else:
            ctx['leave_map'] = {}

        # 3. Assets & Custody
        ctx['assigned_assets'] = m.Asset.objects.filter(employee=emp).select_related('category')
        ctx['asset_history'] = m.AssetAssignmentHistory.objects.filter(
            employee=emp
        ).select_related('asset').order_by('-assigned_date')

        # 4. Attendance & Penalties Summary
        today = timezone.localdate()
        ctx['recent_attendance'] = emp.attendance_records.order_by('-attendance_date')[:15]
        ctx['recent_penalties'] = emp.penalties.order_by('-penalty_date')[:10]
        ctx['grace_usage'] = m.GraceUsageTracker.objects.filter(
            employee=emp, month=today.month, year=today.year
        ).first()

        # 5. Performance Reviews
        ctx['performance_reviews'] = emp.performance_reviews.select_related('reviewer').order_by('-review_date')

        # 6. Salary & Documents
        ctx['current_salary'] = emp.salaries.filter(is_active=True).first()
        ctx['documents'] = emp.documents.all()

        # 7. Role check
        ctx['is_hr'] = is_hr_or_above(self.request.user)

        return ctx


class MyProfileView(LoginRequiredMixin, SidebarContextMixin, View):
    """Employee's own profile - resolves the Employee linked to the logged-in user
    and reuses the detail template (read-only for the employee)."""
    active_group, active_item = 'employee', 'my_profile'

    def get(self, request, *args, **kwargs):
        employee = get_employee_profile(request.user)
        if employee is None:
            messages.warning(request, "Your login isn't linked to an employee record yet. Contact HR.")
            return redirect('hrms:dashboard')
        return redirect('hrms:employee_detail', pk=employee.pk)


import re
from django.db.models import Max


class EmployeeCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Employee
    form_class = f.EmployeeForm
    template_name = 'hrms/employee/employee_form.html'
    active_group, active_item = 'employee', 'employee_add'

    def get_initial(self):
        initial = super().get_initial()

        # Look for existing codes in the standard format (OHCE + 4 digits)
        standard_codes = m.Employee.objects.filter(
            employee_code__regex=r'^OHCE\d{4}$'
        ).values_list('employee_code', flat=True)

        if standard_codes:
            # Extract numbers and find the next one
            numbers = [int(code[4:]) for code in standard_codes if code[4:].isdigit()]
            next_num = max(numbers) + 1 if numbers else 1
        else:
            next_num = 1

        # This will now correctly show OHCE0099 on the page
        initial['employee_code'] = f'OHCE{next_num:04d}'
        return initial

    def form_valid(self, form):
        messages.success(self.request, 'Employee added.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:employee_detail', kwargs={'pk': self.object.pk})


class EmployeeUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Employee
    form_class = f.EmployeeForm
    template_name = 'hrms/employee/employee_form.html'
    active_group, active_item = 'employee', 'employee_list'

    def form_valid(self, form):
        messages.success(self.request, 'Employee updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:employee_detail', kwargs={'pk': self.object.pk})


class EmployeeDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.Employee
    template_name = 'hrms/employee/employee_confirm_delete.html'
    success_url = reverse_lazy('hrms:employee_list')
    active_group, active_item = 'employee', 'employee_list'

    def form_valid(self, form):
        messages.success(self.request, 'Employee removed.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Employee sub-records: Bank Detail (1:1), Documents, Notices
# ---------------------------------------------------------------------------
class EmployeeBankDetailUpdateView(HRRequiredMixin, SidebarContextMixin, View):
    """Create-or-update since it's a OneToOne — one form handles both."""
    active_group, active_item = 'employee', 'employee_list'
    template_name = 'hrms/employee/bankdetail_form.html'

    def _get_employee(self, pk):
        return get_object_or_404(m.Employee, pk=pk)

    def get(self, request, pk):
        employee = self._get_employee(pk)
        instance = getattr(employee, 'bank_detail', None)
        form = f.EmployeeBankDetailForm(instance=instance)
        return render(request, self.template_name, {
            'form': form, 'employee': employee,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        employee = self._get_employee(pk)
        instance = getattr(employee, 'bank_detail', None)
        form = f.EmployeeBankDetailForm(request.POST, instance=instance)
        if form.is_valid():
            bank_detail = form.save(commit=False)
            bank_detail.employee = employee
            bank_detail.save()
            messages.success(request, 'Bank details saved.')
            return redirect('hrms:employee_detail', pk=employee.pk)
        return render(request, self.template_name, {
            'form': form, 'employee': employee,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


class EmployeeDocumentCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.EmployeeDocument
    form_class = f.EmployeeDocumentForm
    template_name = 'hrms/employee/document_form.html'
    active_group, active_item = 'employee', 'employee_list'

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_object_or_404(m.Employee, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['employee'] = self.employee
        return ctx

    def form_valid(self, form):
        form.instance.employee = self.employee
        messages.success(self.request, 'Document uploaded.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:employee_detail', kwargs={'pk': self.employee.pk})


class EmployeeDocumentDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.EmployeeDocument
    template_name = 'hrms/employee/document_confirm_delete.html'
    active_group, active_item = 'employee', 'employee_list'

    def get_success_url(self):
        messages.success(self.request, 'Document deleted.')
        return reverse('hrms:employee_detail', kwargs={'pk': self.object.employee.pk})


class MyDocumentsView(LoginRequiredMixin, SidebarContextMixin, ListView):
    """Employee's own documents, read-only."""
    model = m.EmployeeDocument
    template_name = 'hrms/employee/my_documents.html'
    context_object_name = 'documents'
    active_group, active_item = 'employee', 'my_documents'

    def get_queryset(self):
        employee = get_employee_profile(self.request.user)
        if employee is None:
            return m.EmployeeDocument.objects.none()
        return employee.documents.all()


class EmployeeDocumentBulkUploadView(LoginRequiredMixin, SidebarContextMixin, View):
    """
    Multi-document sequential upload workflow.
    Allows staging and uploading multiple documents simultaneously.
    """
    template_name = 'hrms/employee/employee_document_bulk_upload.html'
    active_group, active_item = 'employee', 'my_documents'

    def get_employee(self, pk=None):
        if pk:
            if not is_hr_or_above(self.request.user):
                raise PermissionDenied("Only HR can upload documents for other employees.")
            return get_object_or_404(m.Employee, pk=pk)
        emp = get_employee_profile(self.request.user)
        if not emp:
            raise PermissionDenied("No employee profile found for your login.")
        return emp

    def get(self, request, pk=None):
        employee = self.get_employee(pk)
        return render(request, self.template_name, {
            'employee': employee,
            'doc_types': m.EmployeeDocument.DocumentType.choices,
            'active_group': self.active_group,
            'active_item': self.active_item,
        })

    def post(self, request, pk=None):
        employee = self.get_employee(pk)
        files = request.FILES
        post_data = request.POST

        row_indices = set()
        for k in post_data.keys():
            if k.startswith('doc_type_'):
                row_indices.add(k.replace('doc_type_', ''))

        uploaded_count = 0
        with transaction.atomic():
            for idx in sorted(list(row_indices), key=lambda x: int(x) if x.isdigit() else str(x)):
                doc_type = post_data.get(f'doc_type_{idx}')
                doc_file = files.get(f'doc_file_{idx}')
                doc_desc = post_data.get(f'doc_desc_{idx}', '').strip()
                doc_expiry_raw = post_data.get(f'doc_expiry_{idx}', '').strip()

                if not doc_type or not doc_file:
                    continue

                expiry_date = None
                if doc_expiry_raw:
                    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
                        try:
                            expiry_date = datetime.strptime(doc_expiry_raw, fmt).date()
                            break
                        except ValueError:
                            pass

                m.EmployeeDocument.objects.create(
                    employee=employee,
                    document_type=doc_type,
                    file=doc_file,
                    description=doc_desc,
                    expiry_on=expiry_date,
                    verification_status=m.EmployeeDocument.VerificationStatus.PENDING
                )
                uploaded_count += 1

        if uploaded_count > 0:
            messages.success(request,
                             f"Successfully uploaded {uploaded_count} document(s). Status set to Pending Verification.")
        else:
            messages.warning(request, "No valid document files were selected.")

        if pk and is_hr_or_above(request.user):
            return redirect('hrms:employee_detail', pk=employee.pk)
        return redirect('hrms:my_documents')


class EmployeeDocumentVerifyView(HRRequiredMixin, View):
    """HR verification endpoint for employee documents."""

    def post(self, request, pk):
        doc = get_object_or_404(m.EmployeeDocument, pk=pk)
        action = request.POST.get('status')
        remarks = request.POST.get('rejection_remarks', '').strip()

        if action == 'verified':
            doc.verification_status = m.EmployeeDocument.VerificationStatus.VERIFIED
            doc.verified_by = request.user
            doc.verified_on = timezone.now()
            doc.rejection_remarks = ''
            doc.save()
            messages.success(request, f"Document '{doc.get_document_type_display()}' verified successfully.")
        elif action == 'rejected':
            if not remarks:
                messages.error(request, "A mandatory reason is required to reject a document.")
                return redirect('hrms:employee_detail', pk=doc.employee.pk)
            doc.verification_status = m.EmployeeDocument.VerificationStatus.REJECTED
            doc.verified_by = request.user
            doc.verified_on = timezone.now()
            doc.rejection_remarks = remarks
            doc.save()
            messages.warning(request, f"Document '{doc.get_document_type_display()}' rejected.")

        return redirect('hrms:employee_detail', pk=doc.employee.pk)


class EmployeeNoticeCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.EmployeeNotice
    form_class = f.EmployeeNoticeForm
    template_name = 'hrms/employee/notice_form.html'
    active_group, active_item = 'employee', 'employee_list'

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_object_or_404(m.Employee, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['employee'] = self.employee
        return ctx

    def form_valid(self, form):
        form.instance.employee = self.employee
        messages.success(self.request, 'Notice added.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:employee_detail', kwargs={'pk': self.employee.pk})


class EmployeeNoticeDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.EmployeeNotice
    template_name = 'hrms/employee/notice_confirm_delete.html'
    active_group, active_item = 'employee', 'employee_list'

    def get_success_url(self):
        messages.success(self.request, 'Notice deleted.')
        return reverse('hrms:employee_detail', kwargs={'pk': self.object.employee.pk})


# ===========================================================================
# ATTENDANCE & GRACE POLICY
# ===========================================================================
from datetime import date as _date

from . import attendance_logic as attn


# ---------------------------------------------------------------------------
# Attendance Policy — one per company, HR/Admin manage (create-or-update pattern)
# ---------------------------------------------------------------------------
class AttendancePolicyListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.Company
    template_name = 'hrms/attendance/policy_list.html'
    context_object_name = 'companies'
    active_group, active_item = 'attendance', 'attendance_policy'

    def get_queryset(self):
        # REMOVE .select_related('attendance_policy') from here
        return m.Company.objects.all()

    def get_queryset(self):
        qs = m.Company.objects.prefetch_related('policy_history').all()
        today = date.today()

        for company in qs:
            company.active_policy = company.get_policy_for_date(today)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # ---  THIS LINE to show all new n old policies  ---
        ctx['policy_versions'] = m.AttendancePolicy.objects.select_related('company').order_by('-effective_from')
        return ctx


class AttendancePolicyFormView(HRRequiredMixin, SidebarContextMixin, View):
    active_group, active_item = 'attendance', 'attendance_policy'
    template_name = 'hrms/attendance/policy_form.html'

    def get(self, request, pk):
        company = get_object_or_404(m.Company, pk=pk)
        instance = getattr(company, 'attendance_policy', None)
        form = f.AttendancePolicyForm(instance=instance)
        return render(request, self.template_name, {
            'form': form, 'company': company,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        company = get_object_or_404(m.Company, pk=pk)
        instance = getattr(company, 'attendance_policy', None)
        form = f.AttendancePolicyForm(request.POST, instance=instance)
        if form.is_valid():
            policy = form.save(commit=False)
            policy.company = company
            policy.save()
            messages.success(request, f'Attendance policy saved for {company}.')
            return redirect('hrms:attendance_policy')
        return render(request, self.template_name, {
            'form': form, 'company': company,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


class AttendancePolicyCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    """View to manually add a new timing version (e.g. for August)."""
    model = m.AttendancePolicy
    form_class = f.AttendancePolicyForm
    template_name = 'hrms/attendance/policy_form.html'
    success_url = reverse_lazy('hrms:attendance_policy')
    active_group, active_item = 'attendance', 'attendance_policy'

    def form_valid(self, form):
        messages.success(self.request, 'New timing version added successfully.')
        return super().form_valid(form)


class AttendancePolicyUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    """View to edit an existing historical timing version."""
    model = m.AttendancePolicy
    form_class = f.AttendancePolicyForm
    template_name = 'hrms/attendance/policy_form.html'
    success_url = reverse_lazy('hrms:attendance_policy')
    active_group, active_item = 'attendance', 'attendance_policy'

    def form_valid(self, form):
        messages.success(self.request, 'Timing version updated successfully.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Attendance Records — HR sees/manages all; employee sees + checks in/out on own
# ---------------------------------------------------------------------------


class AttendanceRecordListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.AttendanceRecord
    template_name = 'hrms/attendance/record_list.html'
    context_object_name = 'records'
    active_group, active_item = 'attendance', 'attendance_records'

    def post(self, request, *args, **kwargs):
        """Logic for manual Admin edits of punches."""
        if not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        try:
            data = json.loads(request.body)
            updates = data.get('updates', [])
            tz = timezone.get_current_timezone()
            emp_ids = list(set([item.get('emp_id') for item in updates]))
            employee_cache = {str(e.id): e for e in m.Employee.objects.select_related('company').filter(id__in=emp_ids)}

            with transaction.atomic():
                for item in updates:
                    emp_id, date_str = str(item.get('emp_id')), item.get('date')
                    in_t, out_t = item.get('in_time'), item.get('out_time')
                    if not in_t or emp_id not in employee_cache: continue

                    emp = employee_cache[emp_id]
                    comp = emp.company
                    att_date = datetime.strptime(date_str, '%Y-%m-%d').date()

                    check_in_dt = timezone.make_aware(datetime.strptime(f"{date_str} {in_t}", '%Y-%m-%d %H:%M'), tz)
                    check_out_dt = None
                    if out_t:
                        check_out_dt = timezone.make_aware(datetime.strptime(f"{date_str} {out_t}", '%Y-%m-%d %H:%M'),
                                                           tz)

                    # Rules Logic
                    m.AttendanceRecord.objects.update_or_create(
                        employee_id=emp_id, attendance_date=att_date,
                        defaults={'check_in': check_in_dt, 'check_out': check_out_dt}
                    )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    def get_context_data(self, **kwargs):
        """The final corrected Smart Brain for the report."""
        ctx = super().get_context_data(**kwargs)
        tz = timezone.get_current_timezone()

        # 1. Date Range Handling
        d_from, d_to = self.request.GET.get('from'), self.request.GET.get('to')
        start_date = datetime.strptime(d_from, '%Y-%m-%d').date() if d_from else timezone.now().date().replace(day=1)
        if d_to:
            end_date = datetime.strptime(d_to, '%Y-%m-%d').date()
        else:
            next_m = start_date.replace(day=28) + timedelta(days=4)
            end_date = next_m - timedelta(days=next_m.day)
        date_list = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]

        # 2. Fetch Data
        emp_filter = self.request.GET.get('employee')
        employees = m.Employee.objects.select_related('company', 'department').all().order_by('employee_code')
        if emp_filter: employees = employees.filter(id=emp_filter)

        # Order by date is CRITICAL for sequential G1, G2, G3
        records = m.AttendanceRecord.objects.filter(attendance_date__range=[start_date, end_date]).order_by(
            'attendance_date')
        leaves = m.LeaveApplication.objects.filter(status='approved', start_date__lte=end_date,
                                                   end_date__gte=start_date).select_related('leave_type')

        att_lookup = collections.defaultdict(dict)
        for r in records: att_lookup[r.employee_id][r.attendance_date] = r

        leave_lookup = collections.defaultdict(dict)
        for l in leaves:
            curr = max(l.start_date, start_date)
            while curr <= min(l.end_date, end_date):
                leave_lookup[l.employee_id][curr] = l.leave_type.code
                curr += timedelta(days=1)

        # 3. Build Matrix
        attendance_matrix = collections.defaultdict(dict)
        grace_counters = collections.defaultdict(int)

        for emp in employees:
            comp = emp.company
            # off_start, off_end = comp.office_start_time, comp.office_end_time
            grace_limit, grace_min = comp.grace_allowed_count, comp.grace_minutes

            for day in date_list:
                # --- SAHI LOGIC: Loop ke andar har din ki policy fetch karo ---
                active_rule = emp.company.get_policy_for_date(day)
                off_start = active_rule.office_start_time
                off_end = active_rule.office_end_time
                grace_mins = active_rule.grace_minutes
                grace_limit = active_rule.grace_allowed_count
                rec = att_lookup[emp.id].get(day)
                leave_code = leave_lookup[emp.id].get(day)
                display_obj = {'status': 'absent', 'display_label': '', 'check_in': None, 'check_out': None}

                if rec:
                    display_obj['check_in'] = rec.check_in
                    display_obj['check_out'] = rec.check_out
                    local_in = timezone.localtime(rec.check_in) if rec.check_in else None
                    local_out = timezone.localtime(rec.check_out) if rec.check_out else None

                    if local_in:
                        p_in = local_in.time()
                        p_out = local_out.time() if local_out else off_start

                        # Grace deadline (e.g., 10:15)
                        grace_deadline = (datetime.combine(day, off_start) + timedelta(minutes=grace_min)).time()
                        # Strictly checked: Did they stay until office end (6:00 PM)?
                        stayed_until_end = p_out >= off_end

                        # --- REFINED LOGIC GATEWAY ---

                        # Case 1: Arrived On Time (<= 10:00)
                        if p_in <= off_start:
                            # Must stay until 6:00 PM to get FD
                            display_obj['status'] = 'present' if stayed_until_end else 'half_day'

                        # Case 2: Within Grace Window (10:01 - 10:15)
                        elif p_in <= grace_deadline:
                            if stayed_until_end:
                                m_key = (emp.id, day.month)
                                if grace_counters[m_key] < grace_limit:
                                    grace_counters[m_key] += 1
                                    display_obj['status'] = 'present'
                                    display_obj['display_label'] = f"G{grace_counters[m_key]}"
                                else:
                                    display_obj['status'] = 'half_day'
                                    display_obj['display_label'] = "Grace Exhausted"
                            else:
                                # Within window but left early -> HD (Grace is not used/wasted)
                                display_obj['status'] = 'half_day'

                        # Case 3: Late Arrival (> 10:15)
                        else:
                            display_obj['status'] = 'half_day'

                elif leave_code:
                    display_obj['status'] = 'on_leave'
                    display_obj['display_label'] = leave_code

                attendance_matrix[emp.id][day] = display_obj

        ctx.update({'employees': employees, 'date_list': date_list, 'attendance_matrix': attendance_matrix,
                    'filters': self.request.GET, 'start_date': start_date, 'end_date': end_date})
        return ctx


from django.utils import timezone
from datetime import datetime, timedelta, time, date
import collections
import json
from django.http import JsonResponse
from django.db import transaction


class AttendanceRecordListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.AttendanceRecord
    template_name = 'hrms/attendance/record_list.html'
    context_object_name = 'records'
    active_group, active_item = 'attendance', 'attendance_records'

    def post(self, request, *args, **kwargs):
        """Logic for manual Admin edits of punches."""
        if not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        try:
            data = json.loads(request.body)
            updates = data.get('updates', [])
            tz = timezone.get_current_timezone()

            with transaction.atomic():
                for item in updates:
                    emp_id, date_str = str(item.get('emp_id')), item.get('date')
                    in_t, out_t = item.get('in_time'), item.get('out_time')
                    if not in_t: continue

                    att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    check_in_dt = timezone.make_aware(datetime.strptime(f"{date_str} {in_t}", '%Y-%m-%d %H:%M'), tz)
                    check_out_dt = None
                    if out_t:
                        check_out_dt = timezone.make_aware(datetime.strptime(f"{date_str} {out_t}", '%Y-%m-%d %H:%M'),
                                                           tz)

                    # We save the raw punches. Dynamic logic in get_context_data handles the display.
                    m.AttendanceRecord.objects.update_or_create(
                        employee_id=emp_id, attendance_date=att_date,
                        defaults={'check_in': check_in_dt, 'check_out': check_out_dt}
                    )
                    # After update_or_create for AttendanceRecord:
                    rec, created = m.AttendanceRecord.objects.update_or_create(
                        employee_id=emp_id, attendance_date=att_date,
                        defaults={'check_in': check_in_dt, 'check_out': check_out_dt}
                    )

            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    def post(self, request, *args, **kwargs):
        """Logic for manual Admin edits of punches with automatic Leave Balance Reset."""
        if not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        try:
            data = json.loads(request.body)
            updates = data.get('updates', [])
            tz = timezone.get_current_timezone()

            with transaction.atomic():
                for item in updates:
                    emp_id = str(item.get('emp_id'))
                    # NEW CHECK: Verify employee is active before saving punch
                    is_active = m.Employee.objects.filter(id=emp_id, status=m.Employee.Status.ACTIVE).exists()
                    if not is_active:
                        continue  # Skip this record if employee is inactive

                    date_str = item.get('date')
                    in_t, out_t = item.get('in_time'), item.get('out_time')
                    if not in_t: continue

                    att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    check_in_dt = timezone.make_aware(datetime.strptime(f"{date_str} {in_t}", '%Y-%m-%d %H:%M'), tz)

                    check_out_dt = None
                    if out_t:
                        check_out_dt = timezone.make_aware(datetime.strptime(f"{date_str} {out_t}", '%Y-%m-%d %H:%M'),
                                                           tz)

                    # 1. Save the Attendance Record
                    rec, created = m.AttendanceRecord.objects.update_or_create(
                        employee_id=emp_id, attendance_date=att_date,
                        defaults={'check_in': check_in_dt, 'check_out': check_out_dt}
                    )

                    # 2. DYNAMIC CALCULATION FOR REFUND
                    # We determine if this manual entry is FD or HD to know how much to refund
                    employee = rec.employee
                    rule = employee.company.get_policy_for_date(att_date)

                    p_in = timezone.localtime(check_in_dt).time()
                    p_out = timezone.localtime(check_out_dt).time() if check_out_dt else rule.office_start_time

                    # Determine Punch Status
                    is_fd = p_in <= rule.office_start_time and (p_out >= rule.office_end_time)
                    # If not FD but has check-in, it's at least a HD
                    punch_status = 'FD' if is_fd else 'HD'

                    # 3. TRIGGER LEAVE REFUND LOGIC
                    # Look for an approved leave application for this date
                    leave_app = m.LeaveApplication.objects.filter(
                        employee=employee,
                        status=m.LeaveApplication.Status.APPROVED,
                        start_date__lte=att_date,
                        end_date__gte=att_date
                    ).first()

                    if leave_app:
                        # Determine refund amount
                        # If Full Day punch -> refund full day leave (1.0 or 0.5)
                        # If Half Day punch -> refund only 0.5
                        refund_val = 0
                        if punch_status == 'FD':
                            refund_val = 0.5 if leave_app.day_type == 'half' else 1.0
                        else:  # HD Punch
                            refund_val = 0.5

                        # Update Leave Bank (EmployeeLeaveBalance table)
                        mapping = {
                            'EL': 'earned_leave', 'SL': 'sick_leave', 'CL': 'casual_leave',
                            'MTL': 'menstrual_leave', 'BL': 'bereavement_leave', 'CO': 'comp_off',
                        }
                        field_name = mapping.get(leave_app.leave_type.code.upper())

                        if field_name:
                            bank, _ = m.EmployeeLeaveBalance.objects.get_or_create(e_name=employee)
                            current_val = getattr(bank, field_name)
                            # We ADD back the refund_val to the balance
                            setattr(bank, field_name, float(current_val) + float(refund_val))
                            bank.save()

                            # Mark Attendance Record status as FD/HD instead of ON_LEAVE
                            rec.status = 'present' if punch_status == 'FD' else 'half_day'
                            rec.remarks = f"Refunded {refund_val} to {leave_app.leave_type.code}"
                            rec.save()

            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tz = timezone.get_current_timezone()
        today = timezone.now().date()

        # 1. Date Range Handling
        d_from, d_to = self.request.GET.get('from'), self.request.GET.get('to')
        start_date = datetime.strptime(d_from, '%Y-%m-%d').date() if d_from else timezone.now().date().replace(day=1)
        if d_to:
            end_date = datetime.strptime(d_to, '%Y-%m-%d').date()
        else:
            next_m = start_date.replace(day=28) + timedelta(days=4)
            end_date = next_m - timedelta(days=next_m.day)
        date_list = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]

        # 2. Fetch ACTIVE Employees (Prefetch holiday_calendar for speed)
        emp_filter = self.request.GET.get('employee')
        employees = m.Employee.objects.select_related('company', 'department', 'holiday_calendar').filter(
            status=m.Employee.Status.ACTIVE
        ).order_by('employee_code')
        if emp_filter: employees = employees.filter(id=emp_filter)

        # 3. PRE-FETCH DATA FOR OPTIMIZATION
        records = m.AttendanceRecord.objects.filter(attendance_date__range=[start_date, end_date])
        leaves = m.LeaveApplication.objects.filter(
            status=m.LeaveApplication.Status.APPROVED,
            start_date__lte=end_date, end_date__gte=start_date
        ).select_related('leave_type')

        # NEW: Fetch Holidays across ALL calendars for the range
        holidays = m.Holiday.objects.filter(date__range=[start_date, end_date])

        # Create Lookups
        att_lookup = collections.defaultdict(dict)
        for r in records: att_lookup[r.employee_id][r.attendance_date] = r

        leave_lookup = collections.defaultdict(dict)
        for l in leaves:
            curr = max(l.start_date, start_date);
            while curr <= min(l.end_date, end_date):
                leave_lookup[l.employee_id][curr] = l.leave_type.code
                curr += timedelta(days=1)

        # NEW: Holiday Lookup {Calendar_ID: {Date: Holiday_Name}}
        holiday_lookup = collections.defaultdict(dict)
        for h in holidays:
            holiday_lookup[h.calendar_id][h.date] = h.name

        # 4. Build Smart Matrix
        attendance_matrix = collections.defaultdict(dict)

        for emp in employees:
            grace_counters = collections.defaultdict(int)

            for day in date_list:
                active_rule = emp.company.get_policy_for_date(day)
                off_start, off_end = active_rule.office_start_time, active_rule.office_end_time
                grace_mins, grace_limit = active_rule.grace_minutes, active_rule.grace_allowed_count

                rec = att_lookup[emp.id].get(day)
                leave_code = leave_lookup[emp.id].get(day)

                # Check for holiday based on Employee's SPECIFIC calendar
                holiday_name = None
                if emp.holiday_calendar_id:
                    holiday_name = holiday_lookup[emp.holiday_calendar_id].get(day)

                display_obj = {'status': '', 'display_label': '', 'check_in': None, 'check_out': None}

                if rec and rec.check_in:
                    # --- PUNCH LOGIC ---
                    display_obj['check_in'], display_obj['check_out'] = rec.check_in, rec.check_out
                    l_in = timezone.localtime(rec.check_in)
                    l_out = timezone.localtime(rec.check_out) if rec.check_out else None
                    p_in = l_in.time()
                    p_out = l_out.time() if l_out else off_start
                    grace_deadline = (datetime.combine(day, off_start) + timedelta(minutes=grace_mins)).time()
                    stayed_until_end = p_out >= off_end

                    if p_in <= off_start:
                        display_obj['status'] = 'present' if stayed_until_end else 'half_day'
                    elif p_in <= grace_deadline:
                        if stayed_until_end:
                            m_key = (emp.id, day.month)
                            if grace_counters[m_key] < grace_limit:
                                grace_counters[m_key] += 1
                                display_obj['status'], display_obj[
                                    'display_label'] = 'present', f"G{grace_counters[m_key]}"
                            else:
                                display_obj['status'], display_obj['display_label'] = 'half_day', "Grace Exhausted"
                        else:
                            display_obj['status'] = 'half_day'
                    else:
                        display_obj['status'] = 'half_day'

                    if display_obj['status'] == 'half_day' and leave_code:
                        display_obj['display_label'] = f"HD + {leave_code}"

                elif leave_code:
                    display_obj['status'], display_obj['display_label'] = 'on_leave', leave_code

                elif holiday_name:
                    # NEW: Mark as holiday based on their regional template
                    display_obj['status'], display_obj['display_label'] = 'holiday', holiday_name

                else:
                    # DEFAULT FALLBACKS
                    if day > today:
                        display_obj['status'] = 'future'
                    elif day.weekday() == 6:  # Sunday
                        display_obj['status'] = 'sunday'
                    else:
                        display_obj['status'] = 'absent'

                attendance_matrix[emp.id][day] = display_obj

        ctx.update({
            'employees': employees, 'date_list': date_list,
            'attendance_matrix': attendance_matrix, 'filters': self.request.GET,
            'start_date': start_date, 'end_date': end_date
        })
        return ctx

    def get_context_data(self, **kwargs):
        """Unified with attendance_engine — same logic as Attendance Matrix."""
        from .attendance_engine import build_attendance
        ctx = super().get_context_data(**kwargs)
        today = timezone.now().date()

        # 1. Date Range Handling
        d_from, d_to = self.request.GET.get('from'), self.request.GET.get('to')
        start_date = datetime.strptime(d_from, '%Y-%m-%d').date() if d_from else today.replace(day=1)
        if d_to:
            end_date = datetime.strptime(d_to, '%Y-%m-%d').date()
        else:
            next_m = start_date.replace(day=28) + timedelta(days=4)
            end_date = next_m - timedelta(days=next_m.day)
        date_list = [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]

        # 2. Fetch ACTIVE Employees
        emp_filter = self.request.GET.get('employee')
        employees = m.Employee.objects.select_related('company', 'department', 'holiday_calendar').filter(
            status=m.Employee.Status.ACTIVE
        ).order_by('employee_code')
        if emp_filter:
            employees = employees.filter(id=emp_filter)
        employees = list(employees)

        # 3. Delegate to engine
        engine_data = build_attendance(employees, start_date, end_date, today=today)

        # 4. Build rows for template (same key names as before so the template still works)
        attendance_matrix = collections.defaultdict(dict)
        for emp in employees:
            by_date = engine_data[emp.id]['by_date']
            for day, d in by_date.items():
                attendance_matrix[emp.id][day] = {
                    'status': d['status'],
                    'display_label': d['badge'],
                    'check_in': d['check_in'],
                    'check_out': d['check_out'],
                    # extra keys for the richer template
                    'label': d['label'],
                    'badge_class': d['badge_class'],
                    'css': d['css'],
                    'is_edited': d['is_edited'],
                    'edit_tooltip': d['edit_tooltip'],
                    'is_auto_leave': d['is_auto_leave'],
                    'leave': d['leave'],
                    'penalties': d['penalties'],
                    'penalty_amount': d['penalty_amount'],
                }

        ctx.update({
            'employees': employees, 'date_list': date_list,
            'attendance_matrix': attendance_matrix, 'filters': self.request.GET,
            'start_date': start_date, 'end_date': end_date,
        })
        return ctx


import json
import collections
from datetime import datetime, time, timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import ListView
# Assuming your models are imported as m
from hrms import models as m

from django.shortcuts import render
from django.views.generic import ListView
from datetime import timedelta, date
from . import models as m


class AttendanceRecordCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.AttendanceRecord
    form_class = f.AttendanceRecordForm
    template_name = 'hrms/attendance/record_form.html'
    success_url = reverse_lazy('hrms:attendance_records')
    active_group, active_item = 'attendance', 'attendance_records'

    def form_valid(self, form):
        record = form.save(commit=False)
        # If a check-in time was given and no check-out yet, apply the same
        # 3-strike grace rules HR would get from a real self-service check-in
        # (e.g. importing a biometric punch). HR's explicit status choice is
        # only overridden when it was left at the default (Present).
        if record.check_in and not record.check_out:
            policy = attn.get_policy_for_employee(record.employee)
            if policy and record.status == m.AttendanceRecord.Status.PRESENT:
                attn.evaluate_arrival(record, policy)
        record.save()
        self.object = record
        messages.success(self.request, 'Attendance record added.')
        return redirect(self.get_success_url())


class AttendanceRecordUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.AttendanceRecord
    form_class = f.AttendanceRecordForm
    template_name = 'hrms/attendance/record_form.html'
    success_url = reverse_lazy('hrms:attendance_records')
    active_group, active_item = 'attendance', 'attendance_records'

    def form_valid(self, form):
        messages.success(self.request, 'Attendance record updated.')
        return super().form_valid(form)


class MyAttendanceView(LoginRequiredMixin, SidebarContextMixin, TemplateView):
    """Employee's own attendance history + today's check-in/out card with month-wise reporting."""
    template_name = 'hrms/attendance/my_attendance.html'
    active_group, active_item = 'attendance', 'my_attendance'

    def get_employee(self):
        return get_employee_profile(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        employee = self.get_employee()
        ctx['employee'] = employee

        today = timezone.localdate()
        ctx['today'] = today

        # 1. Parse Month & Year for month-wise report (defaults to current month)
        try:
            sel_month = int(self.request.GET.get('month', today.month))
            if not (1 <= sel_month <= 12):
                sel_month = today.month
        except (ValueError, TypeError):
            sel_month = today.month

        try:
            sel_year = int(self.request.GET.get('year', today.year))
            if not (2000 <= sel_year <= 2100):
                sel_year = today.year
        except (ValueError, TypeError):
            sel_year = today.year

        sort_order = self.request.GET.get('order', 'desc').lower()
        if sort_order not in ('asc', 'desc'):
            sort_order = 'desc'

        # Prev / Next month navigation
        if sel_month == 1:
            prev_month, prev_year = 12, sel_year - 1
        else:
            prev_month, prev_year = sel_month - 1, sel_year

        if sel_month == 12:
            next_month, next_year = 1, sel_year + 1
        else:
            next_month, next_year = sel_month + 1, sel_year

        months_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
        min_y = min(today.year - 3, sel_year)
        max_y = max(today.year + 1, sel_year)
        year_choices = list(range(min_y, max_y + 1))

        ctx.update({
            'sel_month': sel_month,
            'sel_year': sel_year,
            'month_name': calendar.month_name[sel_month],
            'prev_month': prev_month,
            'prev_year': prev_year,
            'next_month': next_month,
            'next_year': next_year,
            'months_choices': months_choices,
            'year_choices': year_choices,
            'sort_order': sort_order,
            'is_current_month': (sel_month == today.month and sel_year == today.year),
        })

        if not employee:
            ctx['today_record'] = None
            ctx['grace_max'] = 0
            ctx['grace_used'] = 0
            ctx['grace_minutes_limit'] = 15
            ctx['records'] = []
            ctx['report'] = []
            ctx['summary'] = {}
            return ctx

        # 2. Today's Record (for daily punch card)
        ctx['today_record'] = m.AttendanceRecord.objects.filter(
            employee=employee, attendance_date=today
        ).first()

        # 3. Policy & Grace logic for employee
        policy = None
        if employee.company and hasattr(employee.company, 'get_policy_for_date'):
            try:
                policy = employee.company.get_policy_for_date(today)
            except Exception:
                policy = None
        if not policy and employee.company:
            policy = m.AttendancePolicy.objects.filter(company=employee.company).first()

        if policy:
            ctx['grace_max'] = getattr(policy, 'grace_allowed_count', getattr(policy, 'max_grace_per_month', 3))
            ctx['grace_minutes_limit'] = getattr(policy, 'grace_minutes', getattr(policy, 'grace_window_minutes', 15))
            grace_used_count = m.AttendanceRecord.objects.filter(
                employee=employee,
                attendance_date__month=today.month,
                attendance_date__year=today.year,
                late_minutes__gt=0,
                late_minutes__lte=ctx['grace_minutes_limit']
            ).count()
            ctx['grace_used'] = grace_used_count
        else:
            ctx['grace_max'] = 0
            ctx['grace_minutes_limit'] = 15
            ctx['grace_used'] = 0

        # 4. Monthly Attendance Report via unified attendance_engine (no pagination)
        from .attendance_engine import build_attendance
        start_date = date(sel_year, sel_month, 1)
        num_days = calendar.monthrange(sel_year, sel_month)[1]
        end_date = date(sel_year, sel_month, num_days)

        engine_data = build_attendance([employee], start_date, end_date, today=today)[employee.id]
        days = list(engine_data['days'])

        # Enrich dict so existing templates/attributes work seamlessly
        for d in days:
            d['attendance_date'] = d['date']
            d['total_hours'] = d['hours']
            d['get_status_display'] = d['label']

        # Sorting: descending puts newest dates at top (e.g. 31 down to 1, or 05 down to 01)
        if sort_order == 'desc':
            days = list(reversed(days))

        ctx['records'] = days
        ctx['report'] = days
        ctx['summary'] = engine_data['summary']

        return ctx


class CheckInView(LoginRequiredMixin, View):
    def post(self, request):
        employee = get_employee_profile(request.user)
        if employee is None:
            messages.error(request, "Your login isn't linked to an employee record.")
            return redirect('hrms:dashboard')
        try:
            record = attn.check_in(employee)
            if record.late_minutes:
                messages.warning(request, f'Checked in — {record.late_minutes} min late. {record.remarks}')
            else:
                messages.success(request, 'Checked in successfully.')
        except attn.AttendanceError as e:
            messages.error(request, str(e))
        return redirect('hrms:my_attendance')


class CheckOutView(LoginRequiredMixin, View):
    def post(self, request):
        employee = get_employee_profile(request.user)
        if employee is None:
            messages.error(request, "Your login isn't linked to an employee record.")
            return redirect('hrms:dashboard')
        try:
            record = attn.check_out(employee)
            messages.success(request, f'Checked out — {record.total_hours} hrs logged today.')
        except attn.AttendanceError as e:
            messages.error(request, str(e))
        return redirect('hrms:my_attendance')


# ---------------------------------------------------------------------------
# Holidays — everyone can view, HR/Admin manage
# ---------------------------------------------------------------------------
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages


class HolidayListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.Holiday
    template_name = 'hrms/attendance/holiday_list.html'
    context_object_name = 'holidays'
    active_group, active_item = 'attendance', 'holiday'

    def get_queryset(self):
        qs = m.Holiday.objects.select_related('calendar__company').order_by('date')
        template_id = self.request.GET.get('template')
        query = self.request.GET.get('q')

        if template_id:
            qs = qs.filter(calendar_id=template_id)
        if query:
            qs = qs.filter(name__icontains=query)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx['is_hr'] = is_hr_or_above(user)

        # Pass all calendars so the dropdown filter works
        if user.is_superuser:
            ctx['calendars'] = m.HolidayCalendar.objects.all()
        elif ctx['is_hr']:
            emp_profile = get_employee_profile(user)
            if emp_profile:
                managed_ids = emp_profile.managed_companies.values_list('id', flat=True)
                ctx['calendars'] = m.HolidayCalendar.objects.filter(
                    Q(company_id=emp_profile.company_id) | Q(company_id__in=managed_ids)
                ).distinct()
            else:
                ctx['calendars'] = m.HolidayCalendar.objects.all()
        else:
            emp_profile = get_employee_profile(user)
            if emp_profile and emp_profile.holiday_calendar:
                ctx['calendars'] = m.HolidayCalendar.objects.filter(id=emp_profile.holiday_calendar_id)
            else:
                ctx['calendars'] = m.HolidayCalendar.objects.all()[:1]

        return ctx


class HolidayCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Holiday
    form_class = f.BulkHolidayForm  # Use the Bulk Form
    template_name = 'hrms/attendance/holiday_form.html'
    success_url = reverse_lazy('hrms:holiday_calendar_manage')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass user to form
        return kwargs

    def form_valid(self, form):
        # 1. Get the list of calendars selected in the checkboxes
        calendars = form.cleaned_data['target_calendars']

        # 2. Get holiday details
        name = form.cleaned_data['name']
        h_date = form.cleaned_data['date']
        h_type = form.cleaned_data['type']
        desc = form.cleaned_data.get('description', '')

        # 3. Create a separate database entry for each selected calendar
        for cal in calendars:
            m.Holiday.objects.update_or_create(
                calendar=cal,
                date=h_date,
                defaults={'name': name, 'type': h_type, 'description': desc}
            )

        messages.success(self.request, f'Holiday "{name}" assigned to {calendars.count()} templates.')
        return redirect(self.success_url)


class HolidayUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Holiday
    form_class = f.HolidayForm
    template_name = 'hrms/attendance/holiday_form.html'
    success_url = reverse_lazy('hrms:holiday_list')
    active_group, active_item = 'attendance', 'holiday'

    def form_valid(self, form):
        messages.success(self.request, 'Holiday updated.')
        return super().form_valid(form)


class HolidayDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.Holiday
    template_name = 'hrms/attendance/holiday_confirm_delete.html'
    success_url = reverse_lazy('hrms:holiday_list')
    active_group, active_item = 'attendance', 'holiday'

    def form_valid(self, form):
        messages.success(self.request, 'Holiday deleted.')
        return super().form_valid(form)


class HolidayCalendarManageView(HRRequiredMixin, SidebarContextMixin, TemplateView):
    template_name = 'hrms/attendance/holiday_calendar_manage.html'
    active_group, active_item = 'attendance', 'holiday'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Show calendars for ALL companies the user manages
        if self.request.user.is_superuser:
            ctx['calendars'] = m.HolidayCalendar.objects.all().order_by('company__name', 'name')
        else:
            managed_ids = self.request.user.employee_profile.managed_companies.values_list('id', flat=True)
            ctx['calendars'] = m.HolidayCalendar.objects.filter(company_id__in=managed_ids).order_by('name')

        edit_id = self.request.GET.get('edit')
        if edit_id:
            instance = get_object_or_404(m.HolidayCalendar, pk=edit_id)
            # Pass user to form
            ctx['form'] = f.HolidayCalendarForm(instance=instance, user=self.request.user)
            ctx['edit_instance'] = instance
        else:
            # Pass user to form
            ctx['form'] = f.HolidayCalendarForm(user=self.request.user)

        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        pk = request.POST.get('pk')

        if action == 'delete' and pk:
            instance = get_object_or_404(m.HolidayCalendar, pk=pk)
            instance.delete()
            messages.success(request, "Template deleted.")

        elif action == 'save':
            if pk:
                instance = get_object_or_404(m.HolidayCalendar, pk=pk)
                form = f.HolidayCalendarForm(request.POST, instance=instance, user=request.user)
            else:
                form = f.HolidayCalendarForm(request.POST, user=request.user)

            if form.is_valid():
                form.save()  # Company is now handled by the form selection
                messages.success(request, "Template saved successfully.")
            else:
                messages.error(request, "Please correct the errors below.")
                return self.render_to_response(self.get_context_data(form=form))

        return redirect('hrms:holiday_calendar_manage')


from django.db import transaction

from django.db import transaction


class HolidayCalendarManageView(HRRequiredMixin, SidebarContextMixin, TemplateView):
    template_name = 'hrms/attendance/holiday_calendar_manage.html'
    active_group, active_item = 'attendance', 'holiday'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. Existing Logic: Show calendars for managed companies
        if self.request.user.is_superuser:
            ctx['calendars'] = m.HolidayCalendar.objects.all().order_by('company__name', 'name')
        else:
            managed_ids = self.request.user.employee_profile.managed_companies.values_list('id', flat=True)
            ctx['calendars'] = m.HolidayCalendar.objects.filter(company_id__in=managed_ids).order_by('name')

        edit_id = self.request.GET.get('edit')
        if edit_id:
            instance = get_object_or_404(m.HolidayCalendar, pk=edit_id)
            ctx['form'] = f.HolidayCalendarForm(instance=instance, user=self.request.user)
            ctx['edit_instance'] = instance

            # 2. NEW: Get employees belonging to this template's company
            ctx['company_employees'] = m.Employee.objects.filter(
                company=instance.company
            ).select_related('user').order_by('first_name')

            # IDs of employees currently linked to this template
            ctx['assigned_ids'] = list(instance.employees.values_list('id', flat=True))
        else:
            ctx['form'] = f.HolidayCalendarForm(user=self.request.user)

        return ctx

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')
        pk = request.POST.get('pk')

        if action == 'delete' and pk:
            instance = get_object_or_404(m.HolidayCalendar, pk=pk)
            instance.delete()
            messages.success(request, "Template deleted.")

        elif action == 'save':
            if pk:
                instance = get_object_or_404(m.HolidayCalendar, pk=pk)
                form = f.HolidayCalendarForm(request.POST, instance=instance, user=request.user)
            else:
                form = f.HolidayCalendarForm(request.POST, user=request.user)

            if form.is_valid():
                with transaction.atomic():
                    instance = form.save()

                    # 3. NEW: Handle Bulk Employee Assignment logic
                    if pk:
                        selected_emp_ids = request.POST.getlist('assigned_employees')

                        # Remove this template from employees who were unchecked
                        m.Employee.objects.filter(holiday_calendar=instance).exclude(id__in=selected_emp_ids).update(
                            holiday_calendar=None)

                        # Assign this template to the newly checked employees
                        m.Employee.objects.filter(id__in=selected_emp_ids).update(holiday_calendar=instance)

                messages.success(request, "Template and assignments updated successfully.")
            else:
                messages.error(request, "Please correct the errors below.")
                return self.render_to_response(self.get_context_data(form=form))

        return redirect('hrms:holiday_calendar_manage')


# ===========================================================================
# LEAVE MANAGEMENT
# ===========================================================================
from . import leave_logic as lv


# ---------------------------------------------------------------------------
# Leave Types — HR/Admin manage
# ---------------------------------------------------------------------------
class LeaveTypeListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.LeaveType
    template_name = 'hrms/leave/leavetype_list.html'
    context_object_name = 'leave_types'
    active_group, active_item = 'leave', 'leavetype'

    def get_queryset(self):
        qs = m.LeaveType.objects.select_related('company')
        company_id = self.request.GET.get('company')
        if company_id:
            qs = qs.filter(company_id=company_id)
        return qs.order_by('company__name', 'name')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['companies'] = m.Company.objects.all()
        ctx['selected_company'] = self.request.GET.get('company')
        return ctx


class LeaveTypeCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.LeaveType
    form_class = f.LeaveTypeForm
    template_name = 'hrms/leave/leavetype_form.html'
    success_url = reverse_lazy('hrms:leavetype_list')
    active_group, active_item = 'leave', 'leavetype'

    def form_valid(self, form):
        messages.success(self.request, 'Leave type created.')
        return super().form_valid(form)


class LeaveTypeUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.LeaveType
    form_class = f.LeaveTypeForm
    template_name = 'hrms/leave/leavetype_form.html'
    success_url = reverse_lazy('hrms:leavetype_list')
    active_group, active_item = 'leave', 'leavetype'

    def form_valid(self, form):
        messages.success(self.request, 'Leave type updated.')
        return super().form_valid(form)


class LeaveTypeDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.LeaveType
    template_name = 'hrms/leave/leavetype_confirm_delete.html'
    success_url = reverse_lazy('hrms:leavetype_list')
    active_group, active_item = 'leave', 'leavetype'

    def form_valid(self, form):
        messages.success(self.request, 'Leave type deleted.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Leave Balance — HR allocates/corrects; everyone can view (own vs all)
# ---------------------------------------------------------------------------
class LeaveBalanceListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.LeaveBalance
    template_name = 'hrms/leave/leave_balance_list.html'
    context_object_name = 'balances'
    active_group, active_item = 'leave', 'leave_balance'

    def get_queryset(self):
        qs = m.LeaveBalance.objects.select_related('employee', 'leave_type')
        if is_hr_or_above(self.request.user):
            employee_id = self.request.GET.get('employee')
            if employee_id:
                qs = qs.filter(employee_id=employee_id)
            return qs.order_by('employee__employee_code', 'leave_type__name')
        employee = get_employee_profile(self.request.user)
        if employee is None:
            return m.LeaveBalance.objects.none()
        return qs.filter(employee=employee).order_by('leave_type__name')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_is_hr = is_hr_or_above(self.request.user)
        ctx['hrms_is_hr'] = user_is_hr

        # 1. Fetch all balances (Filtered by HR selection if needed)
        qs = self.get_queryset()

        # 2. Pivot the data: { EmployeeObject: { 'CL': 5, 'SL': 2, ... } }
        employee_map = {}
        for bal in qs:
            emp = bal.employee
            if emp not in employee_map:
                employee_map[emp] = {
                    'details': emp,
                    'year': bal.year,
                    'types': {}
                }
            # Store the available balance for each leave type code
            employee_map[emp]['types'][bal.leave_type.code.upper()] = bal.available

        ctx['pivoted_balances'] = employee_map.values()

        if user_is_hr:
            ctx['employees'] = m.Employee.objects.all()
        return ctx


class LeaveBalanceListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.EmployeeLeaveBalance
    template_name = 'hrms/leave/leave_balance_list.html'
    context_object_name = 'balances'
    active_group, active_item = 'leave', 'leave_balance'

    def get_queryset(self):
        # 1. Fetch balances with employee relations preloaded
        qs = m.EmployeeLeaveBalance.objects.select_related(
            'e_name', 'e_name__department', 'e_name__designation', 'e_name__company'
        )

        # 2. Filter strictly for active employees
        qs = qs.filter(e_name__status=m.Employee.Status.ACTIVE)

        user = self.request.user
        emp = get_employee_profile(user)

        # 3. Non-HR employees can only view their own balance
        if not is_hr_or_above(user):
            if emp is None:
                return m.EmployeeLeaveBalance.objects.none()
            return qs.filter(e_name=emp)

        # 4. Multi-company session filter
        active_id = self.request.session.get('active_company_id')
        if active_id and active_id != 'all':
            qs = qs.filter(e_name__company_id=active_id)

        # 5. Single employee filter from search bar
        employee_id = self.request.GET.get('employee')
        if employee_id:
            qs = qs.filter(e_name_id=employee_id)

        return qs.order_by('e_name__employee_code')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_is_hr = is_hr_or_above(self.request.user)
        ctx['hrms_is_hr'] = user_is_hr

        current_year = date.today().year
        qs = self.get_queryset()

        # Build table rows from actual database values
        pivoted = []
        for bal in qs:
            emp = bal.e_name
            pivoted.append({
                'details': emp,
                'year': current_year,
                'types': {
                    'CL': getattr(bal, 'casual_leave', 0.0),
                    'SL': getattr(bal, 'sick_leave', 0.0),
                    'EL': getattr(bal, 'earned_leave', 0.0),
                    'ML': getattr(bal, 'menstrual_leave', 0.0),
                    'BL': getattr(bal, 'bereavement_leave', 0.0),
                    'CO': getattr(bal, 'comp_off', 0.0),
                }
            })

        ctx['pivoted_balances'] = pivoted

        # Populate active employee dropdown for HR filter
        if user_is_hr:
            emp_qs = m.Employee.objects.filter(status=m.Employee.Status.ACTIVE).order_by('employee_code')
            active_id = self.request.session.get('active_company_id')
            if active_id and active_id != 'all':
                emp_qs = emp_qs.filter(company_id=active_id)
            ctx['employees'] = emp_qs

        return ctx


# ---employee leave detail page
class EmployeeLeaveHistoryView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    model = m.Employee
    template_name = 'hrms/leave/employee_leave_history.html'
    context_object_name = 'employee'
    active_group, active_item = 'leave', 'leave_balance'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Fetch all leave applications for this specific employee
        ctx['leave_history'] = m.LeaveApplication.objects.filter(
            employee=self.get_object()
        ).select_related('leave_type').order_by('-start_date')

        # Calculate summary stats for the header
        ctx['total_leaves'] = ctx['leave_history'].filter(status='approved').count()
        return ctx


class LeaveBalanceCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.LeaveBalance
    form_class = f.LeaveBalanceForm
    template_name = 'hrms/leave/leave_balance_form.html'
    success_url = reverse_lazy('hrms:leave_balance')
    active_group, active_item = 'leave', 'leave_balance'

    def form_valid(self, form):
        messages.success(self.request, 'Leave balance allocated.')
        return super().form_valid(form)


class LeaveBalanceUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    # model = m.LeaveBalance
    model = m.EmployeeLeaveBalance
    form_class = f.LeaveBalanceForm
    template_name = 'hrms/leave/leave_balance_form.html'
    success_url = reverse_lazy('hrms:leave_balance')
    active_group, active_item = 'leave', 'leave_balance'

    def get_object(self, queryset=None):
        # Instead of using the PK of the LeaveBalance, we look it up using the 'pk' from the URL (which will be the Employee ID)
        emp_id = self.kwargs.get('pk')
        # This assumes one LeaveBalance record per employee for the current year,Adjust 'year' logic if you track multiple years
        # return get_object_or_404(m.LeaveBalance, employee_id=emp_id)
        obj, created = m.EmployeeLeaveBalance.objects.get_or_create(e_name_id=emp_id)
        return obj

    def form_valid(self, form):
        messages.success(self.request, f'Leave balance for {self.object.employee} updated.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Leave Applications — employee applies for their own; HR sees all + approves
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Leave Applications — employee applies for their own; HR sees all + approves
# ---------------------------------------------------------------------------
class LeaveApplicationListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.LeaveApplication
    template_name = 'hrms/leave/leave_application_list.html'
    context_object_name = 'applications'
    active_group, active_item = 'leave', 'my_leave'
    paginate_by = 15

    def get_queryset(self):
        qs = m.LeaveApplication.objects.select_related(
            'employee', 'employee__designation', 'employee__department', 'leave_type', 'approved_by'
        ).order_by('-applied_on')

        user = self.request.user
        emp = get_employee_profile(user)

        # 1. Role-based visibility
        if not is_hr_or_above(user):
            if emp is None:
                return m.LeaveApplication.objects.none()
            if emp.is_manager:
                # Manager can see their own leaves AND subordinate leaves
                qs = qs.filter(Q(employee=emp) | Q(employee__reporting_manager=emp))
            else:
                qs = qs.filter(employee=emp)

        # 2. Extract Filter Parameters
        status = self.request.GET.get('status')
        employee_id = self.request.GET.get('employee')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        sales_filter = self.request.GET.get('sales')
        department_id = self.request.GET.get('department')

        # 3. Apply Filters
        if status:
            qs = qs.filter(status=status)
        if employee_id and (is_hr_or_above(user) or (emp and emp.is_manager)):
            qs = qs.filter(employee_id=employee_id)
        if start_date:
            qs = qs.filter(start_date__gte=start_date)
        if end_date:
            qs = qs.filter(end_date__lte=end_date)
        if sales_filter == '1':
            qs = qs.filter(employee__department__name__icontains='sales')
        elif department_id:
            qs = qs.filter(employee__department_id=department_id)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        user_is_hr = is_hr_or_above(user)
        emp = get_employee_profile(user)
        ctx['is_hr'] = user_is_hr
        ctx['is_manager'] = (emp and emp.is_manager) if emp else False
        ctx['current_employee'] = emp

        # Base QS for full stats
        base_qs = self.get_queryset()

        ctx['stats'] = {
            'total': base_qs.count(),
            'pending_manager': base_qs.filter(status=m.LeaveApplication.Status.PENDING_MANAGER).count(),
            'pending_hr': base_qs.filter(
                status__in=[m.LeaveApplication.Status.PENDING_HR, m.LeaveApplication.Status.PENDING]).count(),
            'approved': base_qs.filter(status=m.LeaveApplication.Status.APPROVED).count(),
            'rejected': base_qs.filter(status=m.LeaveApplication.Status.REJECTED).count(),
            'sales': base_qs.filter(employee__department__name__icontains='sales').count(),
        }

        # Calculate live remaining balances formatted as CL (Total/Remaining) e.g. CL (10/3)
        field_map = {
            'CL': 'casual_leave', 'EL': 'earned_leave', 'SL': 'sick_leave',
            'ML': 'menstrual_leave', 'MTL': 'menstrual_leave',
            'BL': 'bereavement_leave', 'CO': 'comp_off'
        }

        # Cache balance instances for current page employees
        page_employees = {app.employee_id: app.employee for app in ctx['applications']}
        emp_banks = {b.e_name_id: b for b in m.EmployeeLeaveBalance.objects.filter(e_name_id__in=page_employees.keys())}
        emp_lives = {l.e_name_id: l for l in
                     m.EmployeeLeaveBalanceLive.objects.filter(e_name_id__in=page_employees.keys())}

        from django.db.models import Sum
        for app in ctx['applications']:
            code = (app.leave_type.code or '').upper()
            field_name = field_map.get(code)
            bank = emp_banks.get(app.employee_id)
            live = emp_lives.get(app.employee_id)

            if field_name and bank and live:
                if code == 'CO':
                    co_avail = lv.get_monthly_available_days(app.employee, app.leave_type)
                    co_records = m.CompOffRecord.objects.filter(employee=app.employee)
                    co_earned = co_records.aggregate(s=Sum('credits_earned'))['s'] or Decimal('0.0')
                    tot = max(co_earned, Decimal(str(getattr(bank, 'comp_off', 0) or 0)))
                    rem = co_avail
                    used = max(Decimal('0.0'), tot - rem)
                else:
                    tot = Decimal(str(getattr(bank, field_name, 0.0) or 0.0))
                    rem = Decimal(str(getattr(live, field_name, 0.0) or 0.0))
                    used = max(Decimal('0.0'), tot - rem)

                def _fmt(val):
                    f = float(val)
                    return str(int(f)) if f.is_integer() else f"{f:.1f}"

                app.balance_rem = _fmt(rem)
                app.balance_used = _fmt(used)
                app.balance_tot = _fmt(tot)
                app.has_balance_details = True
                app.balance_formatted = f"{code} ({app.balance_rem} Left / {app.balance_used} Used)"
            else:
                app.has_balance_details = False
                app.balance_formatted = app.leave_type.name

        if user_is_hr or (emp and emp.is_manager):
            if user_is_hr:
                ctx['employees'] = m.Employee.objects.filter(status='active').select_related('department').order_by(
                    'first_name')
            else:
                ctx['employees'] = m.Employee.objects.filter(reporting_manager=emp, status='active').select_related(
                    'department').order_by('first_name')
            ctx['status_choices'] = m.LeaveApplication.Status.choices
            ctx['departments'] = m.Department.objects.all()
            ctx['filters'] = self.request.GET
            ctx['reject_form'] = f.LeaveRejectForm()

        return ctx


class LeaveApplicationCreateView(LoginRequiredMixin, SidebarContextMixin, View):
    """Employees apply for their own leave; HR/Admin can apply on behalf of anyone.
    Balance-sufficiency is enforced by leave_logic.apply_leave() — this view
    just wires the form to it and surfaces any LeaveError as a form error."""
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return f.LeaveApplicationHRForm if is_hr_or_above(self.request.user) else f.LeaveApplicationForm

    def get(self, request):
        form = self.get_form_class()()
        return render(request, self.template_name, {
            'form': form, 'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST)
        if form.is_valid():
            employee = form.cleaned_data.get('employee') if is_hr_or_above(request.user) else get_employee_profile(
                request.user)

            if employee is None:
                messages.error(request, "Your login isn't linked to an employee record.")
                return redirect('hrms:dashboard')

            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    day_type=form.cleaned_data['day_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    reason=form.cleaned_data.get('reason', ''),
                )
                messages.success(request, 'Leave application submitted successfully.')
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))
        return render(request, self.template_name, {
            'form': form, 'active_group': self.active_group, 'active_item': self.active_item,
        })


from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views import View
from hrms import forms as f
from hrms import leave_logic as lv
from hrms import models as m
from hrms.permissions import get_employee_profile, is_hr_or_above


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return f.LeaveApplicationHRForm if is_hr_or_above(self.request.user) else f.LeaveApplicationForm

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            # 1. Fetch all Leave Types configured for this employee's company
            company = employee.company
            leave_types_qs = m.LeaveType.objects.filter(company=company) if company else m.LeaveType.objects.all()

            # 2. Check employee status dynamically from model fields
            is_female = bool(employee.gender and employee.gender.upper().startswith('F'))
            is_male = bool(employee.gender and employee.gender.upper().startswith('M'))
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            # 3. Dynamic Monthly & Annual Card Generator from DB
            for lt in leave_types_qs:
                # Filter by gender restrictions configured in the LeaveType model
                gender_rule = getattr(lt, 'gender_restriction', getattr(lt, 'gender', None))
                if gender_rule:
                    g_str = str(gender_rule).upper()
                    if 'FEMALE' in g_str and not is_female:
                        continue
                    if 'MALE' in g_str and not is_male:
                        continue

                # Code & Name from DB
                code = lt.code.upper()
                annual_quota = Decimal(str(lt.annual_quota or 0.0))

                # If the policy requires confirmation and the employee is not confirmed, skip paid leaves
                if not is_confirmed and code not in ('ML', 'MENSTRUAL'):
                    continue

                # If employee is confirmed, exclude Menstrual leave (policy for trainees/interns only)
                if is_confirmed and code in ('ML', 'MENSTRUAL'):
                    continue

                # Dynamic Monthly Quota (annual_quota / 12)
                monthly_quota = round(annual_quota / Decimal('12.0'), 1) if annual_quota > 0 else Decimal('0.0')

                # Calculate Approved Leave Usage This Month from LeaveApplication Table
                current_month_used = m.LeaveApplication.objects.filter(
                    employee=employee,
                    leave_type=lt,
                    status=m.LeaveApplication.Status.APPROVED,
                    start_date__year=today.year,
                    start_date__month=today.month
                ).aggregate(total=Sum('total_days'))['total'] or Decimal('0.0')

                monthly_available = max(Decimal('0.0'), monthly_quota - current_month_used)

                # Fetch Balance Record from DB
                balance_record = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()
                annual_balance = Decimal('0.0')
                if balance_record:
                    # Match dynamic attribute by leave type code/name
                    field_map = {
                        'CL': 'casual_leave',
                        'EL': 'earned_leave',
                        'SL': 'sick_leave',
                        'BL': 'bereavement_leave',
                        'ML': 'menstrual_leave',
                        'CO': 'comp_off',
                    }
                    target_field = field_map.get(code)
                    if target_field and hasattr(balance_record, target_field):
                        annual_balance = getattr(balance_record, target_field) or Decimal('0.0')
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                # Determine styling dynamically
                is_emergency = code in ('SL', 'BL')
                cards.append({
                    'code': code,
                    'name': lt.name,
                    'is_paid': lt.is_paid,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)

        employee = form.cleaned_data.get('employee') if (
                    is_hr_or_above(request.user) and 'employee' in form.cleaned_data) else get_employee_profile(
            request.user)

        if employee is None:
            messages.error(request, "Your login isn't linked to an employee record.")
            return redirect('hrms:dashboard')

        if form.is_valid():
            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    day_type=form.cleaned_data['day_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get('supporting_document'),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                messages.success(request, 'Leave application submitted successfully.')
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)


from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views import View
from hrms import forms as f
from hrms import leave_logic as lv
from hrms import models as m
from hrms.permissions import get_employee_profile, is_hr_or_above


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return f.LeaveApplicationHRForm if is_hr_or_above(self.request.user) else f.LeaveApplicationForm

    def _get_leave_type_quota(self, leave_type_obj):
        """Dynamically finds the yearly quota field on the LeaveType model instance."""
        for field_name in ('days_per_year', 'annual_quota', 'days_allowed', 'quota', 'max_days', 'days'):
            if hasattr(leave_type_obj, field_name):
                val = getattr(leave_type_obj, field_name)
                if val is not None:
                    return Decimal(str(val))
        return Decimal('0.0')

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = m.LeaveType.objects.filter(company=company) if company else m.LeaveType.objects.all()

            # Dynamic Employee Attributes
            is_female = bool(employee.gender and employee.gender.upper().startswith('F'))
            is_male = bool(employee.gender and employee.gender.upper().startswith('M'))
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            # Fetch Leave Balance Record for Employee from DB
            balance_record = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name

                # Gender check from DB model
                gender_rule = getattr(lt, 'gender_restriction', getattr(lt, 'gender', 'ALL'))
                if gender_rule:
                    g_str = str(gender_rule).upper()
                    if 'FEMALE' in g_str and not is_female:
                        continue
                    if 'MALE' in g_str and not is_male:
                        continue

                annual_quota = self._get_leave_type_quota(lt)

                # Skip paid allocations for unconfirmed staff / interns (except Menstrual Leave if female)
                if not is_confirmed and code not in ('ML', 'MENSTRUAL') and 'menstrual' not in name.lower():
                    continue

                # Menstrual Leave policy: reserved for unconfirmed/intern female staff
                if is_confirmed and (code in ('ML', 'MENSTRUAL') or 'menstrual' in name.lower()):
                    continue

                # Dynamic Monthly Quota (annual / 12)
                monthly_quota = round(annual_quota / Decimal('12.0'), 1) if annual_quota > Decimal('0.0') else Decimal(
                    '0.0')

                # Calculate Approved Days Taken in Current Month from DB
                month_used = m.LeaveApplication.objects.filter(
                    employee=employee,
                    leave_type=lt,
                    status=m.LeaveApplication.Status.APPROVED,
                    start_date__year=today.year,
                    start_date__month=today.month
                ).aggregate(total=Sum('total_days'))['total'] or Decimal('0.0')

                monthly_available = max(Decimal('0.0'), monthly_quota - Decimal(str(month_used)))

                # Determine Balance from DB
                annual_balance = Decimal('0.0')
                if balance_record:
                    # Look for corresponding field in EmployeeLeaveBalance
                    for attr in (code.lower() + '_leave', name.lower().replace(' ', '_') + '_leave',
                                 name.lower().replace(' ', '_')):
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                is_emergency = code in ('SL', 'BL') or 'sick' in name.lower() or 'bereave' in name.lower()

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)

        employee = form.cleaned_data.get('employee') if (
                    is_hr_or_above(request.user) and 'employee' in form.cleaned_data) else get_employee_profile(
            request.user)

        if employee is None:
            messages.error(request, "Your login isn't linked to an employee record.")
            return redirect('hrms:dashboard')

        if form.is_valid():
            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    day_type=form.cleaned_data['day_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get('supporting_document'),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                messages.success(request, 'Leave application submitted successfully.')
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)


from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Sum
from django.shortcuts import redirect, render
from django.views import View
from hrms import forms as f
from hrms import leave_logic as lv
from hrms import models as m
from hrms.permissions import get_employee_profile, is_hr_or_above


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return (
            f.LeaveApplicationHRForm
            if is_hr_or_above(self.request.user)
            else f.LeaveApplicationForm
        )

    def _get_leave_type_quota(self, leave_type_obj):
        for field_name in (
                'days_per_year',
                'annual_quota',
                'days_allowed',
                'quota',
                'max_days',
                'days',
        ):
            if hasattr(leave_type_obj, field_name):
                val = getattr(leave_type_obj, field_name)
                if val is not None:
                    return Decimal(str(val))
        return Decimal('0.0')

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = (
                m.LeaveType.objects.filter(company=company)
                if company
                else m.LeaveType.objects.all()
            )

            is_female = bool(
                employee.gender and employee.gender.upper().startswith('F')
            )
            is_male = bool(
                employee.gender and employee.gender.upper().startswith('M')
            )
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            # 1. Fetch Master Leave Balance record from DB
            balance_record = m.EmployeeLeaveBalance.objects.filter(
                e_name=employee
            ).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name
                norm_name = name.lower()

                # Gender restriction filter
                gender_rule = getattr(
                    lt, 'gender_restriction', getattr(lt, 'gender', 'ALL')
                )
                if gender_rule:
                    g_str = str(gender_rule).upper()
                    if 'FEMALE' in g_str and not is_female:
                        continue
                    if 'MALE' in g_str and not is_male:
                        continue

                # Fetch Annual Balance from EmployeeLeaveBalance table
                annual_balance = Decimal('0.0')
                if balance_record:
                    field_candidates = (
                        code.lower() + '_leave',
                        norm_name.replace(' ', '_') + '_leave',
                        norm_name.replace(' ', '_'),
                    )
                    for attr in field_candidates:
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = self._get_leave_type_quota(lt)
                else:
                    annual_balance = self._get_leave_type_quota(lt)

                # Maternity / Paternity: Show ONLY if explicitly assigned (> 0) in Leave Bank
                is_maternity = 'matern' in norm_name or code in (
                    'MATERNITY',
                    'MTL',
                )
                is_paternity = 'patern' in norm_name or code in (
                    'PATERNITY',
                    'PL',
                )
                if (is_maternity or is_paternity) and annual_balance <= Decimal(
                        '0.0'
                ):
                    continue

                if (
                        code in ('LWP', 'CO')
                        or 'without pay' in norm_name
                        or 'comp' in norm_name
                ):
                    continue

                is_menstrual = (
                        code in ('ML', 'MENSTRUAL') or 'menstrual' in norm_name
                )
                if not is_confirmed and not is_menstrual:
                    continue
                if is_confirmed and is_menstrual:
                    continue

                annual_quota = self._get_leave_type_quota(lt)
                is_emergency = (
                        code in ('SL', 'BL')
                        or 'sick' in norm_name
                        or 'breave' in norm_name
                        or 'bereave' in norm_name
                        or is_maternity
                        or is_paternity
                )

                # Monthly limit definition
                if code == 'CL' or 'casual' in norm_name:
                    monthly_quota = Decimal('1.0')
                elif code == 'EL' or 'earned' in norm_name:
                    monthly_quota = (
                        round(annual_quota / Decimal('12.0'), 1)
                        if annual_quota > 0
                        else Decimal('1.5')
                    )
                elif is_menstrual:
                    monthly_quota = Decimal('2.0')
                else:
                    monthly_quota = annual_quota

                # 2. Sum days taken/applied in the current calendar month (Pending + Approved)
                month_used_result = m.LeaveApplication.objects.filter(
                    employee=employee,
                    leave_type=lt,
                    status__in=[
                        m.LeaveApplication.Status.APPROVED,
                        m.LeaveApplication.Status.PENDING,
                    ],
                    start_date__year=today.year,
                    start_date__month=today.month,
                ).aggregate(total=Sum('total_days'))['total']

                month_used = (
                    Decimal(str(month_used_result))
                    if month_used_result
                    else Decimal('0.0')
                )

                # Remaining monthly quota
                monthly_available = max(Decimal('0.0'), monthly_quota - month_used)

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)

        employee = (
            form.cleaned_data.get('employee')
            if (is_hr_or_above(request.user) and 'employee' in form.cleaned_data)
            else get_employee_profile(request.user)
        )

        if employee is None:
            messages.error(
                request, "Your login isn't linked to an employee record."
            )
            return redirect('hrms:dashboard')

        if form.is_valid():
            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    day_type=form.cleaned_data['day_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get(
                        'supporting_document'
                    ),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                messages.success(
                    request, 'Leave application submitted successfully.'
                )
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return (
            f.LeaveApplicationHRForm
            if is_hr_or_above(self.request.user)
            else f.LeaveApplicationForm
        )

    def _get_leave_type_quota(self, leave_type_obj):
        for field_name in (
                'days_per_year',
                'annual_quota',
                'days_allowed',
                'quota',
                'max_days',
                'days',
        ):
            if hasattr(leave_type_obj, field_name):
                val = getattr(leave_type_obj, field_name)
                if val is not None:
                    return Decimal(str(val))
        return Decimal('0.0')

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = (
                m.LeaveType.objects.filter(company=company)
                if company
                else m.LeaveType.objects.all()
            )

            is_female = bool(
                employee.gender and employee.gender.upper().startswith('F')
            )
            is_male = bool(
                employee.gender and employee.gender.upper().startswith('M')
            )
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            # 1. Fetch Master Balance Record from DB
            balance_record = m.EmployeeLeaveBalance.objects.filter(
                e_name=employee
            ).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name
                norm_name = name.lower()

                # Gender check from DB model
                gender_rule = getattr(
                    lt, 'gender_restriction', getattr(lt, 'gender', 'ALL')
                )
                if gender_rule:
                    g_str = str(gender_rule).upper()
                    if 'FEMALE' in g_str and not is_female:
                        continue
                    if 'MALE' in g_str and not is_male:
                        continue

                # Annual Quota from DB
                annual_quota = self._get_leave_type_quota(lt)

                # Fetch Annual Balance from EmployeeLeaveBalance
                annual_balance = Decimal('0.0')
                if balance_record:
                    candidates = (
                        code.lower() + '_leave',
                        norm_name.replace(' ', '_') + '_leave',
                        norm_name.replace(' ', '_'),
                    )
                    for attr in candidates:
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                # Maternity / Paternity: Show ONLY if assigned (> 0)
                is_maternity = 'matern' in norm_name or code in (
                    'MATERNITY',
                    'MTL',
                )
                is_paternity = 'patern' in norm_name or code in (
                    'PATERNITY',
                    'PL',
                )
                if (is_maternity or is_paternity) and annual_balance <= Decimal(
                        '0.0'
                ):
                    continue

                if (
                        code in ('LWP', 'CO')
                        or 'without pay' in norm_name
                        or 'comp' in norm_name
                ):
                    continue

                is_menstrual = (
                        code in ('ML', 'MENSTRUAL') or 'menstrual' in norm_name
                )
                if not is_confirmed and not is_menstrual:
                    continue
                if is_confirmed and is_menstrual:
                    continue

                is_emergency = (
                        code in ('SL', 'BL')
                        or 'sick' in norm_name
                        or 'breave' in norm_name
                        or 'bereave' in norm_name
                        or is_maternity
                        or is_paternity
                )

                # Monthly quota calculation
                if code == 'CL' or 'casual' in norm_name:
                    monthly_quota = Decimal('1.0')
                elif code == 'EL' or 'earned' in norm_name:
                    monthly_quota = (
                        round(annual_quota / Decimal('12.0'), 1)
                        if annual_quota > Decimal('0.0')
                        else Decimal('1.5')
                    )
                elif is_menstrual:
                    monthly_quota = Decimal('2.0')
                else:
                    monthly_quota = annual_quota

                # 2. Query all active statuses (Pending Manager, Pending HR, Pending, Approved)
                active_statuses = [
                    getattr(m.LeaveApplication.Status, 'APPROVED', 'approved'),
                    getattr(m.LeaveApplication.Status, 'PENDING', 'pending'),
                    getattr(
                        m.LeaveApplication.Status,
                        'PENDING_HR',
                        'pending_hr',
                    ),
                    getattr(
                        m.LeaveApplication.Status,
                        'PENDING_MANAGER',
                        'pending_manager',
                    ),
                ]

                month_used_result = (
                    m.LeaveApplication.objects.filter(
                        employee=employee,
                        leave_type=lt,
                        status__in=active_statuses,
                    )
                    .filter(
                        Q(
                            start_date__year=today.year,
                            start_date__month=today.month,
                        )
                        | Q(
                            end_date__year=today.year,
                            end_date__month=today.month,
                        )
                    )
                    .aggregate(total=Sum('total_days'))['total']
                )

                month_used = (
                    Decimal(str(month_used_result))
                    if month_used_result
                    else Decimal('0.0')
                )
                monthly_available = max(
                    Decimal('0.0'), monthly_quota - month_used
                )

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)

        employee = (
            form.cleaned_data.get('employee')
            if (is_hr_or_above(request.user) and 'employee' in form.cleaned_data)
            else get_employee_profile(request.user)
        )

        if employee is None:
            messages.error(
                request, "Your login isn't linked to an employee record."
            )
            return redirect('hrms:dashboard')

        if form.is_valid():
            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    day_type=form.cleaned_data.get('day_type', 'full'),
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get(
                        'supporting_document'
                    ),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                messages.success(
                    request, 'Leave application submitted successfully.'
                )
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return f.LeaveApplicationHRForm if is_hr_or_above(self.request.user) else f.LeaveApplicationForm

    def _get_leave_type_quota(self, leave_type_obj):
        for field_name in ('days_per_year', 'annual_quota', 'days_allowed', 'quota', 'max_days', 'days'):
            if hasattr(leave_type_obj, field_name):
                val = getattr(leave_type_obj, field_name)
                if val is not None:
                    return Decimal(str(val))
        return Decimal('0.0')

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = m.LeaveType.objects.filter(company=company) if company else m.LeaveType.objects.all()

            is_female = bool(employee.gender and employee.gender.upper().startswith('F'))
            is_male = bool(employee.gender and employee.gender.upper().startswith('M'))
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            # Master Balance Record from DB
            balance_record = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name
                norm_name = name.lower()

                # Gender check
                gender_rule = getattr(lt, 'gender_restriction', getattr(lt, 'gender', 'ALL'))
                if gender_rule:
                    g_str = str(gender_rule).upper()
                    if 'FEMALE' in g_str and not is_female:
                        continue
                    if 'MALE' in g_str and not is_male:
                        continue

                annual_quota = self._get_leave_type_quota(lt)

                # Fetch Balance directly from Master Leave Bank
                annual_balance = Decimal('0.0')
                if balance_record:
                    candidates = (
                        code.lower() + '_leave',
                        norm_name.replace(' ', '_') + '_leave',
                        norm_name.replace(' ', '_'),
                    )
                    for attr in candidates:
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                # Hide Maternity/Paternity if balance is 0 or unassigned
                is_maternity = 'matern' in norm_name or code in ('MATERNITY', 'MTL')
                is_paternity = 'patern' in norm_name or code in ('PATERNITY', 'PL')
                if (is_maternity or is_paternity) and annual_balance <= Decimal('0.0'):
                    continue

                if code in ('LWP', 'CO') or 'without pay' in norm_name or 'comp' in norm_name:
                    continue

                is_menstrual = code in ('ML', 'MENSTRUAL') or 'menstrual' in norm_name
                if not is_confirmed and not is_menstrual:
                    continue
                if is_confirmed and is_menstrual:
                    continue

                is_emergency = code in ('SL',
                                        'BL') or 'sick' in norm_name or 'breave' in norm_name or 'bereave' in norm_name or is_maternity or is_paternity

                # Monthly quota limits
                if code == 'CL' or 'casual' in norm_name:
                    monthly_quota = Decimal('1.0')
                elif code == 'EL' or 'earned' in norm_name:
                    monthly_quota = round(annual_quota / Decimal('12.0'), 1) if annual_quota > Decimal(
                        '0.0') else Decimal('1.5')
                elif is_menstrual:
                    monthly_quota = Decimal('2.0')
                else:
                    monthly_quota = annual_quota

                # Count active applications for this month
                active_statuses = [
                    getattr(m.LeaveApplication.Status, 'APPROVED', 'approved'),
                    getattr(m.LeaveApplication.Status, 'PENDING', 'pending'),
                    getattr(m.LeaveApplication.Status, 'PENDING_HR', 'pending_hr'),
                    getattr(m.LeaveApplication.Status, 'PENDING_MANAGER', 'pending_manager'),
                ]
                month_used_result = m.LeaveApplication.objects.filter(
                    employee=employee,
                    leave_type=lt,
                    status__in=active_statuses,
                    start_date__year=today.year,
                    start_date__month=today.month,
                ).aggregate(total=Sum('total_days'))['total']

                month_used = Decimal(str(month_used_result)) if month_used_result else Decimal('0.0')
                monthly_available = max(Decimal('0.0'), min(annual_balance, monthly_quota - month_used))

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)

        employee = form.cleaned_data.get('employee') if (
                    is_hr_or_above(request.user) and 'employee' in form.cleaned_data) else get_employee_profile(
            request.user)

        if employee is None:
            messages.error(request, "Your login isn't linked to an employee record.")
            return redirect('hrms:dashboard')

        if form.is_valid():
            try:
                lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    day_type=form.cleaned_data.get('day_type', 'full'),
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get('supporting_document'),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                messages.success(request, 'Leave application submitted successfully.')
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)


class LeaveApplicationCreateView(LoginRequiredMixin, View):
    active_group, active_item = 'leave', 'my_leave'
    template_name = 'hrms/leave/leave_application_form.html'

    def get_form_class(self):
        return f.LeaveApplicationHRForm if is_hr_or_above(self.request.user) else f.LeaveApplicationForm

    def _get_leave_type_quota(self, leave_type_obj):
        for field_name in ('days_per_year', 'annual_quota', 'days_allowed', 'quota', 'max_days', 'days'):
            if hasattr(leave_type_obj, field_name):
                val = getattr(leave_type_obj, field_name)
                if val is not None:
                    return Decimal(str(val))
        return Decimal('0.0')

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = m.LeaveType.objects.filter(company=company) if company else m.LeaveType.objects.all()

            is_female = bool(employee.gender and employee.gender.upper().startswith('F'))
            is_male = bool(employee.gender and employee.gender.upper().startswith('M'))
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            balance_record = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name
                norm_name = name.lower()

                # Dynamic Gender Filter
                # gender_rule = getattr(lt, 'gender_restriction', getattr(lt, 'gender', 'ALL'))
                # if gender_rule:
                #     g_str = str(gender_rule).upper()
                #     if 'FEMALE' in g_str and not is_female:
                #         continue
                #     if 'MALE' in g_str and not is_male:
                #         continue
                # 1. READ EXACT FIELD FROM DB: applicable_gender
                gender_rule = str(getattr(lt, 'applicable_gender', getattr(lt, 'gender', 'ALL')) or 'ALL').upper()

                # Filter out by Gender
                if 'FEMALE' in gender_rule or gender_rule == 'F':
                    if not is_female:
                        continue
                elif 'MALE' in gender_rule or gender_rule == 'M':
                    if not is_male:
                        continue

                annual_quota = lv.get_leave_type_annual_quota(lt)

                # Read Annual Balance directly from Master Leave Bank
                annual_balance = Decimal('0.0')
                if balance_record:
                    candidates = (code.lower() + '_leave', norm_name.replace(' ', '_') + '_leave',
                                  norm_name.replace(' ', '_'))
                    for attr in candidates:
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                # Maternity / Paternity only if assigned (> 0)
                is_maternity = 'matern' in norm_name or code in ('MATERNITY', 'MTL')
                is_paternity = 'patern' in norm_name or code in ('PATERNITY', 'PL')
                if (is_maternity or is_paternity) and annual_balance <= Decimal('0.0'):
                    continue

                if code in ('LWP', 'CO') or 'without pay' in norm_name or 'comp' in norm_name:
                    continue

                is_menstrual = code in ('ML', 'MENSTRUAL') or 'menstrual' in norm_name
                if not is_confirmed and not is_menstrual:
                    continue
                if is_confirmed and is_menstrual:
                    continue

                is_emergency = code in ('SL', 'BL') or any(
                    k in norm_name for k in ('sick', 'bereave', 'breave')) or is_maternity or is_paternity

                # 100% Dynamic calculation from DB
                monthly_quota = lv.calculate_monthly_quota(lt)
                monthly_available = lv.get_monthly_available_days(employee, lt, target_date=today)

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get_context_data(self, employee, form):
        today = date.today()
        cards = []

        if employee:
            company = employee.company
            leave_types_qs = m.LeaveType.objects.filter(company=company) if company else m.LeaveType.objects.all()

            # Dynamic Employee Attributes
            emp_gender = (employee.gender or '').upper()
            emp_marital = str(getattr(employee, 'marital_status', '') or '').lower()
            is_female = emp_gender.startswith('F')
            is_male = emp_gender.startswith('M')
            is_confirmed = bool(
                employee.date_of_confirmation
                and employee.employment_type == 'full_time'
                and 'probation' not in str(employee.status).lower()
            )

            balance_record = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()

            for lt in leave_types_qs:
                code = (lt.code or '').upper().strip()
                name = lt.name
                norm_name = name.lower()

                # --- 1. GENDER FILTER ---
                gender_rule = str(getattr(lt, 'applicable_gender', getattr(lt, 'gender', 'ALL')) or 'ALL').upper()
                if 'FEMALE' in gender_rule or gender_rule == 'F':
                    if not is_female:
                        continue
                elif 'MALE' in gender_rule or gender_rule == 'M':
                    if not is_male:
                        continue

                # --- 2. MARITAL STATUS FILTER ---
                marital_rule = str(getattr(lt, 'applicable_marital_status', 'ALL') or 'ALL').lower()
                if marital_rule in ['married'] and emp_marital != 'married':
                    continue  # Skip leave if it is strictly for married employees
                elif marital_rule in ['single', 'unmarried'] and emp_marital == 'married':
                    continue

                annual_quota = lv.get_leave_type_annual_quota(lt)

                # Read Annual Balance directly from Master Leave Bank
                annual_balance = Decimal('0.0')
                if balance_record:
                    candidates = (code.lower() + '_leave', norm_name.replace(' ', '_') + '_leave',
                                  norm_name.replace(' ', '_'))
                    for attr in candidates:
                        if hasattr(balance_record, attr):
                            val = getattr(balance_record, attr)
                            if val is not None:
                                annual_balance = Decimal(str(val))
                                break
                    else:
                        annual_balance = annual_quota
                else:
                    annual_balance = annual_quota

                # Maternity / Paternity only if assigned (> 0)
                is_maternity = 'matern' in norm_name or code in ('MATERNITY', 'MTL')
                is_paternity = 'patern' in norm_name or code in ('PATERNITY', 'PL')
                if (is_maternity or is_paternity) and annual_balance <= Decimal('0.0'):
                    continue

                if code == 'LWP' or 'without pay' in norm_name:
                    continue

                if code == 'CO' or 'comp' in norm_name:
                    co_avail = lv.get_monthly_available_days(employee, lt, target_date=today)
                    co_records = m.CompOffRecord.objects.filter(employee=employee)
                    from django.db.models import Sum
                    co_earned = co_records.aggregate(s=Sum('credits_earned'))['s'] or Decimal('0.0')
                    bank = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()
                    bank_co = Decimal(str(getattr(bank, 'comp_off', 0) or 0))
                    total_quota = max(co_earned, bank_co)
                    used_days = max(Decimal('0.0'), total_quota - co_avail)

                    if co_avail > Decimal('0.0') or total_quota > Decimal('0.0'):
                        leave_mode = getattr(employee, 'leave_policy_mode', 'annual') or 'annual'
                        cards.append({
                            'code': 'CO',
                            'name': 'Compensatory Off',
                            'is_emergency': False,
                            'is_comp_off': True,
                            'annual_quota': total_quota,
                            'annual_balance': co_avail,
                            'used_days': used_days,
                            'monthly_quota': total_quota,
                            'monthly_available': co_avail,
                            'leave_mode': leave_mode,
                        })
                    continue

                is_menstrual = code in ('ML', 'MENSTRUAL') or 'menstrual' in norm_name
                if not is_confirmed and not is_menstrual:
                    continue
                if is_confirmed and is_menstrual:
                    continue

                is_emergency = code in ('SL', 'BL') or any(
                    k in norm_name for k in ('sick', 'bereave', 'breave')) or is_maternity or is_paternity

                monthly_quota = lv.calculate_monthly_quota(lt)
                monthly_available = lv.get_monthly_available_days(employee, lt, target_date=today)

                leave_mode = getattr(employee, 'leave_policy_mode', 'annual') or 'annual'

                cards.append({
                    'code': code,
                    'name': name,
                    'is_emergency': is_emergency,
                    'annual_quota': annual_quota,
                    'annual_balance': annual_balance,
                    'monthly_quota': monthly_quota,
                    'monthly_available': monthly_available,
                    'leave_mode': leave_mode,
                })

        return {
            'form': form,
            'active_group': self.active_group,
            'active_item': self.active_item,
            'employee': employee,
            'dynamic_cards': cards,
        }

    def get(self, request):
        employee = get_employee_profile(request.user)
        form = self.get_form_class()()
        context = self.get_context_data(employee, form)
        return render(request, self.template_name, context)

    def post(self, request):
        form_class = self.get_form_class()
        form = form_class(request.POST, request.FILES)
        if form.is_valid():

            employee = form.cleaned_data.get('employee') if (
                        is_hr_or_above(request.user) and 'employee' in form.cleaned_data) else get_employee_profile(
                request.user)

            if employee is None:
                messages.error(request, "Your login isn't linked to an employee record.")
                return redirect('hrms:dashboard')

            # if form.is_valid():
            try:
                leave_app, notice_msg = lv.apply_leave(
                    employee=employee,
                    leave_type=form.cleaned_data['leave_type'],
                    start_date=form.cleaned_data['start_date'],
                    end_date=form.cleaned_data['end_date'],
                    day_type=form.cleaned_data.get('day_type', 'full'),
                    reason=form.cleaned_data.get('reason', ''),
                    supporting_document=request.FILES.get('supporting_document'),
                    relationship=form.cleaned_data.get('relationship', ''),
                    leave_stage=form.cleaned_data.get('leave_stage', ''),
                )
                # Trigger application notification to Reporting Manager
                send_leave_notification_email(leave_app, event_type='APPLIED')

                if notice_msg:
                    messages.warning(request, notice_msg)
                else:
                    messages.success(request, 'Leave application submitted successfully.')
                return redirect('hrms:my_leave')
            except lv.LeaveError as e:
                form.add_error(None, str(e))

        # 3. If form is invalid, re-render context using current user's profile
        current_emp = get_employee_profile(request.user)
        context = self.get_context_data(current_emp, form)
        return render(request, self.template_name, context)


class LeaveApproveView(HRRequiredMixin, View):
    """HR/SuperAdmin approves leave -> updates status to approved and deducts leave balance."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        try:
            lv.approve_leave(application, approver_user=request.user)
            # Notify HR that manager has approved
            send_leave_notification_email(application, event_type='HR_APPROVED')
            messages.success(request, f'Leave approved for {application.employee.full_name}.')
        except lv.LeaveError as e:
            messages.error(request, str(e))
        return redirect(request.META.get('HTTP_REFERER', 'hrms:my_leave'))


class LeaveRejectView(HRRequiredMixin, View):
    """HR/SuperAdmin rejects leave -> updates status to rejected with rejection reason."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        reason = request.POST.get('rejection_reason', '').strip()
        try:
            lv.reject_leave(application, approver_user=request.user, reason=reason)
            # Send rejection to Employee and CC Manager
            send_leave_notification_email(application, event_type='HR_REJECTED', reason=reason)
            messages.success(request, f'Leave rejected for {application.employee.full_name}.')
        except lv.LeaveError as e:
            messages.error(request, str(e))
        return redirect(request.META.get('HTTP_REFERER', 'hrms:my_leave'))


class ManagerDashboardView(RBACViewMixin, SidebarContextMixin, TemplateView):
    """Dashboard specifically for reporting managers and HR to view team leaves & attendance."""
    template_name = 'hrms/manager/manager_dashboard.html'
    active_group, active_item = 'manager', 'manager_dashboard'
    rbac_permission = "manager.dashboard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        emp = get_employee_profile(user)
        user_is_hr = is_hr_or_above(user) or user.is_superuser
        # Get subordinates
        if user_is_hr:
            subordinates = m.Employee.objects.filter(status='active').select_related('department', 'designation',
                                                                                     'company')
        elif emp:
            from access_control.hierarchy import get_descendant_employee_ids
            downline_ids = get_descendant_employee_ids(emp.id)
            if downline_ids:
                subordinates = m.Employee.objects.filter(id__in=downline_ids, status='active').select_related(
                    'department', 'designation', 'company')
            else:
                subordinates = m.Employee.objects.filter(reporting_manager=emp, status='active').select_related(
                    'department', 'designation', 'company')
        else:
            subordinates = m.Employee.objects.none()

        ctx['subordinates'] = subordinates
        ctx['subordinate_count'] = subordinates.count()

        # Subordinates' pending leaves
        if user_is_hr:
            pending_leaves = m.LeaveApplication.objects.filter(
                status__in=[m.LeaveApplication.Status.PENDING_MANAGER, m.LeaveApplication.Status.PENDING_HR,
                            m.LeaveApplication.Status.PENDING]
            ).select_related('employee', 'leave_type')
        elif emp:
            pending_leaves = m.LeaveApplication.objects.filter(
                Q(employee__reporting_manager=emp) | Q(employee__in=subordinates),
                status=m.LeaveApplication.Status.PENDING_MANAGER
            ).select_related('employee', 'leave_type').distinct()
        else:
            pending_leaves = m.LeaveApplication.objects.none()

        ctx['pending_leaves'] = pending_leaves

        # Today's team attendance
        today = timezone.localdate()
        today_attendance = m.AttendanceRecord.objects.filter(
            employee__in=subordinates,
            attendance_date=today
        ).select_related('employee')
        today_att_map = {att.employee_id: att for att in today_attendance}

        # Build attendance status for each subordinate today
        team_today_list = []
        for sub in subordinates:
            att = today_att_map.get(sub.id)
            team_today_list.append({
                'employee': sub,
                'record': att,
                'status': att.get_status_display() if att else 'Not Checked In',
                'check_in': att.check_in if att else None,
                'check_out': att.check_out if att else None,
                'late_minutes': att.late_minutes if att else 0,
                'is_half_day': att.is_half_day if att else False,
            })
        ctx['team_today_list'] = team_today_list

        # Team monthly summary
        month_start = today.replace(day=1)
        month_records = m.AttendanceRecord.objects.filter(
            employee__in=subordinates,
            attendance_date__gte=month_start,
            attendance_date__lte=today
        )
        ctx['team_stats'] = {
            'present': month_records.filter(status=m.AttendanceRecord.Status.PRESENT).count(),
            'half_day': month_records.filter(status=m.AttendanceRecord.Status.HALF_DAY).count(),
            'on_leave': month_records.filter(status=m.AttendanceRecord.Status.ON_LEAVE).count(),
            'absent': month_records.filter(status=m.AttendanceRecord.Status.ABSENT).count(),
        }

        ctx['reject_form'] = f.LeaveRejectForm()
        return ctx


class ManagerLeaveApproveView(LoginRequiredMixin, View):
    """Manager approves subordinate leave -> forwards to HR (status: PENDING_HR)."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        emp = get_employee_profile(request.user)
        is_hr = is_hr_or_above(request.user) or request.user.is_superuser

        # Check authorization
        if not is_hr and (not emp or application.employee.reporting_manager != emp):
            messages.error(request, "You are not authorized to approve this leave request.")
            return redirect('hrms:my_leave')

        try:
            lv.manager_approve_leave(application, approver_user=request.user)
            # Notify HR that manager has approved
            send_leave_notification_email(application, event_type='MANAGER_APPROVED')
            messages.success(request, f'Leave for {application.employee.full_name} approved and escalated to HR.')
        except lv.LeaveError as e:
            messages.error(request, str(e))
        return redirect(request.META.get('HTTP_REFERER', 'hrms:my_leave'))


class ManagerLeaveRejectView(LoginRequiredMixin, View):
    """Manager rejects subordinate leave with reason."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        emp = get_employee_profile(request.user)
        is_hr = is_hr_or_above(request.user)

        if not is_hr and (not emp or application.employee.reporting_manager != emp):
            messages.error(request, "You are not authorized to reject this leave request.")
            return redirect('hrms:my_leave')

        reason = request.POST.get('rejection_reason', '').strip()
        try:
            lv.reject_leave(application, approver_user=request.user, reason=reason)
            # Notify HR that manager has approved
            send_leave_notification_email(application, event_type='MANAGER_APPROVED')
            messages.success(request, f'Leave for {application.employee.full_name} rejected.')
        except lv.LeaveError as e:
            messages.error(request, str(e))
        return redirect(request.META.get('HTTP_REFERER', 'hrms:my_leave'))


class ManagerLeaveApproveView(LoginRequiredMixin, View):
    """Manager/Admin approves subordinate leave."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        user = request.user
        emp = get_employee_profile(user)
        is_hr = is_hr_or_above(user) or user.is_superuser

        # Check authorization
        if not is_hr and (not emp or application.employee.reporting_manager != emp):
            messages.error(request, "You are not authorized to approve this leave request.")
            return redirect(request.META.get('HTTP_REFERER', 'hrms:manager_dashboard'))

        try:
            # If the application is already pending HR, or a superuser/HR is giving final approval:
            if application.status == m.LeaveApplication.Status.PENDING_HR and is_hr:
                # Resolve the general approval function in leave_logic
                approve_fn = getattr(lv, 'approve_leave', getattr(lv, 'hr_approve', None))
                if approve_fn:
                    approve_fn(application, approver_user=user)
                else:
                    # Fallback directly to manager approval if no dedicated HR function exists
                    lv.manager_approve_leave(application, approver_user=user)

                send_leave_notification_email(application, event_type='HR_APPROVED')
                messages.success(request, f'Leave for {application.employee.full_name} fully approved.')
            else:
                lv.manager_approve_leave(application, approver_user=user)
                send_leave_notification_email(application, event_type='MANAGER_APPROVED')
                messages.success(request, f'Leave for {application.employee.full_name} approved and escalated to HR.')

        except lv.LeaveError as e:
            messages.error(request, str(e))

        return redirect(request.META.get('HTTP_REFERER', 'hrms:manager_dashboard'))


class ManagerLeaveRejectView(LoginRequiredMixin, View):
    """Manager/Admin rejects subordinate leave with reason."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        user = request.user
        emp = get_employee_profile(user)
        is_hr = is_hr_or_above(user) or user.is_superuser

        if not is_hr and (not emp or application.employee.reporting_manager != emp):
            messages.error(request, "You are not authorized to reject this leave request.")
            return redirect(request.META.get('HTTP_REFERER', 'hrms:manager_dashboard'))

        reason = request.POST.get('rejection_reason', '').strip()
        try:
            lv.reject_leave(application, approver_user=user, reason=reason)
            evt = 'HR_REJECTED' if is_hr else 'MANAGER_REJECTED'
            send_leave_notification_email(application, event_type=evt, reason=reason)
            messages.success(request, f'Leave for {application.employee.full_name} rejected.')
        except lv.LeaveError as e:
            messages.error(request, str(e))

        return redirect(request.META.get('HTTP_REFERER', 'hrms:manager_dashboard'))


class PenaltyListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    """Penalties page for tracking late arrival penalties and automatic leave deductions."""
    model = m.AttendancePenalty
    template_name = 'hrms/attendance/penalty_list.html'
    context_object_name = 'penalties'
    active_group, active_item = 'attendance', 'penalties'
    paginate_by = 20

    def get_queryset(self):
        qs = m.AttendancePenalty.objects.select_related(
            'employee', 'employee__department', 'employee__designation', 'attendance_record'
        ).order_by('-penalty_date', '-created_at')

        user = self.request.user
        emp = get_employee_profile(user)
        active_company_id = self.request.session.get('active_company_id')

        if not is_hr_or_above(user):
            if emp is None:
                return m.AttendancePenalty.objects.none()
            if emp.is_manager:
                qs = qs.filter(Q(employee=emp) | Q(employee__reporting_manager=emp))
            else:
                qs = qs.filter(employee=emp)
        elif active_company_id and active_company_id != 'all':
            qs = qs.filter(employee__company_id=active_company_id)

        # Text search (like in employee page)
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(employee__first_name__icontains=q) |
                Q(employee__last_name__icontains=q) |
                Q(employee__employee_code__icontains=q) |
                Q(reason__icontains=q)
            )

        employee_id = self.request.GET.get('employee')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        department_id = self.request.GET.get('department')

        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        if start_date:
            qs = qs.filter(penalty_date__gte=start_date)
        if end_date:
            qs = qs.filter(penalty_date__lte=end_date)
        if department_id:
            qs = qs.filter(employee__department_id=department_id)

        # Tab filter: all, grace (grace exhausted half days), other (other penalties)
        penalty_type = self.request.GET.get('penalty_type', 'all').strip()
        grace_filter = Q(reason__icontains='grace') | (Q(reason__icontains='late') & Q(deduction_days=Decimal('0.5')))

        if penalty_type == 'grace':
            qs = qs.filter(grace_filter)
        elif penalty_type == 'other':
            qs = qs.filter(~grace_filter)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        user_is_hr = is_hr_or_above(user)
        emp = get_employee_profile(user)
        ctx['is_hr'] = user_is_hr

        # Calculate counts across scope
        scope_qs = m.AttendancePenalty.objects.select_related(
            'employee', 'employee__department'
        )
        active_company_id = self.request.session.get('active_company_id')
        if not user_is_hr:
            if emp and emp.is_manager:
                scope_qs = scope_qs.filter(Q(employee=emp) | Q(employee__reporting_manager=emp))
            elif emp:
                scope_qs = scope_qs.filter(employee=emp)
            else:
                scope_qs = scope_qs.none()
        elif active_company_id and active_company_id != 'all':
            scope_qs = scope_qs.filter(employee__company_id=active_company_id)

        grace_filter = Q(reason__icontains='grace') | (Q(reason__icontains='late') & Q(deduction_days=Decimal('0.5')))
        total_count = scope_qs.count()
        grace_count = scope_qs.filter(grace_filter).count()
        other_count = scope_qs.filter(~grace_filter).count()

        ctx['tab_counts'] = {
            'all': total_count,
            'grace': grace_count,
            'other': other_count
        }

        current_qs = self.get_queryset()
        stats_agg = current_qs.aggregate(
            total_penalties=Count('id'),
            total_days_deducted=Sum('deduction_days'),
            total_late_minutes=Sum('late_minutes')
        )
        ctx['stats'] = {
            'total_penalties': stats_agg['total_penalties'] or 0,
            'total_days_deducted': stats_agg['total_days_deducted'] or Decimal('0.0'),
            'total_late_minutes': stats_agg['total_late_minutes'] or 0,
        }

        emp_scope = m.Employee.objects.filter(status='active')
        dept_scope = m.Department.objects.all()

        if active_company_id and active_company_id != 'all':
            emp_scope = emp_scope.filter(company_id=active_company_id)
            dept_scope = dept_scope.filter(company_id=active_company_id)

        if user_is_hr:
            ctx['employees'] = emp_scope.order_by('first_name')
        elif emp and emp.is_manager:
            ctx['employees'] = emp_scope.filter(reporting_manager=emp).order_by('first_name')
        else:
            ctx['employees'] = emp_scope.filter(pk=emp.pk) if emp else emp_scope.none()

        ctx['departments'] = dept_scope
        ctx['penalty_type'] = self.request.GET.get('penalty_type', 'all')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['filters'] = self.request.GET
        return ctx


class LeaveCancelView(LoginRequiredMixin, View):
    """The employee themself, or HR on their behalf, can cancel a pending/approved application."""

    def post(self, request, pk):
        application = get_object_or_404(m.LeaveApplication, pk=pk)
        is_hr = is_hr_or_above(request.user)
        employee = get_employee_profile(request.user)
        try:
            lv.cancel_leave(application, requested_by_employee=employee, is_hr=is_hr)
            messages.success(request, 'Leave application cancelled.')
        except lv.LeaveError as e:
            messages.error(request, str(e))
        return redirect('hrms:my_leave')


class LeaveBankListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.EmployeeLeaveBalance
    template_name = 'hrms/leave/leave_bank.html'
    context_object_name = 'leave_balances'
    active_group, active_item = 'leave', 'leave-bank'

    def get_queryset(self):
        user = self.request.user
        active_statuses = [
            m.Employee.Status.ACTIVE,
            m.Employee.Status.ON_LEAVE,
            m.Employee.Status.SUSPENDED
        ]

        base_qs = m.EmployeeLeaveBalance.objects.filter(
            e_name__status__in=active_statuses
        ).select_related(
            'e_name',
            'e_name__department',
            'e_name__designation',
            'e_name__company'
        )

        # 1. Superadmin and HR see all company employees
        if user.is_superuser or is_hr_or_above(user):
            active_id = self.request.session.get('active_company_id')
            if active_id and active_id != 'all':
                base_qs = base_qs.filter(e_name__company_id=active_id)
            return base_qs

        # 2. Reporting Managers see their direct team & self
        emp = get_employee_profile(user)
        if emp and (emp.is_manager or m.Employee.objects.filter(reporting_manager=emp).exists()):
            return base_qs.filter(
                Q(e_name=emp) | Q(e_name__reporting_manager=emp)
            )

        # 3. Standard Employee sees only their own leave balance record
        if emp:
            return base_qs.filter(e_name=emp)
        return m.EmployeeLeaveBalance.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        context['active_records'] = qs.filter(e_name__status__in=[m.Employee.Status.ACTIVE, m.Employee.Status.ON_LEAVE])
        context['inactive_records'] = qs.exclude(
            e_name__status__in=[m.Employee.Status.ACTIVE, m.Employee.Status.ON_LEAVE])
        context['is_hr'] = is_hr_or_above(self.request.user)

        # Superadmin / HR bulk editing formset
        if self.request.user.is_superuser or is_hr_or_above(self.request.user):
            LeaveFormSet = modelformset_factory(
                m.EmployeeLeaveBalance,
                form=f.LeaveBalanceForm,
                extra=0
            )
            context['formset'] = kwargs.get('formset') or LeaveFormSet(queryset=qs)

        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        context['active_records'] = qs.filter(
            e_name__status__in=[
                m.Employee.Status.ACTIVE,
                m.Employee.Status.ON_LEAVE,
            ]
        )
        context['inactive_records'] = qs.exclude(
            e_name__status__in=[
                m.Employee.Status.ACTIVE,
                m.Employee.Status.ON_LEAVE,
            ]
        )
        context['is_hr'] = is_hr_or_above(self.request.user)

        # Pull dynamic leave type policy quotas from DB
        active_id = self.request.session.get('active_company_id')
        l_types = m.LeaveType.objects.all()
        if active_id and active_id != 'all':
            l_types = l_types.filter(company_id=active_id)
        context['leave_type_map'] = {lt.code: lt for lt in l_types}

        # Formset for Superadmin / HR inline updates
        if self.request.user.is_superuser or is_hr_or_above(self.request.user):
            LeaveFormSet = modelformset_factory(
                m.EmployeeLeaveBalance, form=f.LeaveBalanceForm, extra=0
            )
            context['formset'] = kwargs.get('formset') or LeaveFormSet(queryset=qs)

        return context

    def post(self, request, *args, **kwargs):
        # 1. Security Check: Only Superadmin / HR can modify or trigger bulk calculation
        if not (request.user.is_superuser or is_hr_or_above(request.user)):
            messages.error(request, "Access Denied: Only HR/Superadmin can modify leave balances.")
            return redirect('hrms:leave-bank')

        action = request.POST.get('action')

        # 2. Handle 'Calculate All' Bulk Sync Action
        if action == 'calculate_all':
            active_id = request.session.get('active_company_id')
            emp_qs = m.Employee.objects.filter(status__in=[m.Employee.Status.ACTIVE, m.Employee.Status.ON_LEAVE])
            if active_id and active_id != 'all':
                emp_qs = emp_qs.filter(company_id=active_id)
            count = 0
            for emp in emp_qs:
                lv.sync_employee_leave_bank(emp)
                count += 1
            messages.success(request, f"Successfully recalculated leave bank balances for {count} active employees.")
            return redirect('hrms:leave-bank')

        # 3. Handle Formset Submission (Manual Bulk Edit)
        LeaveFormSet = modelformset_factory(
            m.EmployeeLeaveBalance,
            form=f.LeaveBalanceForm,
            extra=0
        )
        formset = LeaveFormSet(request.POST)

        if formset.is_valid():
            formset.save()
            messages.success(request, "Leave bank balances updated successfully.")
            return redirect('hrms:leave-bank')

        messages.error(request, "Error saving leave balance changes. Please review the highlighted fields.")
        return self.render_to_response(self.get_context_data(formset=formset))


# PAYROLL & SALARY STRUCTURE ENGINE
# ===========================================================================
import calendar as _calendar

from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.http import HttpResponse

from . import payroll_logic as pay


# ---------------------------------------------------------------------------
# Salary Structure + Components — HR/Admin manage
# ---------------------------------------------------------------------------
class SalaryStructureListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.SalaryStructure
    template_name = 'hrms/payroll/salarystructure_list.html'
    context_object_name = 'structures'
    active_group, active_item = 'payroll', 'salarystructure'

    def get_queryset(self):
        return m.SalaryStructure.objects.select_related('company').order_by('company__name', 'name')


class SalaryStructureCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.SalaryStructure
    form_class = f.SalaryStructureForm
    template_name = 'hrms/payroll/salarystructure_form.html'
    active_group, active_item = 'payroll', 'salarystructure'

    def form_valid(self, form):
        messages.success(self.request, 'Salary structure created.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:salarystructure_detail', kwargs={'pk': self.object.pk})


class SalaryStructureUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.SalaryStructure
    form_class = f.SalaryStructureForm
    template_name = 'hrms/payroll/salarystructure_form.html'
    active_group, active_item = 'payroll', 'salarystructure'

    def form_valid(self, form):
        messages.success(self.request, 'Salary structure updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:salarystructure_detail', kwargs={'pk': self.object.pk})


class SalaryStructureDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.SalaryStructure
    template_name = 'hrms/payroll/salarystructure_confirm_delete.html'
    success_url = reverse_lazy('hrms:salarystructure_list')
    active_group, active_item = 'payroll', 'salarystructure'

    def form_valid(self, form):
        messages.success(self.request, 'Salary structure deleted.')
        return super().form_valid(form)


class SalaryStructureDetailView(HRRequiredMixin, SidebarContextMixin, DetailView):
    model = m.SalaryStructure
    template_name = 'hrms/payroll/salarystructure_detail.html'
    context_object_name = 'structure'
    active_group, active_item = 'payroll', 'salarystructure'


class SalaryComponentCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.SalaryComponent
    form_class = f.SalaryComponentForm
    template_name = 'hrms/payroll/salarycomponent_form.html'
    active_group, active_item = 'payroll', 'salarystructure'

    def dispatch(self, request, *args, **kwargs):
        self.structure = get_object_or_404(m.SalaryStructure, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['structure'] = self.structure
        return ctx

    def form_valid(self, form):
        form.instance.structure = self.structure
        messages.success(self.request, 'Component added.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:salarystructure_detail', kwargs={'pk': self.structure.pk})


class SalaryComponentDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.SalaryComponent
    template_name = 'hrms/payroll/salarycomponent_confirm_delete.html'
    active_group, active_item = 'payroll', 'salarystructure'

    def get_success_url(self):
        messages.success(self.request, 'Component removed.')
        return reverse('hrms:salarystructure_detail', kwargs={'pk': self.object.structure.pk})


# ---------------------------------------------------------------------------
# Employee Salary — HR/Admin manage; drives payroll processing
# ---------------------------------------------------------------------------
from django.db.models import Sum, Avg


class EmployeeSalaryListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.EmployeeSalary
    template_name = 'hrms/payroll/employee_salary_list.html'
    context_object_name = 'salaries'
    active_group, active_item = 'payroll', 'employee_salary'

    def get_queryset(self):
        qs = m.EmployeeSalary.objects.select_related('employee', 'employee__designation').order_by('-is_active',
                                                                                                   'employee__first_name')
        emp_id = self.request.GET.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        active_qs = m.EmployeeSalary.objects.filter(is_active=True)

        # Financial Stats
        ctx['total_ctc'] = active_qs.aggregate(Sum('ctc_annual'))['ctc_annual__sum'] or 0
        ctx['avg_ctc'] = active_qs.aggregate(Avg('ctc_annual'))['ctc_annual__avg'] or 0
        ctx['active_count'] = active_qs.count()

        ctx['employees'] = m.Employee.objects.all()
        ctx['selected_employee'] = self.request.GET.get('employee')
        return ctx


class EmployeeSalaryCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.EmployeeSalary
    form_class = f.EmployeeSalaryForm
    template_name = 'hrms/payroll/employee_salary_form.html'
    success_url = reverse_lazy('hrms:employee_salary_list')
    active_group, active_item = 'payroll', 'employee_salary'

    def form_valid(self, form):
        messages.success(self.request, 'Salary structure assigned to employee.')
        return super().form_valid(form)


class EmployeeSalaryUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.EmployeeSalary
    form_class = f.EmployeeSalaryForm
    template_name = 'hrms/payroll/employee_salary_form.html'
    success_url = reverse_lazy('hrms:employee_salary_list')
    active_group, active_item = 'payroll', 'employee_salary'

    def form_valid(self, form):
        messages.success(self.request, 'Employee salary updated.')
        return super().form_valid(form)


from django.utils import timezone
from datetime import datetime, timedelta, date
import calendar


class EmployeePunchReportView(HRRequiredMixin, SidebarContextMixin, DetailView):
    model = m.Employee
    template_name = 'hrms/attendance/punch_report.html'
    context_object_name = 'target_employee'
    pk_url_kwarg = 'emp_id'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        emp = self.object
        month = int(self.kwargs['month'])
        year = int(self.kwargs['year'])
        comp = emp.company

        days_in_month = calendar.monthrange(year, month)[1]
        report_data = []

        stats = {'FD': 0, 'HD': 0, 'ABS': 0, 'Grace': 0, 'Leave': 0}

        records = {r.attendance_date: r for r in m.AttendanceRecord.objects.filter(
            employee=emp, attendance_date__year=year, attendance_date__month=month)}

        holiday_dates = []
        if emp.holiday_calendar:
            holiday_dates = m.Holiday.objects.filter(
                calendar=emp.holiday_calendar, date__year=year, date__month=month
            ).values_list('date', flat=True)

        leaves = m.LeaveApplication.objects.filter(
            employee=emp, status='approved',
            start_date__lte=date(year, month, days_in_month),
            end_date__gte=date(year, month, 1)
        ).select_related('leave_type')

        leave_map = {dt: l.leave_type.code.upper() for l in leaves for dt in
                     [l.start_date + timedelta(days=x) for x in range((l.end_date - l.start_date).days + 1)]
                     if dt.month == month and dt.year == year}

        grace_used = 0

        for d in range(1, days_in_month + 1):
            dt = date(year, month, d)
            policy = comp.get_policy_for_date(dt)

            off_start = policy.office_start_time
            off_end = policy.office_end_time
            grace_limit = policy.grace_allowed_count
            full_thresh = float(policy.full_day_threshold_hours)

            rec = records.get(dt)
            leave_code = leave_map.get(dt)
            is_holiday = dt in holiday_dates
            is_sunday = dt.weekday() == 6

            day_info = {
                'date': dt, 'in': None, 'out': None, 'hours': 0,
                'status': 'ABS', 'label': '', 'css': 'mark-abs',
                'in_lat': None, 'in_lng': None,
                'out_lat': None, 'out_lng': None,
                'punch_source': None, 'photo': None
            }

            if is_holiday or is_sunday:
                day_info.update({'status': 'HOL' if is_holiday else 'SUN', 'css': 'mark-sun'})

            elif rec and rec.check_in:
                local_in = timezone.localtime(rec.check_in)
                local_out = timezone.localtime(rec.check_out) if rec.check_out else None

                day_info.update({
                    'in': local_in,
                    'out': local_out,
                    'in_lat': rec.punch_in_latitude,
                    'in_lng': rec.punch_in_longitude,
                    'out_lat': rec.punch_out_latitude,
                    'out_lng': rec.punch_out_longitude,
                    'punch_source': getattr(rec, 'punch_source', 'mobile'),
                    'photo': rec.punch_in_photo.url if rec.punch_in_photo else None
                })

                p_in = local_in.time()
                p_out = local_out.time() if local_out else off_start
                eff_hours = (datetime.combine(dt, min(p_out, off_end)) -
                             datetime.combine(dt, max(p_in, off_start))).total_seconds() / 3600
                day_info['hours'] = round(eff_hours, 2)

                grace_deadline = (datetime.combine(dt, off_start) + timedelta(minutes=policy.grace_minutes)).time()

                if p_in <= off_start:
                    if day_info['hours'] >= full_thresh:
                        day_info.update({'status': 'FD', 'css': 'mark-fd'})
                        stats['FD'] += 1
                    else:
                        day_info.update({'status': 'HD', 'css': 'mark-hd', 'label': 'Short Duration'})
                        stats['HD'] += 1
                elif p_in <= grace_deadline:
                    if local_out and local_out.time() >= off_end:
                        if grace_used < grace_limit:
                            grace_used += 1
                            day_info.update(
                                {'status': 'FD', 'css': 'mark-fd', 'label': f'Grace Used ({grace_used}/{grace_limit})'})
                            stats['FD'] += 1
                            stats['Grace'] += 1
                        else:
                            day_info.update({'status': 'HD', 'css': 'mark-hd', 'label': 'Grace Exhausted'})
                            stats['HD'] += 1
                    else:
                        day_info.update({'status': 'HD', 'css': 'mark-hd', 'label': 'Late + Early Out'})
                        stats['HD'] += 1
                else:
                    day_info.update({'status': 'HD', 'css': 'mark-hd', 'label': 'Late Arrival'})
                    stats['HD'] += 1

            elif leave_code:
                day_info.update({'status': leave_code, 'css': 'mark-leave', 'label': 'Approved Leave'})
                stats['Leave'] += 1
            else:
                stats['ABS'] += 1

            report_data.append(day_info)

        ctx.update({
            'report': report_data,
            'stats': stats,
            'month_name': calendar.month_name[month],
            'year': year,
        })
        return ctx


class EmployeePunchReportView(HRRequiredMixin, SidebarContextMixin, DetailView):
    model = m.Employee
    template_name = 'hrms/attendance/punch_report.html'
    context_object_name = 'target_employee'
    pk_url_kwarg = 'emp_id'

    def get_context_data(self, **kwargs):
        """Delegates to the unified attendance_engine for consistency with the Matrix."""
        from .attendance_engine import build_attendance
        ctx = super().get_context_data(**kwargs)
        emp = self.object
        month = int(self.kwargs['month'])
        year = int(self.kwargs['year'])

        start_date = date(year, month, 1)
        end_date = date(year, month, calendar.monthrange(year, month)[1])
        engine_data = build_attendance([emp], start_date, end_date)[emp.id]

        report_data = []
        stats = {'FD': 0, 'HD': 0, 'ABS': 0, 'Grace': 0, 'Leave': 0}

        for d in engine_data['days']:
            rec = m.AttendanceRecord.objects.filter(
                employee=emp, attendance_date=d['date']
            ).select_related().first()  # only used for extra punch-detail fields
            day_info = {
                'date': d['date'],
                'in': d['check_in'],
                'out': d['check_out'],
                'hours': float(d['hours']),
                'status': d['badge'],
                'label': d['label'],
                'css': d['css'],
                'is_auto_leave': d['is_auto_leave'],
                'is_edited': d['is_edited'],
                'edit_tooltip': d['edit_tooltip'],
                'leave': d['leave'],
                'penalties': d['penalties'],
                'penalty_amount': d['penalty_amount'],
                # geo/photo from actual record if it exists
                'in_lat': getattr(rec, 'punch_in_latitude', None) if rec else None,
                'in_lng': getattr(rec, 'punch_in_longitude', None) if rec else None,
                'out_lat': getattr(rec, 'punch_out_latitude', None) if rec else None,
                'out_lng': getattr(rec, 'punch_out_longitude', None) if rec else None,
                'punch_source': getattr(rec, 'punch_source', None) if rec else None,
                'photo': (rec.punch_in_photo.url if rec and rec.punch_in_photo else None),
            }
            st = d['status']
            if st in ('present', 'grace'):
                stats['FD'] += 1
                if st == 'grace':
                    stats['Grace'] += 1
            elif st == 'half_day':
                stats['HD'] += 1
            elif st == 'on_leave':
                stats['Leave'] += 1
            elif st == 'absent':
                stats['ABS'] += 1
            report_data.append(day_info)

        ctx.update({
            'report': report_data,
            'stats': stats,
            'summary': engine_data['summary'],
            'month_name': calendar.month_name[month],
            'year': year,
        })
        return ctx

        days_in_month = calendar.monthrange(year, month)[1]
        report_data = []
        stats = {'FD': 0, 'HD': 0, 'ABS': 0, 'Grace': 0, 'Leave': 0}

        records = {
            r.attendance_date: r
            for r in m.AttendanceRecord.objects.filter(
                employee=emp, attendance_date__year=year, attendance_date__month=month
            )
        }

        holiday_dates = []
        if emp.holiday_calendar:
            holiday_dates = m.Holiday.objects.filter(
                calendar=emp.holiday_calendar, date__year=year, date__month=month
            ).values_list('date', flat=True)

        leaves = m.LeaveApplication.objects.filter(
            employee=emp,
            status=getattr(m.LeaveApplication.Status, 'APPROVED', 'approved'),
            start_date__lte=date(year, month, days_in_month),
            end_date__gte=date(year, month, 1)
        ).select_related('leave_type')

        leave_map = {}
        for l in leaves:
            c = max(l.start_date, date(year, month, 1))
            end_bound = min(l.end_date, date(year, month, days_in_month))
            while c <= end_bound:
                reason_lower = (l.reason or '').lower()
                is_auto = 'auto-approved' in reason_lower or 'auto-approval' in reason_lower
                leave_map[c] = {
                    'code': (l.leave_type.code or '').upper(),
                    'name': l.leave_type.name,
                    'is_auto': is_auto,
                    'day_type': getattr(l, 'day_type', 'full'),
                    'reason': l.reason or 'Approved Leave',
                }
                c += timedelta(days=1)

        grace_used = 0

        for d in range(1, days_in_month + 1):
            dt = date(year, month, d)
            policy = comp.get_policy_for_date(dt)

            off_start = policy.office_start_time
            off_end = policy.office_end_time
            grace_limit = policy.grace_allowed_count
            full_thresh = float(policy.full_day_threshold_hours)

            rec = records.get(dt)
            leave_info = leave_map.get(dt)
            is_holiday = dt in holiday_dates
            is_sunday = dt.weekday() == 6

            rec_remarks = rec.remarks if rec and rec.remarks else ''
            rec_is_auto = 'auto-approved' in rec_remarks.lower()

            day_info = {
                'date': dt, 'in': None, 'out': None, 'hours': 0,
                'status': 'ABS', 'label': '', 'css': 'mark-abs',
                'in_lat': None, 'in_lng': None,
                'out_lat': None, 'out_lng': None,
                'punch_source': None, 'photo': None,
                'is_auto_leave': bool(rec_is_auto or (leave_info and leave_info['is_auto'])),
            }

            if is_holiday or is_sunday:
                day_info.update({'status': 'HOL' if is_holiday else 'SUN', 'css': 'mark-sun'})

            elif rec and rec.check_in:
                local_in = timezone.localtime(rec.check_in)
                local_out = timezone.localtime(rec.check_out) if rec.check_out else None

                day_info.update({
                    'in': local_in, 'out': local_out,
                    'in_lat': rec.punch_in_latitude, 'in_lng': rec.punch_in_longitude,
                    'out_lat': rec.punch_out_latitude, 'out_lng': rec.punch_out_longitude,
                    'punch_source': getattr(rec, 'punch_source', 'mobile'),
                    'photo': rec.punch_in_photo.url if rec.punch_in_photo else None
                })

                p_in = local_in.time()
                p_out = local_out.time() if local_out else off_start
                eff_hours = (datetime.combine(dt, min(p_out, off_end)) -
                             datetime.combine(dt, max(p_in, off_start))).total_seconds() / 3600
                day_info['hours'] = round(eff_hours, 2)
                grace_deadline = (datetime.combine(dt, off_start) + timedelta(minutes=policy.grace_minutes)).time()

                # --- CRITICAL FIX: If status was manually updated to 'present', display FD and suppress old leave ---
                if str(rec.status).lower() in ['present', 'fd']:
                    day_info.update({
                        'status': 'FD',
                        'css': 'mark-fd',
                        'label': rec.remarks or 'Present',
                        'is_auto_leave': False,  # Suppress the yellow Auto-Approved tag
                    })
                    stats['FD'] += 1
                else:
                    # Regular punch evaluation
                    punch_status = 'HD'
                    punch_label = ''

                    if p_in <= off_start:
                        if day_info['hours'] >= full_thresh:
                            punch_status = 'FD'
                        else:
                            punch_status = 'HD'
                            punch_label = 'Short Duration'
                    elif p_in <= grace_deadline:
                        if local_out and local_out.time() >= off_end:
                            if grace_used < grace_limit:
                                grace_used += 1
                                punch_status = 'FD'
                                punch_label = f'Grace Used ({grace_used}/{grace_limit})'
                                stats['Grace'] += 1
                            else:
                                punch_status = 'HD'
                                punch_label = 'Grace Exhausted'
                        else:
                            punch_status = 'HD'
                            punch_label = 'Late + Early Out'
                    else:
                        punch_status = 'HD'
                        punch_label = 'Late Arrival'

                    if punch_status == 'HD' and leave_info:
                        leave_code = leave_info['code']
                        auto_flag = " (Auto-Approved)" if day_info['is_auto_leave'] else ""
                        day_info.update({
                            'status': f"HD + {leave_code}",
                            'css': 'mark-hd',
                            'label': f"Half Day worked + {leave_code}{auto_flag}",
                        })
                        stats['HD'] += 1
                    elif punch_status == 'FD':
                        day_info.update({'status': 'FD', 'css': 'mark-fd', 'label': punch_label})
                        stats['FD'] += 1
                    else:
                        day_info.update({'status': 'HD', 'css': 'mark-hd', 'label': punch_label})
                        stats['HD'] += 1

            elif leave_info:
                leave_code = leave_info['code']
                label_text = 'Auto-Approved Leave' if day_info['is_auto_leave'] else 'Approved Leave'
                day_info.update({
                    'status': leave_code,
                    'css': 'mark-leave',
                    'label': label_text,
                })
                stats['Leave'] += 1

            elif rec and rec.status == getattr(m.AttendanceRecord.Status, 'ON_LEAVE', 'on_leave'):
                label_text = 'Auto-Approved Leave' if day_info['is_auto_leave'] else 'On Leave'
                day_info.update({
                    'status': 'LEAVE',
                    'css': 'mark-leave',
                    'label': rec.remarks or label_text,
                })
                stats['Leave'] += 1

            else:
                stats['ABS'] += 1

            report_data.append(day_info)

        ctx.update({
            'report': report_data,
            'stats': stats,
            'month_name': calendar.month_name[month],
            'year': year,
        })
        return ctx


# ------------------autoapprovalleavelogic-----------------------------

from django.views import View
from django.shortcuts import redirect
from django.contrib import messages
from .permissions import HRRequiredMixin
from .leave_logic import auto_convert_absent_to_leaves


class AutoApproveAbsentLeaveView(HRRequiredMixin, View):
    """POST endpoint to convert absent days.0656 001o CL/EL/LWP for selected employees."""

    def post(self, request, *args, **kwargs):
        emp_ids = request.POST.getlist('selected_employees')
        year = int(request.POST.get('year', date.today().year))
        month = int(request.POST.get('month', date.today().month))

        if not emp_ids:
            messages.warning(request, "No employees selected for auto-approval.")
            return redirect(request.META.get('HTTP_REFERER', 'hrms:attendance-matrix'))

        results = auto_convert_absent_to_leaves(emp_ids, year, month, request.user)

        messages.success(
            request,
            f"Successfully processed {results['total_employees']} confirmed employees. "
            f"Converted {results['converted_days']} absent days "
            f"(CL: {results['cl_deducted']}, EL: {results['el_deducted']}, LWP: {results['lwp_count']})."
        )
        return redirect(f"/hrms/attendance/matrix/?month={month}&year={year}")


class AutoApproveAbsentLeaveView(HRRequiredMixin, View):
    """POST endpoint to convert absent days to CL/EL/LWP for selected employees."""

    def post(self, request, *args, **kwargs):
        # Read liEmployeePunchReportViewst of selected employee IDs submitted from checkboxes
        emp_ids = request.POST.getlist('selected_employees')

        # Parse year and month with safe fallbacks
        try:
            year = int(request.POST.get('year') or date.today().year)
            month = int(request.POST.get('month') or date.today().month)
        except ValueError:
            year = date.today().year
            month = date.today().month

        # Validate that positive month numbers are processed
        if month < 1 or month > 12:
            month = date.today().month

        # Check if at least one employee was checked
        if not emp_ids:
            messages.warning(request, "No employees selected for auto-approval.")
            return redirect(request.META.get('HTTP_REFERER', 'hrms:attendance_matrix'))

        # Run the conversion logic
        results = auto_convert_absent_to_leaves(emp_ids, year, month, request.user)

        # Safely extract dictionary keys to prevent any KeyError
        total_emp = results.get('total_employees', 0)
        converted = results.get('converted_days', 0)
        cl = results.get('cl_deducted', 0)
        el = results.get('el_deducted', 0)
        lwp = results.get('lwp_count', 0)

        # Notify user of converted counts
        messages.success(
            request,
            f"Successfully processed {total_emp} confirmed employees. "
            f"Converted {converted} absent days (CL: {cl}, EL: {el}, LWP: {lwp})."
        )
        return redirect(f"/hrms/attendance/matrix/?month={month}&year={year}")

        # ---------------------------------------------------------------------------


# Loans/Advances & Payroll Extras (Incentives) — HR/Admin manage
# ---------------------------------------------------------------------------
class LoanAdvanceListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.LoanAdvance
    template_name = 'hrms/payroll/loan_list.html'
    context_object_name = 'loans'
    active_group, active_item = 'payroll', 'loan'

    def get_queryset(self):
        return m.LoanAdvance.objects.select_related('employee').order_by('-is_active', '-start_date')


class LoanAdvanceCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.LoanAdvance
    form_class = f.LoanAdvanceForm
    template_name = 'hrms/payroll/loan_form.html'
    success_url = reverse_lazy('hrms:loan_list')
    active_group, active_item = 'payroll', 'loan'

    def form_valid(self, form):
        messages.success(self.request, 'Loan/advance recorded.')
        return super().form_valid(form)


class LoanAdvanceUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.LoanAdvance
    form_class = f.LoanAdvanceForm
    template_name = 'hrms/payroll/loan_form.html'
    success_url = reverse_lazy('hrms:loan_list')
    active_group, active_item = 'payroll', 'loan'

    def form_valid(self, form):
        messages.success(self.request, 'Loan/advance updated.')
        return super().form_valid(form)


class PayrollExtraListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.PayrollExtra
    template_name = 'hrms/payroll/extra_list.html'
    context_object_name = 'extras'
    active_group, active_item = 'payroll', 'extra'

    def get_queryset(self):
        return m.PayrollExtra.objects.select_related('employee').order_by('is_consumed', '-created_at')


class PayrollExtraCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.PayrollExtra
    form_class = f.PayrollExtraForm
    template_name = 'hrms/payroll/extra_form.html'
    success_url = reverse_lazy('hrms:extra_list')
    active_group, active_item = 'payroll', 'extra'

    def form_valid(self, form):
        messages.success(self.request, 'Incentive/extra queued for the employee\'s next payroll run.')
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Payroll Runs — the engine
# ---------------------------------------------------------------------------
class PayrollRunListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.PayrollRun
    template_name = 'hrms/payroll/payrollrun_list.html'
    context_object_name = 'runs'
    active_group, active_item = 'payroll', 'payrollrun'

    def get_queryset(self):
        return m.PayrollRun.objects.select_related('company').order_by('-year', '-month')

    def get_queryset(self):
        qs = super().get_queryset().select_related('company').order_by('-year', '-month')
        active_id = self.request.session.get('active_company_id')

        # Scoped strictly to selected company
        if active_id and active_id != 'all':
            qs = qs.filter(company_id=active_id)
        elif not self.request.user.is_superuser:
            emp = get_employee_profile(self.request.user)
            if emp and emp.company:
                qs = qs.filter(company=emp.company)
        return qs


class PayrollProcessView(RBACViewMixin, SidebarContextMixin, View):
    """Accountant/Admin selects Company + Month + Year; process_payroll() loops
    every active employee in that company and creates/updates their PaySlip.
    Renders the Bootstrap 5 payroll summary table directly after processing."""
    active_group, active_item = 'payroll', 'payrollrun'
    template_name = 'hrms/payroll/payroll_process.html'
    rbac_permission = "payroll.run"

    def get(self, request):
        form = f.PayrollProcessForm(initial={
            'month': timezone.localdate().month, 'year': timezone.localdate().year,
        })
        return render(request, self.template_name, {
            'form': form, 'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request):
        form = f.PayrollProcessForm(request.POST)
        if form.is_valid():
            company = form.cleaned_data['company']
            month = int(form.cleaned_data['month'])
            year = form.cleaned_data['year']
            payroll_run, warnings = pay.process_payroll(company, year, month, user=request.user)
            for w in warnings:
                messages.warning(request, w)
            messages.success(
                request,
                f'Payroll processed for {company} — {_calendar.month_name[month]} {year} '
                f'({payroll_run.payslips.count()} employee(s)).'
            )
            return redirect('hrms:payrollrun_detail', pk=payroll_run.pk)
        return render(request, self.template_name, {
            'form': form, 'active_group': self.active_group, 'active_item': self.active_item,
        })


from django.db.models import F, Q, Sum
import csv


class PayrollRunDetailView(HRRequiredMixin, SidebarContextMixin, DetailView):
    """The Bootstrap 5 payroll summary table for one processed run."""
    model = m.PayrollRun
    template_name = 'hrms/payroll/payrollrun_detail.html'
    context_object_name = 'run'
    active_group, active_item = 'payroll', 'payrollrun'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['payslips'] = (
            self.object.payslips
            .select_related('employee', 'employee__designation')
            .order_by('employee__employee_code')
        )
        ctx['totals'] = self.object.payslips.aggregate(
            total_net=Sum('net_pay'), total_earnings=Sum('total_earnings'), total_deductions=Sum('total_deductions'))
        return ctx

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payroll_run = self.object

        # 1. Base Queryset scoped to the PayrollRun's Company
        payslips = (
            payroll_run.payslips.filter(employee__company=payroll_run.company)
            .select_related(
                'employee',
                'employee__department',
                'employee__designation',
                'employee__company',
                'employee__bank_detail',
            )
            .prefetch_related('employee__salaries')
            .order_by('employee__employee_code')
        )

        # 2. Apply Department / Search Filters
        dept_id = self.request.GET.get('department')
        emp_search = self.request.GET.get('q', '').strip()
        if dept_id:
            payslips = payslips.filter(employee__department_id=dept_id)
        if emp_search:
            payslips = payslips.filter(
                Q(employee__first_name__icontains=emp_search) |
                Q(employee__last_name__icontains=emp_search) |
                Q(employee__employee_code__icontains=emp_search)
            )

        # 3. Aggregated Totals
        totals = payslips.aggregate(
            total_net=Sum('net_pay'),
            total_earnings=Sum('total_earnings'),
            total_deductions=Sum('total_deductions'),
            total_penalties=Sum('penalty_deduction'),
            total_paid_days=Sum('paid_days'),
        )

        # 4. Pre-build AttendancePenalty lookup for grace half-day count
        #    (one query for the whole month instead of N queries in the template)
        from .models import AttendancePenalty
        import calendar as cal_mod
        month_days = cal_mod.monthrange(payroll_run.year, payroll_run.month)[1]
        from datetime import date as dt_date
        period_start = dt_date(payroll_run.year, payroll_run.month, 1)
        period_end = dt_date(payroll_run.year, payroll_run.month, month_days)

        # Count grace-triggered half-day penalties per employee
        grace_counts = {}
        try:
            penalties = (
                AttendancePenalty.objects
                .filter(
                    attendance_record__employee__company=payroll_run.company,
                    attendance_record__attendance_date__range=[period_start, period_end],
                    deduction_days=Decimal('0.5'),  # Grace-exhausted = 0.5 day deduction
                    status=AttendancePenalty.DeductionStatus.APPLIED,
                )
                .values('attendance_record__employee_id')
                .annotate(cnt=Count('id'))
            )
            grace_counts = {row['attendance_record__employee_id']: row['cnt'] for row in penalties}
        except Exception:
            grace_counts = {}

        # 5. Annotate each payslip with computed display fields
        from decimal import Decimal
        enriched = []
        for slip in payslips:
            emp = slip.employee
            sal = emp.salaries.all().filter(is_active=True).first() or emp.salaries.all().first()

            # Earned Base: what the employee actually worked (attendance component only)
            full = Decimal(str(slip.full_days or 0))
            half = Decimal(str(slip.half_days or 0))
            dw = Decimal(str(slip.daily_wage or 0))
            slip.earned_base = (dw * (full + half * Decimal('0.5'))).quantize(Decimal('0.01'))

            # Grace half-day count for audit column
            slip.grace_halfday_count = grace_counts.get(emp.pk, 0)

            # Cache salary snapshot for PF/TDS display (avoids extra DB hit in template)
            slip.sal_snap = sal
            enriched.append(slip)

        ctx['payslips'] = enriched
        ctx['total_net'] = round(totals['total_net'] or 0, 2)
        ctx['total_earnings'] = round(totals['total_earnings'] or 0, 2)
        ctx['total_deductions'] = round(totals['total_deductions'] or 0, 2)
        ctx['total_penalties'] = round(totals['total_penalties'] or 0, 2)
        ctx['total_paid_days'] = round(totals['total_paid_days'] or 0, 2)
        ctx['total_employees_count'] = len(enriched)

        # Filter Dropdown
        ctx['departments'] = m.Department.objects.filter(company=payroll_run.company).order_by('name')
        ctx['selected_dept'] = dept_id
        ctx['search_query'] = emp_search
        return ctx


class PayrollExportCSVView(HRRequiredMixin, View):
    """Exports the complete, filtered Payroll & Bank Disbursement CSV."""

    def get(self, request, pk):
        from decimal import Decimal
        payroll_run = get_object_or_404(m.PayrollRun, pk=pk)
        payslips = (
            payroll_run.payslips
            .select_related(
                'employee',
                'employee__department',
                'employee__designation',
                'employee__bank_detail',
            )
            .prefetch_related('employee__salaries')
            .order_by('employee__employee_code')
        )

        # Apply same filters as the register view
        dept_id = request.GET.get('department')
        emp_search = request.GET.get('q', '').strip()
        if dept_id:
            payslips = payslips.filter(employee__department_id=dept_id)
        if emp_search:
            payslips = payslips.filter(
                Q(employee__first_name__icontains=emp_search) |
                Q(employee__last_name__icontains=emp_search) |
                Q(employee__employee_code__icontains=emp_search)
            )

        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        filename = (
            f"Payroll_{payroll_run.company.name}_"
            f"{payroll_run.month}_{payroll_run.year}.csv"
        ).replace(' ', '_')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)

        # Header row — matches register column order exactly
        writer.writerow([
            # Identity
            'EMP ID', 'EMPLOYEE NAME', 'DEPARTMENT', 'DESIGNATION',
            # Attendance
            'FULL DAYS', 'HALF DAYS', 'HD AFTER GRACE', 'OFF / HOL',
            'PAID LEAVES', 'COMP OFF', 'PAID DAYS', 'UNPAID (LWP)',
            # Earnings
            'MONTHLY CTC', 'DAILY WAGE', 'EARNED BASE',
            'EXTRAS / INCENTIVES', 'GROSS EARNED',
            # Deductions
            'PF (EMPLOYEE)', 'TDS', 'LOAN / EMI',
            'PENALTY DEDUCTION', 'TOTAL DEDUCTIONS',
            # Payout
            'NET TAKE-HOME', 'PAYMENT STATUS',
            # Bank
            'BANK NAME', 'ACCOUNT NUMBER', 'IFSC CODE',
        ])

        for s in payslips:
            emp = s.employee
            bank = getattr(emp, 'bank_detail', None)
            sal = emp.salaries.all().filter(is_active=True).first() \
                  or emp.salaries.all().first()

            # Earned Base = daily_wage × (full_days + half_days × 0.5)
            full = Decimal(str(s.full_days or 0))
            half = Decimal(str(s.half_days or 0))
            dw = Decimal(str(s.daily_wage or 0))
            earned_base = (dw * (full + half * Decimal('0.5'))).quantize(Decimal('0.01'))

            # Monthly CTC from salary record
            monthly_ctc = Decimal('0.00')
            if sal and sal.ctc_annual:
                monthly_ctc = (sal.ctc_annual / Decimal('12.0')).quantize(Decimal('0.01'))

            pf = sal.pf_employee if sal else 0
            tds = sal.tds if sal else 0

            writer.writerow([
                emp.employee_code,
                emp.full_name,
                emp.department.name if emp.department else '',
                emp.designation.title if emp.designation else '',
                # Attendance
                round(float(s.full_days), 1),
                round(float(s.half_days), 1),
                getattr(s, 'grace_halfday_count', 0),
                round(float(s.off_days), 1),
                round(float(s.paid_leave_days), 1),
                round(float(s.comp_off_days), 1),
                round(float(s.paid_days), 1),
                round(float(s.absent_days), 1),
                # Earnings
                float(monthly_ctc),
                float(s.daily_wage),
                float(earned_base),
                float(s.extra_earning),
                float(s.total_earnings),
                # Deductions
                float(pf),
                float(tds),
                float(s.loan_deduction),
                float(s.penalty_deduction),
                float(s.total_deductions),
                # Payout
                float(s.net_pay),
                s.get_payment_status_display(),
                # Bank
                bank.bank_name if bank else '',
                bank.account_number if bank else '',
                bank.ifsc_code if bank else '',
            ])

        return response


# ---------------------------------------------------------------------------
# Payslips — HR sees all (filterable); employee sees only their own
# ---------------------------------------------------------------------------
class PaySlipListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.PaySlip
    template_name = 'hrms/payroll/payslip_list.html'
    context_object_name = 'payslips'
    active_group, active_item = 'payroll', 'my_payslips'
    paginate_by = 30

    def get_queryset(self):
        qs = m.PaySlip.objects.select_related('employee', 'payroll_run').order_by(
            '-payroll_run__year', '-payroll_run__month')
        if is_hr_or_above(self.request.user):
            employee_id = self.request.GET.get('employee')
            if employee_id:
                qs = qs.filter(employee_id=employee_id)
            return qs
        employee = get_employee_profile(self.request.user)
        if employee is None:
            return m.PaySlip.objects.none()
        return qs.filter(employee=employee)

    def get_queryset(self):
        qs = m.PaySlip.objects.select_related('employee', 'payroll_run').order_by(
            '-payroll_run__year', '-payroll_run__month')

        # Base Filtering based on Role
        if not is_hr_or_above(self.request.user):
            employee = get_employee_profile(self.request.user)
            if employee is None:
                return m.PaySlip.objects.none()
            qs = qs.filter(employee=employee)

        # Apply Search and Date Filters
        search_query = self.request.GET.get('search')
        employee_id = self.request.GET.get('employee')
        month = self.request.GET.get('month')
        year = self.request.GET.get('year')

        if search_query:
            qs = qs.filter(
                Q(employee__first_name__icontains=search_query) |
                Q(employee__last_name__icontains=search_query) |
                Q(employee__employee_code__icontains=search_query)
            )

        if employee_id:
            qs = qs.filter(employee_id=employee_id)

        if month:
            qs = qs.filter(payroll_run__month=month)

        if year:
            qs = qs.filter(payroll_run__year=year)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        if ctx['is_hr']:
            ctx['employees'] = m.Employee.objects.order_by('employee_code')
        return ctx


class PaySlipDetailView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    model = m.PaySlip
    template_name = 'hrms/payroll/payslip_detail.html'
    context_object_name = 'payslip'
    active_group, active_item = 'payroll', 'my_payslips'

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not is_hr_or_above(self.request.user):
            employee = get_employee_profile(self.request.user)
            if employee is None or obj.employee_id != employee.id:
                raise PermissionDenied("You can only view your own payslips.")
        return obj


class PaySlipPDFView(LoginRequiredMixin, View):
    """Generates a simple, professional payslip PDF with ReportLab."""

    def get(self, request, pk):
        payslip = get_object_or_404(m.PaySlip.objects.select_related('employee', 'payroll_run', 'employee__company'),
                                    pk=pk)
        if not is_hr_or_above(request.user):
            employee = get_employee_profile(request.user)
            if employee is None or payslip.employee_id != employee.id:
                raise PermissionDenied("You can only view your own payslips.")

        # Module E: Silent download audit logging
        try:
            ip_addr = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
            m.PayslipDownloadLog.objects.create(
                payslip=payslip,
                downloaded_by=request.user,
                ip_address=ip_addr[:45] if ip_addr else ''
            )
        except Exception:
            pass

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            return HttpResponse(
                'PDF export requires ReportLab. Install it with: pip install reportlab --break-system-packages',
                status=501, content_type='text/plain',
            )

        from io import BytesIO
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
        styles = getSampleStyleSheet()
        elements = []

        run = payslip.payroll_run
        employee = payslip.employee
        period = f'{_calendar.month_name[run.month]} {run.year}'

        elements.append(Paragraph(f'<b>{employee.company.name}</b>', styles['Title']))
        elements.append(Paragraph(f'Payslip — {period}', styles['Heading3']))
        elements.append(Spacer(1, 8))

        info_table = Table([
            ['Employee', employee.full_name, 'Employee Code', employee.employee_code],
            ['Designation', str(employee.designation or '—'), 'Department', str(employee.department or '—')],
            ['Paid Days', str(payslip.paid_days), 'Absent Days', str(payslip.absent_days)],
        ], colWidths=[80, 150, 90, 150])
        info_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 14))

        breakdown = Table([
            ['Earnings', 'Amount (₹)', 'Deductions', 'Amount (₹)'],
            ['Extra / Incentive', f'{payslip.extra_earning}', 'Loan/Advance', f'{payslip.loan_deduction}'],
            ['', '', 'Absence Penalty', f'{payslip.penalty_deduction}'],
            ['Total Earnings', f'{payslip.total_earnings}', 'Total Deductions', f'{payslip.total_deductions}'],
        ], colWidths=[110, 90, 110, 90])
        breakdown.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#101b2d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('LINEABOVE', (0, -1), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e4e8ef')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(breakdown)
        elements.append(Spacer(1, 14))

        net_table = Table([['Net Pay', f'Rs. {payslip.net_pay}']], colWidths=[300, 100])
        net_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#2fb8a3')),
        ]))
        elements.append(net_table)

        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        response[
            'Content-Disposition'] = f'attachment; filename="payslip_{employee.employee_code}_{run.year}_{run.month}.pdf"'
        return response


# ===========================================================================
# HIRING / RECRUITMENT PIPELINE
# ===========================================================================
from . import hiring_logic as hire


class JobPostingListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.JobPosting
    template_name = 'hrms/hiring/jobposting_list.html'
    context_object_name = 'postings'
    active_group, active_item = 'hiring', 'jobposting'

    def get_queryset(self):
        return m.JobPosting.objects.select_related('company', 'department').order_by('-posted_on')


class JobPostingCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.JobPosting
    form_class = f.JobPostingForm
    template_name = 'hrms/hiring/jobposting_form.html'
    success_url = reverse_lazy('hrms:jobposting_list')
    active_group, active_item = 'hiring', 'jobposting'

    def form_valid(self, form):
        messages.success(self.request, 'Job posting created.')
        return super().form_valid(form)


class JobPostingUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.JobPosting
    form_class = f.JobPostingForm
    template_name = 'hrms/hiring/jobposting_form.html'
    success_url = reverse_lazy('hrms:jobposting_list')
    active_group, active_item = 'hiring', 'jobposting'

    def form_valid(self, form):
        messages.success(self.request, 'Job posting updated.')
        return super().form_valid(form)


class JobPostingDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.JobPosting
    template_name = 'hrms/hiring/jobposting_confirm_delete.html'
    success_url = reverse_lazy('hrms:jobposting_list')
    active_group, active_item = 'hiring', 'jobposting'

    def form_valid(self, form):
        messages.success(self.request, 'Job posting deleted.')
        return super().form_valid(form)


class CandidateListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.Candidate
    template_name = 'hrms/hiring/candidate_list.html'
    context_object_name = 'candidates'
    active_group, active_item = 'hiring', 'candidate'
    paginate_by = 30

    def get_queryset(self):
        qs = m.Candidate.objects.prefetch_related(
            Prefetch(
                'applications',
                queryset=m.Application.objects.select_related('job_posting', 'current_stage').order_by('-applied_on')
            )
        ).order_by('-created_at')

        # Keyword search
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(email__icontains=q) |
                Q(phone__icontains=q) |
                Q(current_company__icontains=q) |
                Q(applications__job_posting__title__icontains=q)
            )

        # Status filter
        status = self.request.GET.get('status', '').strip()
        if status:
            qs = qs.filter(applications__status=status)

        # Experience filter
        exp = self.request.GET.get('exp', '').strip()
        if exp == '0-1':
            qs = qs.filter(experience_years__lte=1)
        elif exp == '1-3':
            qs = qs.filter(experience_years__gt=1, experience_years__lte=3)
        elif exp == '3-5':
            qs = qs.filter(experience_years__gt=3, experience_years__lte=5)
        elif exp == '5-10':
            qs = qs.filter(experience_years__gt=5, experience_years__lte=10)
        elif exp == '10+':
            qs = qs.filter(experience_years__gt=10)

        # Current Company filter
        company = self.request.GET.get('company', '').strip()
        if company:
            qs = qs.filter(current_company__icontains=company)

        # Location filter (city or state)
        location = self.request.GET.get('location', '').strip()
        if location:
            qs = qs.filter(Q(city__icontains=location) | Q(state__icontains=location))

        # Job posting filter
        job_id = self.request.GET.get('job', '').strip()
        if job_id:
            qs = qs.filter(applications__job_posting_id=job_id)

        # Date range filter (from_date / to_date)
        from_date = self.request.GET.get('from_date', '').strip()
        to_date = self.request.GET.get('to_date', '').strip()
        if from_date:
            try:
                qs = qs.filter(created_at__date__gte=from_date)
            except Exception:
                pass
        if to_date:
            try:
                qs = qs.filter(created_at__date__lte=to_date)
            except Exception:
                pass

        return qs.distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '').strip()
        ctx['status'] = self.request.GET.get('status', '').strip()
        ctx['exp'] = self.request.GET.get('exp', '').strip()
        ctx['company'] = self.request.GET.get('company', '').strip()
        ctx['location'] = self.request.GET.get('location', '').strip()
        ctx['job_id'] = self.request.GET.get('job', '').strip()
        ctx['from_date'] = self.request.GET.get('from_date', '').strip()
        ctx['to_date'] = self.request.GET.get('to_date', '').strip()

        # Count active filters
        active_filters = 0
        for key in ['q', 'status', 'exp', 'company', 'location', 'job_id', 'from_date', 'to_date']:
            if ctx.get(key):
                active_filters += 1
        ctx['active_filters_count'] = active_filters

        # Choices & dropdown data
        ctx['status_choices'] = m.Application.Status.choices
        ctx['jobs_list'] = m.JobPosting.objects.all().order_by('title')
        return ctx


class CandidateCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Candidate
    form_class = f.UnifiedCandidateForm
    template_name = 'hrms/hiring/candidate_form.html'
    success_url = reverse_lazy('hrms:candidate_list')
    active_group, active_item = 'hiring', 'candidate'

    def form_valid(self, form):
        with transaction.atomic():
            self.object = form.save()
            m.Application.objects.create(
                candidate=self.object,
                job_posting=form.cleaned_data['job_posting'],
                status=form.cleaned_data.get('status', 'applied'),
                source=form.cleaned_data.get('source', ''),
                cover_letter=form.cleaned_data.get('cover_letter', '')
            )
        messages.success(self.request,
                         f"Candidate {self.object.first_name} {self.object.last_name} added successfully.")
        return redirect(self.success_url)


class CandidateUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Candidate
    form_class = f.CandidateForm
    template_name = 'hrms/hiring/candidate_form.html'
    active_group, active_item = 'hiring', 'candidate'

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse('hrms:candidate_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request,
                         f"Candidate {self.object.first_name} {self.object.last_name} updated successfully.")
        return super().form_valid(form)


class CandidateDetailView(HRRequiredMixin, SidebarContextMixin, DetailView):
    model = m.Candidate
    template_name = 'hrms/hiring/candidate_detail.html'
    context_object_name = 'candidate'
    active_group, active_item = 'hiring', 'candidate'

    def get_queryset(self):
        return m.Candidate.objects.prefetch_related(
            Prefetch(
                'applications',
                queryset=m.Application.objects.select_related('job_posting', 'current_stage').prefetch_related(
                    'audit_logs').order_by('-applied_on')
            )
        )


class ApplicationListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.Application
    template_name = 'hrms/hiring/application_list.html'
    context_object_name = 'applications'
    active_group, active_item = 'hiring', 'application'
    paginate_by = 30

    def get_queryset(self):
        qs = m.Application.objects.select_related('candidate', 'job_posting', 'current_stage').order_by('-applied_on')

        # 1. Filter by Job ID
        job_id = self.request.GET.get('job', '').strip()
        if job_id:
            qs = qs.filter(job_posting_id=job_id)

        # 2. Filter by Status
        status = self.request.GET.get('status', '').strip()
        if status:
            qs = qs.filter(status=status)

        # 3. Filter by Stage / Round
        stage_id = self.request.GET.get('stage', '').strip()
        if stage_id:
            qs = qs.filter(current_stage_id=stage_id)

        # 4. Search by candidate name or email
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(candidate__first_name__icontains=q) |
                Q(candidate__last_name__icontains=q) |
                Q(candidate__email__icontains=q)
            )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job_id = (self.request.GET.get('job') or '').strip()
        stage_id = (self.request.GET.get('stage') or '').strip()
        status_filter = (self.request.GET.get('status') or '').strip()
        q = (self.request.GET.get('q') or '').strip()

        ctx['jobs'] = m.JobPosting.objects.all().order_by('-posted_on')
        ctx['statuses'] = m.Application.Status.choices
        ctx['current_job_id'] = job_id
        ctx['current_stage_id'] = stage_id
        ctx['current_status'] = status_filter
        ctx['search_query'] = q

        # Pipeline stages mapping and round calculation
        pipelines = m.JobPipeline.objects.select_related('stage').order_by('job_id', 'order')
        job_pipelines_map = {}
        stage_round_display_map = {}
        for p in pipelines:
            job_pipelines_map.setdefault(p.job_id, []).append(p)

        for jid, plist in job_pipelines_map.items():
            for idx, p in enumerate(plist, start=1):
                stage_round_display_map[(jid, p.stage_id)] = f"Round {idx}: {p.stage.name}"

        # Assign stage_round_display to applications in the current page
        applications = ctx.get('applications') or []
        for app in applications:
            if app.current_stage_id:
                app.stage_round_display = stage_round_display_map.get(
                    (app.job_posting_id, app.current_stage_id),
                    app.current_stage.name
                )
            else:
                app.stage_round_display = None

        # Available stages for the filter dropdown
        available_stages = []
        if job_id and job_id.isdigit():
            target_job_id = int(job_id)
            if target_job_id in job_pipelines_map:
                for idx, p in enumerate(job_pipelines_map[target_job_id], start=1):
                    available_stages.append({
                        'id': p.stage_id,
                        'name': p.stage.name,
                        'display_name': f"Round {idx}: {p.stage.name}"
                    })

        if not available_stages:
            all_stages = m.RecruitmentStage.objects.all().order_by('name')
            for s in all_stages:
                available_stages.append({
                    'id': s.id,
                    'name': s.name,
                    'display_name': s.name
                })
        ctx['available_stages'] = available_stages

        # JSON map of job_id -> stages for dynamic client-side dropdown update
        job_stages_json = {}
        for jid, plist in job_pipelines_map.items():
            job_stages_json[str(jid)] = [
                {'id': p.stage_id, 'display_name': f"Round {idx}: {p.stage.name}"}
                for idx, p in enumerate(plist, start=1)
            ]
        all_stages_list = [
            {'id': s.id, 'display_name': s.name}
            for s in m.RecruitmentStage.objects.all().order_by('name')
        ]
        job_stages_json['all'] = all_stages_list
        ctx['job_stages_json'] = json.dumps(job_stages_json)

        # Status count aggregation for the tab pills (reflecting active job, stage, and search query)
        base_qs = m.Application.objects.all()
        if job_id:
            base_qs = base_qs.filter(job_posting_id=job_id)
        if stage_id:
            base_qs = base_qs.filter(current_stage_id=stage_id)
        if q:
            base_qs = base_qs.filter(
                Q(candidate__first_name__icontains=q) |
                Q(candidate__last_name__icontains=q) |
                Q(candidate__email__icontains=q)
            )

        status_counts = base_qs.aggregate(
            total=Count('id'),
            applied=Count('id', filter=Q(status='applied')),
            shortlisted=Count('id', filter=Q(status='shortlisted')),
            interviewing=Count('id', filter=Q(status='interviewing')),
            offered=Count('id', filter=Q(status='offered')),
            hired=Count('id', filter=Q(status='hired')),
            rejected=Count('id', filter=Q(status='rejected')),
        )
        ctx['status_counts'] = status_counts

        return ctx


class ApplicationCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Application
    form_class = f.ApplicationForm
    template_name = 'hrms/hiring/application_form.html'
    active_group, active_item = 'hiring', 'application'

    def form_valid(self, form):
        messages.success(self.request, 'Application recorded.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:application_detail', kwargs={'pk': self.object.pk})


class ApplicationDetailView(HRRequiredMixin, SidebarContextMixin, DetailView):
    """The pipeline hub: shows interviews, offer letter (if any), and the
    quick-action buttons that move the candidate through the pipeline —
    including 'Generate Offer Letter', the flow the prompt asked for."""
    model = m.Application
    template_name = 'hrms/hiring/application_detail.html'
    context_object_name = 'application'
    active_group, active_item = 'hiring', 'application'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. Use 'prefetch_related' because it's a Many-to-Many field
        # 2. Use 'interviewer' (singular), NOT 'interviewers'
        ctx['interviews'] = self.object.interviews.prefetch_related('interviewer').order_by('scheduled_on')

        ctx['offer'] = getattr(self.object, 'offer_letter', None)
        ctx['status_choices'] = m.Application.Status.choices
        ctx['audit_logs'] = self.object.audit_logs.select_related('performed_by')[:20]
        return ctx


from django.contrib import messages


class ApplicationStatusView(HRRequiredMixin, View):
    def post(self, request, pk):
        application = get_object_or_404(m.Application, pk=pk)
        new_status = request.POST.get('status')
        reason = request.POST.get('reason', '')  # From the Reject modal

        # Check if the submitted status is valid
        valid_statuses = [choice[0] for choice in m.Application.Status.choices]

        if application.is_locked and not request.user.is_superuser:
            messages.error(request, "This application is locked and can only be modified by Super Admin.")
            return redirect(request.META.get('HTTP_REFERER') or reverse('hrms:application_list'))

        if new_status in valid_statuses:
            # Update the status
            application.status = new_status
            application.save()

            messages.success(request, f"Status updated successfully to {application.get_status_display()}.")
        else:
            messages.error(request, "Unknown action or invalid status.")

        return redirect(request.META.get('HTTP_REFERER') or reverse('hrms:application_list'))


class UnlockApplicationView(SuperAdminRequiredMixin, View):
    """Super Admin only — reopens a locked (Hired/Rejected) application."""

    def post(self, request, pk):
        application = get_object_or_404(m.Application, pk=pk)
        try:
            hire.unlock_application(application, request.user)
            messages.success(request, 'Application unlocked.')
        except hire.HiringError as e:
            messages.error(request, str(e))
        return redirect('hrms:application_detail', pk=pk)


class InterviewCreateView(HRRequiredMixin, SidebarContextMixin, View):
    """Uses hiring_logic.schedule_interview() so locking, the audit trail, and
    the dual candidate/interviewer email notification all happen together."""
    active_group, active_item = 'hiring', 'interview'
    template_name = 'hrms/hiring/interview_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.application = get_object_or_404(m.Application, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = f.InterviewForm()
        return render(request, self.template_name, {
            'form': form, 'application': self.application,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        form = f.InterviewForm(request.POST, application=self.application)
        if form.is_valid():
            try:
                interview, email_results = hire.schedule_interview(
                    self.application, request.user,
                    interview_round=form.cleaned_data['interview_round'],
                    scheduled_on=form.cleaned_data['scheduled_on'],
                    interviewer=form.cleaned_data.get('interviewer'),
                    mode=form.cleaned_data['mode'],
                )
                messages.success(request, 'Interview scheduled.')
                _report_email_results(request, email_results)
                return redirect('hrms:application_detail', pk=self.application.pk)
            except hire.HiringError as e:
                form.add_error(None, str(e))
        return render(request, self.template_name, {
            'form': form, 'application': self.application,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


class InterviewUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    """Manual corrections (feedback, status) — doesn't re-fire emails or move
    the pipeline; use the action buttons for that."""
    model = m.Interview
    form_class = f.InterviewForm
    template_name = 'hrms/hiring/interview_form.html'
    active_group, active_item = 'hiring', 'interview'

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        try:
            hire.ensure_unlocked(obj.application, self.request.user)
        except hire.HiringError as e:
            messages.error(self.request, str(e))
            raise PermissionDenied(str(e))
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['application'] = self.object.application
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Interview updated.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:application_detail', kwargs={'pk': self.object.application.pk})


class InterviewListView(HRRequiredMixin, SidebarContextMixin, ListView):
    """All upcoming/past interviews across the pipeline (HR view)."""
    model = m.Interview
    template_name = 'hrms/hiring/interview_list.html'
    context_object_name = 'interviews'
    active_group, active_item = 'hiring', 'interview'

    def get_queryset(self):
        return m.Interview.objects.select_related('application__candidate', 'application__job_posting',
                                                  'interviewer').order_by('-scheduled_on')

    def get_queryset(self):
        return m.Interview.objects.select_related(
            'application__candidate',
            'application__job_posting'
        ).prefetch_related(
            'interviewer'  # Use prefetch_related for Many-to-Many fields
        ).order_by('-scheduled_on')


class MyInterviewsView(LoginRequiredMixin, SidebarContextMixin, ListView):
    """Any logged-in employee assigned as an interviewer (not just HR) can see
    and give feedback on the interviews they've been assigned — a department
    manager who isn't 'HR' still needs to see their own interview schedule."""
    model = m.Interview
    template_name = 'hrms/hiring/my_interviews.html'
    context_object_name = 'interviews'
    active_group, active_item = 'hiring', 'my_interviews'

    def get_queryset(self):
        employee = get_employee_profile(self.request.user)
        if employee is None:
            return m.Interview.objects.none()
        return m.Interview.objects.filter(interviewer=employee).select_related(
            'application__candidate', 'application__job_posting').order_by('-scheduled_on')


class InterviewFeedbackView(LoginRequiredMixin, View):
    """The assigned interviewer submits feedback + marks the interview Completed."""

    def post(self, request, pk):
        interview = get_object_or_404(m.Interview, pk=pk)
        employee = get_employee_profile(request.user)
        if not is_hr_or_above(request.user) and (employee is None or interview.interviewer_id != employee.id):
            raise PermissionDenied('You can only submit feedback for interviews assigned to you.')
        interview.feedback = request.POST.get('feedback', interview.feedback)
        interview.status = m.Interview.Status.COMPLETED
        interview.save(update_fields=['feedback', 'status', 'updated_at'])
        messages.success(request, 'Feedback submitted.')
        return redirect('hrms:my_interviews')


def _report_email_results(request, results):
    """Surfaces per-recipient email send success/failure as Django messages."""
    for role, email, ok, err in results:
        if ok:
            messages.info(request, f'Notification email sent to {role} ({email}).')
        else:
            messages.warning(request, f'Could not email {role} ({email}): {err}')


class OfferLetterCreateView(HRRequiredMixin, SidebarContextMixin, View):
    """THE flow the prompt asked for: move a Candidate/Application to OfferLetter status."""
    active_group, active_item = 'hiring', 'application'
    template_name = 'hrms/hiring/offer_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.application = get_object_or_404(m.Application, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = f.OfferLetterForm()
        return render(request, self.template_name, {
            'form': form, 'application': self.application,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        form = f.OfferLetterForm(request.POST)
        if form.is_valid():
            try:
                hire.move_to_offer(
                    self.application, request.user,
                    offer_date=form.cleaned_data['offer_date'],
                    ctc_offered=form.cleaned_data['ctc_offered'],
                    joining_date=form.cleaned_data.get('joining_date'),
                    expiry_date=form.cleaned_data.get('expiry_date'),
                )
                messages.success(request, f'Offer letter created for {self.application.candidate}.')
                return redirect('hrms:application_detail', pk=self.application.pk)
            except hire.HiringError as e:
                form.add_error(None, str(e))
        return render(request, self.template_name, {
            'form': form, 'application': self.application,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


class OfferLetterActionView(HRRequiredMixin, View):
    """Send / Accept / Decline / Expire buttons on the application detail page."""

    def post(self, request, pk, action):
        offer = get_object_or_404(m.OfferLetter, pk=pk)
        try:
            if action == 'send':
                offer, email_results = hire.send_offer(offer, request.user)
                messages.success(request, 'Offer marked as sent.')
                _report_email_results(request, email_results)
            elif action == 'accept':
                hire.accept_offer(offer, request.user)
                messages.success(request, 'Offer accepted — application marked Hired and locked.')
            elif action == 'decline':
                hire.decline_offer(offer, request.user)
                messages.warning(request, 'Offer declined — application marked Rejected and locked.')
            elif action == 'expire':
                hire.expire_offer(offer, request.user)
                messages.info(request, 'Offer marked as expired.')
            else:
                messages.error(request, 'Unknown action.')
        except hire.HiringError as e:
            messages.error(request, str(e))
        return redirect('hrms:application_detail', pk=offer.application.pk)


class ConvertToEmployeeView(HRRequiredMixin, SidebarContextMixin, View):
    """Once an offer is ACCEPTED, turn the candidate into a real Employee record."""
    active_group, active_item = 'hiring', 'application'
    template_name = 'hrms/hiring/convert_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.offer = get_object_or_404(m.OfferLetter, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = f.ConvertToEmployeeForm()
        return render(request, self.template_name, {
            'form': form, 'offer': self.offer,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        form = f.ConvertToEmployeeForm(request.POST)
        if form.is_valid():
            try:
                employee = hire.convert_to_employee(
                    self.offer, request.user,
                    company=form.cleaned_data['company'],
                    department=form.cleaned_data.get('department'),
                    designation=form.cleaned_data.get('designation'),
                    employee_code=form.cleaned_data['employee_code'],
                )
                messages.success(request, f'{employee.full_name} onboarded as {employee.employee_code}.')
                return redirect('hrms:employee_detail', pk=employee.pk)
            except hire.HiringError as e:
                form.add_error(None, str(e))
        return render(request, self.template_name, {
            'form': form, 'offer': self.offer,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


# --- NEW: RecruitmentStage CRUD --------------------------------------------
class RecruitmentStageListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.RecruitmentStage
    template_name = 'hrms/hiring/recruitmentstage_list.html'
    context_object_name = 'stages'
    active_group, active_item = 'hiring', 'recruitmentstage'


class RecruitmentStageCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.RecruitmentStage
    form_class = f.RecruitmentStageForm
    template_name = 'hrms/hiring/recruitmentstage_form.html'
    success_url = reverse_lazy('hrms:recruitmentstage_list')
    active_group, active_item = 'hiring', 'recruitmentstage'

    def form_valid(self, form):
        messages.success(self.request, 'Recruitment stage created.')
        return super().form_valid(form)


class RecruitmentStageUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.RecruitmentStage
    form_class = f.RecruitmentStageForm
    template_name = 'hrms/hiring/recruitmentstage_form.html'
    success_url = reverse_lazy('hrms:recruitmentstage_list')
    active_group, active_item = 'hiring', 'recruitmentstage'

    def form_valid(self, form):
        messages.success(self.request, 'Recruitment stage updated.')
        return super().form_valid(form)


class RecruitmentStageDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.RecruitmentStage
    template_name = 'hrms/hiring/recruitmentstage_confirm_delete.html'
    success_url = reverse_lazy('hrms:recruitmentstage_list')
    active_group, active_item = 'hiring', 'recruitmentstage'


# --- NEW: JobPipeline — manage a job's stage sequence on one screen -------
class JobPipelineManageView(HRRequiredMixin, SidebarContextMixin, View):
    """One page per JobPosting: an inline formset to add/reorder/remove the
    RecruitmentStages that make up that job's own hiring flow."""
    template_name = 'hrms/hiring/jobpipeline_manage.html'
    active_group, active_item = 'hiring', 'jobposting'

    def dispatch(self, request, *args, **kwargs):
        self.job = get_object_or_404(m.JobPosting, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        formset = f.JobPipelineFormSet(instance=self.job)
        pipeline_stages = m.JobPipeline.objects.filter(job=self.job).select_related('stage').order_by('order')
        used_stage_ids = set(pipeline_stages.values_list('stage_id', flat=True))
        all_stages = m.RecruitmentStage.objects.all().order_by('name')
        return render(request, self.template_name, {
            'job': self.job, 'formset': formset,
            'pipeline_stages': pipeline_stages,
            'used_stage_ids': used_stage_ids,
            'all_stages': all_stages,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        formset = f.JobPipelineFormSet(request.POST, instance=self.job)
        if formset.is_valid():
            formset.save()
            messages.success(request, 'Pipeline updated.')
            return redirect('hrms:jobpipeline_manage', pk=self.job.pk)
        return render(request, self.template_name, {
            'job': self.job, 'formset': formset,
            'active_group': self.active_group, 'active_item': self.active_item,
        })


# --- NEW: OfferTemplate CRUD ------------------------------------------------
class OfferTemplateListView(HRRequiredMixin, SidebarContextMixin, ListView):
    model = m.OfferTemplate
    template_name = 'hrms/hiring/offertemplate_list.html'
    context_object_name = 'templates'
    active_group, active_item = 'hiring', 'offertemplate'


class OfferTemplateCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.OfferTemplate
    form_class = f.OfferTemplateForm
    template_name = 'hrms/hiring/offertemplate_form.html'
    success_url = reverse_lazy('hrms:offertemplate_list')
    active_group, active_item = 'hiring', 'offertemplate'

    def form_valid(self, form):
        messages.success(self.request, 'Offer template created.')
        return super().form_valid(form)


class OfferTemplateUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.OfferTemplate
    form_class = f.OfferTemplateForm
    template_name = 'hrms/hiring/offertemplate_form.html'
    success_url = reverse_lazy('hrms:offertemplate_list')
    active_group, active_item = 'hiring', 'offertemplate'

    def form_valid(self, form):
        messages.success(self.request, 'Offer template updated.')
        return super().form_valid(form)


class OfferTemplateDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.OfferTemplate
    template_name = 'hrms/hiring/offertemplate_confirm_delete.html'
    success_url = reverse_lazy('hrms:offertemplate_list')
    active_group, active_item = 'hiring', 'offertemplate'


class OfferTemplatePreviewView(HRRequiredMixin, View):
    """AJAX-ish endpoint: render the chosen template against an application
    so the OfferLetter form can pre-fill `content` before the user tweaks it."""

    def get(self, request, template_pk, application_pk):
        template = get_object_or_404(m.OfferTemplate, pk=template_pk)
        application = get_object_or_404(m.Application, pk=application_pk)
        ctc = request.GET.get('ctc_offered') or 0
        rendered = template.render(application, ctc_offered=ctc, offer_date=None)
        return render(request, 'hrms/hiring/_offer_preview_fragment.html', {'rendered': rendered})


from django.db.models import Prefetch


# from .hiring_logic import HiringService


# ---------------------------------------------------------------------------
# ADVANCED RECRUITMENT PIPELINE
# ---------------------------------------------------------------------------

class JobKanbanView(HRRequiredMixin, SidebarContextMixin, DetailView):
    """
    Advanced Kanban Board for a specific Job.
    Groups applications by their current stage in the pipeline.
    """
    model = m.JobPosting
    template_name = 'hrms/hiring/job_kanban.html'
    context_object_name = 'job'
    active_group, active_item = 'hiring', 'jobposting'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job = self.object

        # Get the ordered pipeline stages for this specific job
        pipeline_stages = job.pipeline_stages.select_related('stage').order_by('order')

        # Check if single application or candidate filter requested
        app_id = self.request.GET.get('application') or self.request.GET.get('app')
        candidate_id = self.request.GET.get('candidate')
        filtered_app = None

        all_apps = job.applications.select_related('candidate', 'current_stage').all()

        if app_id:
            try:
                candidate_app = all_apps.filter(pk=app_id).first()
                if candidate_app:
                    filtered_app = candidate_app
                    all_apps = all_apps.filter(pk=app_id)
            except Exception:
                pass
        elif candidate_id:
            try:
                candidate_app = all_apps.filter(candidate_id=candidate_id).first()
                if candidate_app:
                    filtered_app = candidate_app
                    all_apps = all_apps.filter(candidate_id=candidate_id)
            except Exception:
                pass

        # Prefetch applications for each stage to avoid N+1 queries
        apps_by_stage = collections.defaultdict(list)
        for app in all_apps:
            apps_by_stage[app.current_stage_id].append(app)

        ctx['pipeline'] = [
            {'stage': ps.stage, 'apps': apps_by_stage[ps.stage_id]}
            for ps in pipeline_stages
        ]
        ctx['filtered_app'] = filtered_app
        return ctx


class ApplicationDetailView(HRRequiredMixin, SidebarContextMixin, DetailView):
    """
    The 360-Degree Candidate View.
    Aggregates interviews, scores, notes, and timeline.
    """
    model = m.Application
    template_name = 'hrms/hiring/application_detail.html'
    context_object_name = 'application'
    active_group, active_item = 'hiring', 'application'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        app = self.object

        # Dynamic Pipeline Visualization
        ctx['stages'] = app.job_posting.pipeline_stages.select_related('stage').order_by('order')

        # Detailed Interview Scores
        ctx['interviews'] = app.interviews.prefetch_related(
            'interviewer',
            'individual_feedbacks__interviewer'
        ).all()

        # Collaboration & Notes
        ctx['notes'] = app.notes.select_related('author').all()

        # History
        ctx['audit_logs'] = app.audit_logs.select_related('performed_by').all()

        return ctx


class SubmitInterviewFeedbackView(LoginRequiredMixin, SidebarContextMixin, CreateView):
    """
    Class-Based View for interviewers to submit private scorecard ratings.
    Utilizes the evaluation_criteria defined in the RecruitmentStage.
    """
    model = m.InterviewFeedback
    fields = ['recommendation', 'feedback_text', 'scorecard_filled']
    template_name = 'hrms/hiring/feedback_form.html'
    active_group, active_item = 'hiring', 'my_interviews'

    def form_valid(self, form):
        interview = get_object_or_404(m.Interview, pk=self.kwargs['interview_pk'])
        employee = get_employee_profile(self.request.user)

        form.instance.interview = interview
        form.instance.interviewer = employee

        messages.success(self.request, "Your feedback has been recorded.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('hrms:my_interviews')


# class FinalizeHiringActionView(HRRequiredMixin, View):
#     """
#     The 'Trigger' view to execute Candidate-to-Employee conversion.
#     """
#
#     def post(self, request, pk):
#         application = get_object_or_404(m.Application, pk=pk)
#
#         if application.is_locked:
#             messages.error(request, "This application is already locked.")
#             return redirect('hrms:application_detail', pk=pk)
#
#         try:
#             employee = HiringService.convert_to_employee(application, request.user)
#             messages.success(request, f"Success! {employee.full_name} is now an active employee.")
#             return redirect('hrms:employee_detail', pk=employee.pk)
#         except Exception as e:
#             messages.error(request, f"Conversion failed: {str(e)}")
#             return redirect('hrms:application_detail', pk=pk)

# ===========================================================================
# APPLICATION NOTES (HTMX)
# ===========================================================================
class ApplicationNoteCreateView(LoginRequiredMixin, View):
    """HTMX-aware endpoint: POST a note on an application.
    Returns a rendered HTML fragment when called via HTMX,
    or redirects to application_detail for full-page fallback.
    """

    def post(self, request, pk):
        application = get_object_or_404(m.Application, pk=pk)
        message = request.POST.get('message', '').strip()
        is_private = request.POST.get('is_private') == 'true'
        if message:
            note = m.ApplicationNote.objects.create(
                application=application,
                author=request.user,
                message=message,
                is_private=is_private,
            )
            if request.headers.get('HX-Request'):
                return render(request, 'hrms/hiring/_note_fragment.html', {'note': note})
        return redirect('hrms:application_detail', pk=pk)


# ===========================================================================
# ASSET MANAGEMENT
# ===========================================================================
import json
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from . import models as m
from . import forms as f
from django.contrib.auth.mixins import LoginRequiredMixin
from .permissions import HRRequiredMixin, is_hr_or_above, get_employee_profile


# ---------------------------------------------------------------------------
# ASSET COMMAND CENTER & INVENTORY
# ---------------------------------------------------------------------------

class AssetListView(HRRequiredMixin, SidebarContextMixin, ListView):
    """Command Center listing all assets grouped cleanly by Category."""
    model = m.Asset
    template_name = 'hrms/asset/asset_list.html'
    context_object_name = 'assets'
    active_group, active_item = 'asset', 'asset'

    def get_queryset(self):
        # Optimization: select related company, category, and employee to prevent N+1 queries
        qs = m.Asset.objects.select_related('company', 'category', 'employee')
        active_company_id = self.request.session.get('active_company_id')
        if active_company_id and active_company_id != 'all':
            qs = qs.filter(company_id=active_company_id)
        return qs.order_by('category__name', '-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base_qs = self.get_queryset()

        # 1. Top Inventory Stat Cards
        ctx['total_count'] = base_qs.count()
        ctx['available_count'] = base_qs.filter(status=m.Asset.Status.AVAILABLE).count()
        ctx['assigned_count'] = base_qs.filter(status=m.Asset.Status.ASSIGNED).count()
        ctx['repair_count'] = base_qs.filter(status=m.Asset.Status.UNDER_REPAIR).count()

        # 2. Category-wise Accordion Grouping
        categories = m.AssetCategory.objects.all().order_by('name')
        structured_data = []
        for cat in categories:
            cat_assets = base_qs.filter(category=cat)
            if cat_assets.exists():
                structured_data.append({
                    'id': cat.id,
                    'name': cat.name,
                    'assets': cat_assets,
                    'total': cat_assets.count(),
                    'available': cat_assets.filter(status=m.Asset.Status.AVAILABLE).count(),
                    'assigned': cat_assets.filter(status=m.Asset.Status.ASSIGNED).count(),
                })

        ctx['structured_assets'] = structured_data
        # 3. Dynamic Category configuration map passed to modal
        ctx['category_configs_json'] = json.dumps({
            str(c.id): c.required_fields for c in categories
        })
        return ctx


class AssetListView(CompanyFilterMixin, LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.Asset
    template_name = 'hrms/asset/asset_list.html'
    context_object_name = 'assets'
    active_group, active_item = 'asset', 'asset'
    paginate_by = 30

    def get_queryset(self):
        # Optimization: prefetch relations
        qs = m.Asset.objects.select_related('employee', 'company', 'category')

        # 1. Scope query by active company or employee profile
        if not is_hr_or_above(self.request.user):
            employee = get_employee_profile(self.request.user)
            if employee is None:
                return m.Asset.objects.none()
            qs = qs.filter(employee=employee)

        return qs.order_by('category__name', '-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        all_assets_qs = self.get_queryset()

        # 1. Stat Cards (Always un-filtered counts across full inventory)
        ctx['total_count'] = all_assets_qs.count()
        ctx['available_count'] = all_assets_qs.filter(status=m.Asset.Status.AVAILABLE).count()
        ctx['assigned_count'] = all_assets_qs.filter(status=m.Asset.Status.ASSIGNED).count()
        ctx['repair_count'] = all_assets_qs.filter(status=m.Asset.Status.UNDER_REPAIR).count()

        # 2. Apply status filter from clicked card query parameter (?status=available / assigned / under_repair)
        status_filter = self.request.GET.get('status', '').strip()
        filtered_qs = all_assets_qs
        if status_filter:
            filtered_qs = filtered_qs.filter(status=status_filter)

        ctx['current_status_filter'] = status_filter

        # 3. Build Category-wise Grouping using the filtered items
        categories = m.AssetCategory.objects.all().order_by('name')
        structured_data = []

        for cat in categories:
            type_qs = filtered_qs.filter(category=cat)
            if type_qs.exists():
                structured_data.append({
                    'id': cat.id,
                    'name': cat.name,
                    'assets': type_qs,
                    'total': all_assets_qs.filter(category=cat).count(),
                    'available': all_assets_qs.filter(category=cat, status=m.Asset.Status.AVAILABLE).count(),
                    'assigned': all_assets_qs.filter(category=cat, status=m.Asset.Status.ASSIGNED).count(),
                })

        ctx['structured_assets'] = structured_data
        ctx['flat_assets'] = filtered_qs
        ctx['hrms_is_hr'] = is_hr_or_above(self.request.user)
        return ctx


class AssetCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    """Creates an asset and auto-opens an assignment history log if issued on creation."""
    model = m.Asset
    form_class = f.AssetForm
    template_name = 'hrms/asset/asset_form.html'
    success_url = reverse_lazy('hrms:asset_list')
    active_group, active_item = 'asset', 'asset'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # JSON dictionary passed to JavaScript to toggle fields dynamically per category
        categories = m.AssetCategory.objects.all()
        ctx['category_configs_json'] = json.dumps({
            str(c.id): c.required_fields for c in categories
        })
        return ctx

    def form_valid(self, form):
        with transaction.atomic():
            self.object = form.save()
            # If an employee was selected during creation, create the initial custody history record
            if self.object.employee:
                m.AssetAssignmentHistory.objects.create(
                    asset=self.object,
                    employee=self.object.employee,
                    assigned_date=self.object.assigned_on or timezone.localdate(),
                    is_still_using=True,
                    assignment_notes=f"Initial issuance during asset registration: {self.object.remarks or ''}"
                )
        from django.contrib import messages
        messages.success(self.request, f"Asset '{self.object.name}' registered successfully.")
        return redirect(self.success_url)


class AssetUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    """Updates specifications and safely handles reassignments or unassignments."""
    model = m.Asset
    form_class = f.AssetForm
    template_name = 'hrms/asset/asset_form.html'
    success_url = reverse_lazy('hrms:asset_list')
    active_group, active_item = 'asset', 'asset'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        categories = m.AssetCategory.objects.all()
        ctx['category_configs_json'] = json.dumps({
            str(c.id): c.required_fields for c in categories
        })
        return ctx

    def form_valid(self, form):
        old_asset = m.Asset.objects.get(pk=self.object.pk)
        old_emp = old_asset.employee

        with transaction.atomic():
            self.object = form.save()
            new_emp = self.object.employee

            # Custody Change Scenario: Employee changed in form
            if old_emp != new_emp:
                today = timezone.localdate()
                # 1. Close active custody record for the previous employee
                if old_emp:
                    m.AssetAssignmentHistory.objects.filter(
                        asset=self.object,
                        employee=old_emp,
                        is_still_using=True
                    ).update(
                        returned_date=today,
                        is_still_using=False,
                        returned_in_good_condition=True,
                        return_remarks="Reassigned via Asset Edit Specification form."
                    )

                # 2. Open new custody record for the new employee
                if new_emp:
                    m.AssetAssignmentHistory.objects.create(
                        asset=self.object,
                        employee=new_emp,
                        assigned_date=self.object.assigned_on or today,
                        is_still_using=True,
                        assignment_notes=f"Reassigned from {old_emp.full_name if old_emp else 'Warehouse'}."
                    )

        from django.contrib import messages
        messages.success(self.request, f"Asset '{self.object.name}' updated successfully.")
        return redirect(self.success_url)


class AssetDetailView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    """Shows full specs, invoice downloads, security credentials, and custody lifecycle."""
    model = m.Asset
    template_name = 'hrms/asset/asset_detail.html'
    context_object_name = 'asset'
    active_group, active_item = 'asset', 'asset'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        # Fetch full chronological custody history with employee relations
        ctx['history'] = self.object.history.select_related('employee').order_by('-assigned_date')
        return ctx


class AssetReturnView(HRRequiredMixin, View):
    """Explicit return endpoint triggered from modal to unassign hardware and log condition."""

    def post(self, request, pk):
        asset = get_object_or_404(m.Asset, pk=pk)

        if asset.employee:
            good_cond_val = request.POST.get('returned_in_good_condition', 'yes')
            is_good = (good_cond_val == 'yes' or good_cond_val is True or good_cond_val == 'True')
            remarks = (request.POST.get('return_remarks') or '').strip()

            from django.contrib import messages
            if not is_good and not remarks:
                messages.error(request, "Return remarks are mandatory when an asset is returned in damaged condition.")
                return redirect('hrms:asset_detail', pk=pk)

            with transaction.atomic():
                # 1. Close the active assignment record in history
                m.AssetAssignmentHistory.objects.filter(
                    asset=asset,
                    employee=asset.employee,
                    is_still_using=True
                ).update(
                    returned_date=timezone.localdate(),
                    is_still_using=False,
                    returned_in_good_condition=is_good,
                    return_remarks=remarks or "Returned in good condition."
                )

                # 2. Reset Asset to Available state
                prev_holder = asset.employee.full_name
                asset.employee = None
                asset.assigned_on = None
                asset.status = m.Asset.Status.AVAILABLE if is_good else m.Asset.Status.UNDER_REPAIR
                asset.save()

            messages.success(request,
                             f"Asset successfully returned from {prev_holder} and moved to {asset.get_status_display()}.")
        else:
            from django.contrib import messages
            messages.info(request, "Asset is already stored in warehouse.")

        return redirect('hrms:asset_detail', pk=pk)


def create_category_ajax(request):
    """AJAX endpoint for instant category creation from modal with duplicate prevention."""
    if not is_hr_or_above(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            name = (data.get('name') or '').strip()
            fields = (data.get('fields') or '').strip()

            if not name:
                return JsonResponse({'success': False, 'error': 'Category name cannot be empty.'})

            # Check case-insensitive duplication
            if m.AssetCategory.objects.filter(name__iexact=name).exists():
                return JsonResponse({'success': False, 'error': f"Category '{name}' already exists."})

            cat = m.AssetCategory.objects.create(name=name, required_fields=fields)
            return JsonResponse({
                'success': True,
                'id': cat.id,
                'name': cat.name,
                'fields': cat.required_fields
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'success': False, 'error': 'Invalid HTTP method.'}, status=405)


# ===========================================================================
# PERFORMANCE REVIEWS
# ===========================================================================
class PerformanceReviewListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.PerformanceReview
    template_name = 'hrms/performance/performance_list.html'
    context_object_name = 'reviews'
    active_group, active_item = 'performance', 'performance'
    paginate_by = 30

    def get_queryset(self):
        qs = m.PerformanceReview.objects.select_related('employee', 'reviewer').order_by('-review_date')
        if is_hr_or_above(self.request.user):
            employee_id = self.request.GET.get('employee')
            if employee_id:
                qs = qs.filter(employee_id=employee_id)
            return qs
        employee = get_employee_profile(self.request.user)
        if employee is None:
            return m.PerformanceReview.objects.none()
        return qs.filter(employee=employee)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        if ctx['is_hr']:
            ctx['employees'] = m.Employee.objects.order_by('employee_code')
        return ctx


class PerformanceReviewCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.PerformanceReview
    form_class = f.PerformanceReviewForm
    template_name = 'hrms/performance/performance_form.html'
    success_url = reverse_lazy('hrms:performance_list')
    active_group, active_item = 'performance', 'performance'

    def form_valid(self, form):
        messages.success(self.request, 'Performance review created.')
        return super().form_valid(form)


class PerformanceReviewUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.PerformanceReview
    form_class = f.PerformanceReviewForm
    template_name = 'hrms/performance/performance_form.html'
    success_url = reverse_lazy('hrms:performance_list')
    active_group, active_item = 'performance', 'performance'

    def form_valid(self, form):
        messages.success(self.request, 'Performance review updated.')
        return super().form_valid(form)


class PerformanceReviewDetailView(EmployeeSelfOrHRMixin, SidebarContextMixin, DetailView):
    model = m.PerformanceReview
    template_name = 'hrms/performance/performance_detail.html'
    context_object_name = 'review'
    active_group, active_item = 'performance', 'performance'

    def get_object_employee(self, obj):
        return obj.employee


class PerformanceAcknowledgeView(LoginRequiredMixin, View):
    """The reviewed employee acknowledges their own submitted review."""

    def post(self, request, pk):
        review = get_object_or_404(m.PerformanceReview, pk=pk)
        employee = get_employee_profile(request.user)
        if not is_hr_or_above(request.user) and (employee is None or review.employee_id != employee.id):
            raise PermissionDenied("You can only acknowledge your own review.")
        if review.status != m.PerformanceReview.Status.SUBMITTED:
            messages.error(request, 'Only a submitted review can be acknowledged.')
        else:
            review.status = m.PerformanceReview.Status.ACKNOWLEDGED
            review.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Review acknowledged.')
        return redirect('hrms:performance_detail', pk=pk)


# ===========================================================================
# MODULE D: ATTENDANCE MATRIX & REGULARIZATION WORKFLOW
# ===========================================================================
class AttendanceMatrixView(HRRequiredMixin, SidebarContextMixin, TemplateView):
    """
    Advanced Attendance Matrix with frozen left columns (ID, Name, Designation)
    and horizontal date grid. Displays live punches, grace counts, leave tags,
    and audit flags for manually edited punches.
    Uses the unified attendance_engine so data matches the Punch Report exactly.
    """
    template_name = 'hrms/attendance/attendance_matrix.html'
    active_group, active_item = 'attendance', 'attendance_matrix'

    def get_context_data(self, **kwargs):
        from .attendance_engine import build_attendance, leave_wallet_options
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()

        # 1. Month / Year & Date Range Setup
        month_str = self.request.GET.get('month')
        year_str = self.request.GET.get('year')
        try:
            sel_month = int(month_str) if month_str else today.month
            sel_year = int(year_str) if year_str else today.year
        except ValueError:
            sel_month, sel_year = today.month, today.year

        import calendar as cal_mod
        num_days = cal_mod.monthrange(sel_year, sel_month)[1]
        date_list = [date(sel_year, sel_month, d) for d in range(1, num_days + 1)]
        start_date, end_date = date_list[0], date_list[-1]

        # 2. Filter Employees
        employees = m.Employee.objects.filter(status=m.Employee.Status.ACTIVE).select_related(
            'department', 'designation', 'company', 'holiday_calendar'
        ).order_by('employee_code')

        dept_id = self.request.GET.get('department')
        if dept_id:
            employees = employees.filter(department_id=dept_id)

        emp_id = self.request.GET.get('employee')
        if emp_id:
            employees = employees.filter(id=emp_id)

        # 3. Delegate classification to the engine (single source of truth)
        employees = list(employees)
        engine_data = build_attendance(employees, start_date, end_date, today=today)

        # 4. Build matrix rows for the template
        matrix_rows = []
        stats = {'total_present': 0, 'total_half_day': 0, 'total_on_leave': Decimal('0'),
                 'total_absent': Decimal('0'), 'total_holidays': 0}

        for emp in employees:
            edata = engine_data[emp.id]
            s = edata['summary']
            row_cells = edata['days']  # each day-dict from the engine
            matrix_rows.append({
                'employee': emp,
                'cells': row_cells,
                'stats': {
                    'present': s['present'],
                    'half_day': s['half_day'],
                    'on_leave': s['on_leave'],
                    'absent': s['absent'],
                    'holiday': s['holiday'],
                    'penalty_amount': s['penalty_amount'],
                },
                # per-employee leave wallet for the Quick Actions panel
                'leave_options': leave_wallet_options(emp),
            })
            stats['total_present'] += s['present']
            stats['total_half_day'] += s['half_day']
            stats['total_on_leave'] += s['on_leave']
            stats['total_absent'] += s['absent']
            stats['total_holidays'] += s['holiday']

        ctx['matrix_rows'] = matrix_rows
        ctx['date_list'] = date_list
        ctx['sel_month'] = sel_month
        ctx['sel_year'] = sel_year
        ctx['month_name'] = cal_mod.month_name[sel_month]
        ctx['months_choices'] = [(i, cal_mod.month_name[i]) for i in range(1, 13)]
        ctx['year_choices'] = [sel_year - 1, sel_year, sel_year + 1]
        ctx['departments'] = m.Department.objects.all()
        ctx['employees_list'] = m.Employee.objects.filter(status='active').order_by('first_name')
        ctx['stats'] = stats
        ctx['filters'] = self.request.GET
        return ctx

        # 1. Month / Year & Date Range Setup
        month_str = self.request.GET.get('month')
        year_str = self.request.GET.get('year')
        try:
            sel_month = int(month_str) if month_str else today.month
            sel_year = int(year_str) if year_str else today.year
        except ValueError:
            sel_month, sel_year = today.month, today.year

        import calendar
        num_days = calendar.monthrange(sel_year, sel_month)[1]
        date_list = [date(sel_year, sel_month, d) for d in range(1, num_days + 1)]
        start_date, end_date = date_list[0], date_list[-1]

        # 2. Filter Employees
        employees = m.Employee.objects.filter(status=m.Employee.Status.ACTIVE).select_related(
            'department', 'designation', 'company', 'holiday_calendar'
        ).order_by('employee_code')

        dept_id = self.request.GET.get('department')
        if dept_id:
            employees = employees.filter(department_id=dept_id)

        emp_id = self.request.GET.get('employee')
        if emp_id:
            employees = employees.filter(id=emp_id)

        # 3. Pre-fetch Attendance, Leaves, Holidays, Penalties
        records = m.AttendanceRecord.objects.filter(
            attendance_date__range=[start_date, end_date]
        ).select_related('edited_by')

        leaves = m.LeaveApplication.objects.filter(
            status=m.LeaveApplication.Status.APPROVED,
            start_date__lte=end_date,
            end_date__gte=start_date
        ).select_related('leave_type')

        holidays = m.Holiday.objects.filter(date__range=[start_date, end_date])
        holiday_lookup = collections.defaultdict(dict)
        for h in holidays:
            holiday_lookup[h.calendar_id][h.date] = h.name

        # Lookups by [employee_id][date]
        att_lookup = collections.defaultdict(dict)
        for r in records:
            att_lookup[r.employee_id][r.attendance_date] = r

        leave_lookup = collections.defaultdict(dict)
        for l in leaves:
            curr = max(l.start_date, start_date)
            while curr <= min(l.end_date, end_date):
                leave_lookup[l.employee_id][curr] = l.leave_type.code
                curr += timedelta(days=1)

        # 4. Build Matrix Matrix Map
        matrix_rows = []
        stats = {'total_present': 0, 'total_half_day': 0, 'total_on_leave': 0, 'total_absent': 0, 'total_holidays': 0}

        for emp in employees:
            row_cells = []
            emp_stats = {'present': 0, 'half_day': 0, 'on_leave': 0, 'absent': 0, 'holiday': 0}
            grace_tracker = 0

            for day in date_list:
                policy = emp.company.get_policy_for_date(day) if emp.company else None
                grace_mins = policy.grace_minutes if policy else 15
                grace_limit = 4  # Standard allowance: G1, G2, G3, G4
                off_start_time = policy.office_start_time if (policy and policy.office_start_time) else time(9, 0)

                rec = att_lookup[emp.id].get(day)
                leave_code = leave_lookup[emp.id].get(day)
                holiday_name = holiday_lookup[emp.holiday_calendar_id].get(day) if emp.holiday_calendar_id else None

                cell = {
                    'date': day,
                    'status': 'absent',
                    'badge': 'A',
                    'badge_class': 'bg-danger-subtle text-danger border-danger-subtle',
                    'label': 'Absent',
                    'record_id': rec.id if rec else None,
                    'check_in': None,
                    'check_out': None,
                    'in_str': '',
                    'out_str': '',
                    'is_edited': bool(rec and rec.edited_by),
                    'edit_reason': (rec.edit_reason or '') if rec else '',
                    'edited_by_name': (rec.edited_by.get_full_name() or rec.edited_by.username) if (
                                rec and rec.edited_by) else '',
                    'edited_on_str': timezone.localtime(rec.edited_on).strftime('%d %b %Y, %I:%M %p') if (
                                rec and rec.edited_on) else '',
                    'edit_tooltip': f"Edited by {rec.edited_by.username} on {rec.edited_on.strftime('%d %b %H:%M') if rec and rec.edited_on else ''}: {rec.edit_reason}" if (
                                rec and rec.edited_by) else '',
                }

                if rec and rec.check_in:
                    local_in = timezone.localtime(rec.check_in)
                    local_out = timezone.localtime(rec.check_out) if rec.check_out else None

                    cell['check_in'] = local_in.strftime('%H:%M')
                    cell['check_out'] = local_out.strftime('%H:%M') if local_out else None
                    cell['in_str'] = cell['check_in']
                    cell['out_str'] = cell['check_out'] or ''

                    # Check In vs Shift Start Time
                    sched_start_dt = datetime.combine(day, off_start_time)
                    if timezone.is_naive(sched_start_dt):
                        sched_start_dt = timezone.make_aware(sched_start_dt, timezone.get_current_timezone())

                    grace_cutoff_dt = sched_start_dt + timedelta(minutes=grace_mins)

                    # 1. Check manual status overrides first
                    if rec.status == m.AttendanceRecord.Status.ON_LEAVE:
                        cell['status'] = 'on_leave'
                        cell['badge'] = 'LV'
                        cell['badge_class'] = 'bg-primary-subtle text-primary border-primary-subtle'
                        cell['label'] = 'On Leave'
                        emp_stats['on_leave'] += 1
                        stats['total_on_leave'] += 1

                    elif local_in > grace_cutoff_dt:
                        # Late arrival beyond grace window -> Auto Half Day
                        cell['status'] = 'half_day'
                        cell['badge'] = 'HD'
                        cell['badge_class'] = 'bg-info-subtle text-info border-info-subtle'
                        cell[
                            'label'] = f"Half Day: Late Arrival after Grace Window (> {grace_cutoff_dt.strftime('%I:%M %p')})"
                        emp_stats['half_day'] += 1
                        stats['total_half_day'] += 1

                    elif local_in > sched_start_dt and local_in <= grace_cutoff_dt:
                        # Arrival in Grace Window (09:01 - 09:15)
                        grace_tracker += 1
                        if grace_tracker <= grace_limit:
                            cell['status'] = 'grace'
                            cell['badge'] = f'G{grace_tracker}'
                            cell['badge_class'] = 'bg-warning-subtle text-warning border-warning-subtle'
                            cell['label'] = f'Grace #{grace_tracker} used (Arrived at {local_in.strftime("%H:%M")})'
                            emp_stats['present'] += 1
                            stats['total_present'] += 1
                        else:
                            # Grace limit exhausted (G4 crossed) -> Auto Half Day
                            cell['status'] = 'half_day'
                            cell['badge'] = 'HD'
                            cell['badge_class'] = 'bg-info-subtle text-info border-info-subtle'
                            cell['label'] = f'Half Day: Grace Limit Exhausted (G{grace_limit} Crossed)'
                            emp_stats['half_day'] += 1
                            stats['total_half_day'] += 1

                    elif rec.total_hours > 0 and rec.total_hours < Decimal('4.0'):
                        # Short duration < 4 hours
                        cell['status'] = 'half_day'
                        cell['badge'] = 'HD'
                        cell['badge_class'] = 'bg-info-subtle text-info border-info-subtle'
                        cell['label'] = f'Half Day: Short Work Duration ({rec.total_hours} Hours < 4.0)'
                        emp_stats['half_day'] += 1
                        stats['total_half_day'] += 1

                    elif rec.status == m.AttendanceRecord.Status.HALF_DAY:
                        cell['status'] = 'half_day'
                        cell['badge'] = 'HD'
                        cell['badge_class'] = 'bg-info-subtle text-info border-info-subtle'
                        cell['label'] = 'Half Day'
                        emp_stats['half_day'] += 1
                        stats['total_half_day'] += 1

                    else:
                        cell['status'] = 'present'
                        cell['badge'] = 'P'
                        cell['badge_class'] = 'bg-success-subtle text-success border-success-subtle'
                        cell['label'] = 'Present'
                        emp_stats['present'] += 1
                        stats['total_present'] += 1

                elif leave_code:
                    cell['status'] = 'on_leave'
                    cell['badge'] = leave_code
                    cell['badge_class'] = 'bg-primary-subtle text-primary border-primary-subtle'
                    cell['label'] = f'Leave ({leave_code})'
                    emp_stats['on_leave'] += 1
                    stats['total_on_leave'] += 1

                elif holiday_name:
                    cell['status'] = 'holiday'
                    cell['badge'] = 'H'
                    cell['badge_class'] = 'bg-secondary-subtle text-secondary border-secondary-subtle'
                    cell['label'] = holiday_name
                    emp_stats['holiday'] += 1
                    stats['total_holidays'] += 1

                elif day.weekday() == 6:  # Sunday
                    cell['status'] = 'week_off'
                    cell['badge'] = 'OFF'
                    cell['badge_class'] = 'bg-light text-muted border'
                    cell['label'] = 'Sunday'

                elif day > today:
                    cell['status'] = 'future'
                    cell['badge'] = '-'
                    cell['badge_class'] = 'bg-transparent text-muted'
                    cell['label'] = 'Future'

                else:
                    emp_stats['absent'] += 1
                    stats['total_absent'] += 1

                row_cells.append(cell)

            matrix_rows.append({
                'employee': emp,
                'cells': row_cells,
                'stats': emp_stats,
            })

        ctx['matrix_rows'] = matrix_rows
        ctx['date_list'] = date_list
        ctx['sel_month'] = sel_month
        ctx['sel_year'] = sel_year
        ctx['month_name'] = calendar.month_name[sel_month]
        ctx['months_choices'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        ctx['year_choices'] = [sel_year - 1, sel_year, sel_year + 1]
        ctx['departments'] = m.Department.objects.all()
        ctx['employees_list'] = m.Employee.objects.filter(status='active').order_by('first_name')
        ctx['stats'] = stats
        ctx['filters'] = self.request.GET
        return ctx


@login_required
def manual_punch_edit_ajax(request):
    """AJAX endpoint for HR/SuperAdmin to edit punch records with mandatory audit reason."""
    if not is_hr_or_above(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=400)

    def parse_time_input(time_val, base_date, tz):
        if not time_val or str(time_val).strip() in ('', 'None', 'null'):
            return None
        cleaned = str(time_val).strip()
        for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I:%M%p', '%I:%M:%S %p'):
            try:
                t = datetime.strptime(cleaned, fmt).time()
                dt = datetime.combine(base_date, t)
                return timezone.make_aware(dt, tz)
            except ValueError:
                continue
        return None

    try:
        data = json.loads(request.body)
        record_id = data.get('record_id')
        emp_id = data.get('employee_id')
        date_str = data.get('date')
        check_in_str = data.get('check_in')
        check_out_str = data.get('check_out')
        status_val = data.get('status')
        edit_reason = (data.get('edit_reason') or '').strip()

        if not edit_reason:
            return JsonResponse({'success': False, 'error': 'A reason for manual edit is mandatory.'}, status=400)

        tz = timezone.get_current_timezone()

        if record_id:
            record = get_object_or_404(m.AttendanceRecord, pk=record_id)
        else:
            if not emp_id or not date_str:
                return JsonResponse({'success': False, 'error': 'Employee and Date required.'}, status=400)
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            record, _ = m.AttendanceRecord.objects.get_or_create(
                employee_id=emp_id, attendance_date=att_date
            )

        # Parse Times safely
        record.check_in = parse_time_input(check_in_str, record.attendance_date, tz)
        record.check_out = parse_time_input(check_out_str, record.attendance_date, tz)

        if status_val:
            record.status = status_val

        record.edited_by = request.user
        record.edited_on = timezone.now()
        record.edit_reason = edit_reason
        record.save()

        return JsonResponse({
            'success': True,
            'message': 'Punch updated successfully with audit trail.',
            'record_id': record.id,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def manual_punch_edit_ajax(request):
    """AJAX endpoint for HR/SuperAdmin to edit punch records with full audit and automatic leave sync/refund."""
    if not is_hr_or_above(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=400)

    def parse_time_input(time_val, base_date, tz):
        if not time_val or str(time_val).strip() in ('', 'None', 'null'):
            return None
        cleaned = str(time_val).strip()
        for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I:%M%p', '%I:%M:%S %p'):
            try:
                t = datetime.strptime(cleaned, fmt).time()
                dt = datetime.combine(base_date, t)
                return timezone.make_aware(dt, tz)
            except ValueError:
                continue
        return None

    try:
        data = json.loads(request.body)
        record_id = data.get('record_id')
        emp_id = data.get('employee_id')
        date_str = data.get('date')
        check_in_str = data.get('check_in')
        check_out_str = data.get('check_out')
        status_val = data.get('status')
        edit_reason = (data.get('edit_reason') or '').strip()

        if not edit_reason:
            return JsonResponse({'success': False, 'error': 'Reason / Remark is mandatory whenever modifying a punch.'},
                                status=400)

        tz = timezone.get_current_timezone()

        if record_id:
            record = get_object_or_404(m.AttendanceRecord, pk=record_id)
        else:
            if not emp_id or not date_str:
                return JsonResponse({'success': False, 'error': 'Employee and Date required.'}, status=400)
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            record, _ = m.AttendanceRecord.objects.get_or_create(
                employee_id=emp_id, attendance_date=att_date
            )

        with transaction.atomic():
            # 1. Update punch timestamps
            record.check_in = parse_time_input(check_in_str, record.attendance_date, tz)
            record.check_out = parse_time_input(check_out_str, record.attendance_date, tz)

            # 2. Update status & audit details
            old_status = record.status
            if status_val:
                record.status = status_val

            now_dt = timezone.now()
            now_str = timezone.localtime(now_dt).strftime('%d %b %Y, %I:%M %p')
            editor_name = request.user.get_full_name() or request.user.username

            changes = []
            if check_in_str:
                changes.append(f"In: {check_in_str}")
            if check_out_str:
                changes.append(f"Out: {check_out_str}")
            if status_val and status_val != old_status:
                changes.append(f"Status: {old_status} -> {status_val}")
            change_info = f" ({', '.join(changes)})" if changes else ""

            audit_entry = f"[{now_str} by {editor_name}]{change_info}: {edit_reason}"
            existing_reasons = (record.edit_reason or '').strip()
            if existing_reasons:
                record.edit_reason = f"{audit_entry}\n---\n{existing_reasons}"
            else:
                record.edit_reason = audit_entry

            record.remarks = edit_reason[:255]
            record.edited_by = request.user
            record.edited_on = now_dt

            # Calculate total hours if both punches exist
            if record.check_in and record.check_out:
                hrs = (record.check_out - record.check_in).total_seconds() / 3600.0
                record.total_hours = Decimal(str(round(hrs, 2)))
            record.save()

            # 3. SYNC & REFUND LEAVE APPLICATION
            # If the punch is marked 'present' (Full Day), any active leave on this date must be cancelled and refunded
            normalized_status = str(record.status).lower()
            if normalized_status in ['present', 'fd']:
                # Find approved leaves covering this exact date
                active_leaves = m.LeaveApplication.objects.filter(
                    employee=record.employee,
                    status=getattr(m.LeaveApplication.Status, 'APPROVED', 'approved'),
                    start_date__lte=record.attendance_date,
                    end_date__gte=record.attendance_date
                )

                wallet, _ = m.EmployeeLeaveBalanceLive.objects.get_or_create(e_name=record.employee)
                field_map = {
                    'CL': 'casual_leave', 'EL': 'earned_leave', 'SL': 'sick_leave',
                    'ML': 'menstrual_leave', 'MTL': 'menstrual_leave',
                    'BL': 'bereavement_leave', 'CO': 'comp_off'
                }

                for leave_app in active_leaves:
                    code = (leave_app.leave_type.code or '').upper().strip()
                    refund_qty = Decimal(str(leave_app.total_days or 1.0))
                    field_name = field_map.get(code)

                    # Refund back to the live wallet
                    if field_name and hasattr(wallet, field_name):
                        curr_val = Decimal(str(getattr(wallet, field_name, 0.0) or 0.0))
                        setattr(wallet, field_name, float(curr_val + refund_qty))
                        wallet.save()

                    # Cancel the leave so it stops showing on Matrix and Punch Analysis
                    leave_app.status = getattr(m.LeaveApplication.Status, 'CANCELLED', 'cancelled')
                    leave_app.rejection_reason = f"Auto-refunded: Marked Present via HR Audit Edit by {request.user.username}"
                    leave_app.save(update_fields=['status', 'rejection_reason'])

        return JsonResponse({
            'success': True,
            'message': 'Punch updated, leave cancelled, and balance refunded successfully.',
            'record_id': record.id,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===========================================================================
# QUICK ACTIONS AJAX  (Petpooja-style multi-action per cell)
# ===========================================================================
@login_required
def quick_action_ajax(request):
    """
    HR Quick Actions endpoint.  A single POST can combine:
      - attendance_status  : 'present' | 'half_day' | 'absent' | 'week_off'
      - use_leave         : true / false
      - leave_type_id     : int (required when use_leave=true)
      - leave_half        : true / false  (half-day leave)
      - add_penalty       : true / false
      - penalty_amount    : float
      - penalty_reason    : str
    All combinations are valid (e.g. half_day + leave + penalty simultaneously).
    """
    if not is_hr_or_above(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    try:
        from .attendance_engine import leave_wallet_options, wallet_field_for, MANUAL_PENALTY_SOURCE
        data = json.loads(request.body)

        emp_id = data.get('employee_id')
        date_str = data.get('date')
        if not emp_id or not date_str:
            return JsonResponse({'success': False, 'error': 'employee_id and date are required.'}, status=400)

        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        emp = get_object_or_404(m.Employee, pk=emp_id)

        attendance_status = data.get('attendance_status')
        use_leave = bool(data.get('use_leave'))
        leave_type_id = data.get('leave_type_id')
        leave_half = bool(data.get('leave_half'))
        add_penalty = bool(data.get('add_penalty'))
        penalty_amount = data.get('penalty_amount')
        penalty_reason = (data.get('penalty_reason') or '').strip()
        edit_reason = (data.get('edit_reason') or '').strip()
        if not edit_reason:
            return JsonResponse({'success': False, 'error': 'Audit Note / Remark is strictly mandatory.'}, status=400)

        with transaction.atomic():
            now_dt = timezone.now()
            now_str = timezone.localtime(now_dt).strftime('%d %b %Y, %I:%M %p')
            editor_name = request.user.get_full_name() or request.user.username
            audit_entry = f"[{now_str} by {editor_name}]: {edit_reason}"

            # 1. Attendance Record override
            if attendance_status:
                status_map = {
                    'present': m.AttendanceRecord.Status.PRESENT,
                    'half_day': m.AttendanceRecord.Status.HALF_DAY,
                    'absent': m.AttendanceRecord.Status.ABSENT,
                    'week_off': m.AttendanceRecord.Status.WEEK_OFF,
                }
                rec_status = status_map.get(attendance_status)
                if rec_status:
                    rec, _ = m.AttendanceRecord.objects.get_or_create(
                        employee=emp, attendance_date=att_date
                    )
                    existing_reasons = (rec.edit_reason or '').strip()
                    rec.edit_reason = f"{audit_entry}\n---\n{existing_reasons}" if existing_reasons else audit_entry
                    rec.remarks = edit_reason[:255]
                    rec.status = rec_status
                    rec.manual_override = True
                    rec.edited_by = request.user
                    rec.edited_on = now_dt
                    rec.save(
                        update_fields=['status', 'manual_override', 'edited_by', 'edited_on', 'edit_reason', 'remarks'])

            # 2. Use Leave
            messages_out = []
            if use_leave and leave_type_id:
                lt = get_object_or_404(m.LeaveType, pk=leave_type_id)
                day_type = 'half' if leave_half else 'full'
                deduct = Decimal('0.5') if leave_half else Decimal('1.0')

                # Cancel any existing auto-approved leave on this date to avoid double-count
                m.LeaveApplication.objects.filter(
                    employee=emp,
                    status=m.LeaveApplication.Status.APPROVED,
                    start_date__lte=att_date,
                    end_date__gte=att_date,
                ).update(
                    status=m.LeaveApplication.Status.CANCELLED,
                    rejection_reason='Superseded by HR Quick Action',
                )

                # Create a new single-day leave application already approved
                leave_app = m.LeaveApplication.objects.create(
                    employee=emp,
                    leave_type=lt,
                    start_date=att_date,
                    end_date=att_date,
                    day_type=day_type,
                    total_days=float(deduct),
                    status=m.LeaveApplication.Status.APPROVED,
                    reason=f'HR Quick Action by {request.user.username}: {edit_reason}',
                    applied_on=timezone.now().date(),
                )

                # If CO, deduct credits
                if (lt.code or '').upper() == 'CO':
                    from . import comp_off_logic as col
                    col.deduct_comp_off_credits(leave_app)

                # Deduct from the live wallet
                field = wallet_field_for(lt)
                if field:
                    wallet, _ = m.EmployeeLeaveBalanceLive.objects.get_or_create(e_name=emp)
                    curr = Decimal(str(getattr(wallet, field, 0) or 0))
                    new_val = max(Decimal('0'), curr - deduct)
                    setattr(wallet, field, float(new_val))
                    wallet.save(update_fields=[field])
                    messages_out.append(f'{lt.code} deducted ({deduct} day). Remaining: {new_val}')
                else:
                    messages_out.append(f'{lt.code} leave applied (no live-wallet field mapped)')

                # If attendance_status was not explicitly set, reflect the leave on the record
                if not attendance_status:
                    rec, _ = m.AttendanceRecord.objects.get_or_create(
                        employee=emp, attendance_date=att_date
                    )
                    existing_reasons = (rec.edit_reason or '').strip()
                    rec.edit_reason = f"{audit_entry}\n---\n{existing_reasons}" if existing_reasons else audit_entry
                    rec.remarks = edit_reason[:255]
                    rec.status = (m.AttendanceRecord.Status.HALF_DAY if leave_half
                                  else m.AttendanceRecord.Status.ON_LEAVE)
                    rec.manual_override = True
                    rec.edited_by = request.user
                    rec.edited_on = now_dt
                    rec.save(
                        update_fields=['status', 'manual_override', 'edited_by', 'edited_on', 'edit_reason', 'remarks'])

            # 3. Penalty
            if add_penalty and penalty_amount:
                try:
                    amount = Decimal(str(penalty_amount))
                except Exception:
                    return JsonResponse({'success': False, 'error': 'Invalid penalty amount.'}, status=400)
                m.AttendancePenalty.objects.create(
                    employee=emp,
                    penalty_date=att_date,
                    deduction_source=MANUAL_PENALTY_SOURCE,
                    salary_deduction_amount=amount,
                    reason=penalty_reason or f'Manual penalty by {request.user.username}',
                    status=m.AttendancePenalty.DeductionStatus.APPLIED,
                )
                messages_out.append(f'Penalty of Rs.{amount} added.')

        return JsonResponse({
            'success': True,
            'messages': messages_out or ['Action applied successfully.'],
        })

    except Exception as e:
        import traceback
        return JsonResponse({'success': False, 'error': str(e), 'trace': traceback.format_exc()}, status=500)


# ===========================================================================
# PUNCH REGULARIZATION WORKFLOW
# ===========================================================================
class PunchRegularizationListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.PunchRegularizationRequest
    template_name = 'hrms/attendance/regularization_list.html'
    context_object_name = 'requests'
    active_group, active_item = 'attendance', 'regularization'
    paginate_by = 20

    def get_queryset(self):
        qs = m.PunchRegularizationRequest.objects.select_related('employee', 'employee__department',
                                                                 'reviewed_by').order_by('-created_at')
        user = self.request.user
        if not is_hr_or_above(user):
            emp = get_employee_profile(user)
            if not emp:
                return m.PunchRegularizationRequest.objects.none()
            if emp.is_manager:
                qs = qs.filter(Q(employee=emp) | Q(employee__reporting_manager=emp))
            else:
                qs = qs.filter(employee=emp)

        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx['is_hr'] = is_hr_or_above(user)
        ctx['apply_form'] = f.PunchRegularizationRequestForm()
        ctx['review_form'] = f.PunchRegularizationReviewForm()
        return ctx


class PunchRegularizationCreateView(LoginRequiredMixin, SidebarContextMixin, CreateView):
    model = m.PunchRegularizationRequest
    form_class = f.PunchRegularizationRequestForm
    template_name = 'hrms/attendance/regularization_form.html'
    success_url = reverse_lazy('hrms:regularization_list')
    active_group, active_item = 'attendance', 'regularization'

    def form_valid(self, form):
        emp = get_employee_profile(self.request.user)
        if not emp:
            messages.error(self.request, "Your account is not linked to an employee profile.")
            return redirect('hrms:dashboard')
        form.instance.employee = emp
        messages.success(self.request, "Punch regularization request submitted successfully.")
        return super().form_valid(form)


class PunchRegularizationReviewView(HRRequiredMixin, View):
    """HR Review view for regularization with mandatory rejection remarks."""

    def post(self, request, pk):
        reg_req = get_object_or_404(m.PunchRegularizationRequest, pk=pk)
        action = request.POST.get('status')
        rejection_reason = (request.POST.get('rejection_reason') or '').strip()

        if action == 'rejected':
            if not rejection_reason:
                messages.error(request, "A rejection reason is mandatory when rejecting a regularization request.")
                return redirect('hrms:regularization_list')
            reg_req.status = m.PunchRegularizationRequest.Status.REJECTED
            reg_req.rejection_reason = rejection_reason
            reg_req.reviewed_by = request.user
            reg_req.reviewed_on = timezone.now()
            reg_req.save()
            messages.success(request, f"Regularization request rejected for {reg_req.employee.full_name}.")

        elif action == 'approved':
            # Create or update AttendanceRecord
            rec, _ = m.AttendanceRecord.objects.get_or_create(
                employee=reg_req.employee,
                attendance_date=reg_req.attendance_date,
            )
            if reg_req.requested_check_in:
                rec.check_in = reg_req.requested_check_in
            if reg_req.requested_check_out:
                rec.check_out = reg_req.requested_check_out

            rec.status = m.AttendanceRecord.Status.PRESENT
            rec.edited_by = request.user
            rec.edited_on = timezone.now()
            rec.edit_reason = f"Regularization approved: {reg_req.reason}"
            rec.save()

            reg_req.status = m.PunchRegularizationRequest.Status.APPROVED
            reg_req.reviewed_by = request.user
            reg_req.reviewed_on = timezone.now()
            reg_req.save()
            messages.success(request, f"Regularization approved and punch updated for {reg_req.employee.full_name}.")

        return redirect('hrms:regularization_list')


# ===========================================================================
# MODULE F: POLICIES, NOTICES & ACKNOWLEDGMENT QUIZ
# ===========================================================================
class PolicyListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    """Employee Policies Library view with categories and acknowledgment tracking."""
    model = m.Policy
    template_name = 'hrms/policy/policy_library.html'
    context_object_name = 'policies'
    active_group, active_item = 'policy', 'policy'

    def get_queryset(self):
        qs = m.Policy.objects.filter(is_active=True).select_related('company').order_by('-created_at')
        category = self.request.GET.get('category')
        q = self.request.GET.get('q')
        if category:
            qs = qs.filter(category=category)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        emp = get_employee_profile(user)
        ctx['is_hr'] = is_hr_or_above(user)
        ctx['categories'] = m.Policy.Category.choices
        ctx['active_cat'] = self.request.GET.get('category', '')

        # Build acknowledgment mapping for this employee
        if emp:
            acks = m.PolicyAcknowledgement.objects.filter(employee=emp)
            ctx['ack_map'] = {a.policy_id: a for a in acks}
        else:
            ctx['ack_map'] = {}

        return ctx


class PolicyManagementView(HRRequiredMixin, SidebarContextMixin, TemplateView):
    """HR Compliance dashboard tracking acknowledgment percentage per policy."""
    template_name = 'hrms/policy/policy_management.html'
    active_group, active_item = 'policy', 'policy_manage'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        policies = m.Policy.objects.all().order_by('-created_at')
        total_active_employees = m.Employee.objects.filter(status=m.Employee.Status.ACTIVE).count()

        policy_stats = []
        for pol in policies:
            acks = pol.acknowledgements.select_related('employee').all()
            ack_count = acks.count()
            pct = round((ack_count / total_active_employees * 100), 1) if total_active_employees else 0
            policy_stats.append({
                'policy': pol,
                'total_employees': total_active_employees,
                'ack_count': ack_count,
                'percentage': pct,
                'acknowledgements': acks,
                'has_quiz': pol.has_quiz,
            })

        ctx['policy_stats'] = policy_stats
        ctx['total_policies'] = policies.count()
        ctx['total_employees'] = total_active_employees
        return ctx


class PolicyCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.Policy
    form_class = f.PolicyForm
    template_name = 'hrms/policy/policy_form.html'
    success_url = reverse_lazy('hrms:policy_manage')
    active_group, active_item = 'policy', 'policy_manage'

    def form_valid(self, form):
        quiz_json = self.request.POST.get('quiz_data_json')
        if quiz_json:
            try:
                form.instance.quiz_data = json.loads(quiz_json)
            except Exception:
                pass
        messages.success(self.request, "Policy created successfully.")
        return super().form_valid(form)


class PolicyUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.Policy
    form_class = f.PolicyForm
    template_name = 'hrms/policy/policy_form.html'
    success_url = reverse_lazy('hrms:policy_manage')
    active_group, active_item = 'policy', 'policy_manage'

    def form_valid(self, form):
        quiz_json = self.request.POST.get('quiz_data_json')
        if quiz_json:
            try:
                form.instance.quiz_data = json.loads(quiz_json)
            except Exception:
                pass
        messages.success(self.request, "Policy updated successfully.")
        return super().form_valid(form)


class PolicyDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.Policy
    template_name = 'hrms/policy/policy_confirm_delete.html'
    success_url = reverse_lazy('hrms:policy_manage')
    active_group, active_item = 'policy', 'policy_manage'

    def form_valid(self, form):
        messages.success(self.request, "Policy deleted.")
        return super().form_valid(form)


class PolicyDetailView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    """
    Employee Policy Stepper View:
    1. Read / Download Document
    2. Summary Quiz Questionnaire (if questions exist)
    3. Final Compliance Acknowledgment
    """
    model = m.Policy
    template_name = 'hrms/policy/policy_quiz.html'
    context_object_name = 'policy'
    active_group, active_item = 'policy', 'policy'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        emp = get_employee_profile(self.request.user)
        ack = m.PolicyAcknowledgement.objects.filter(policy=self.object, employee=emp).first() if emp else None
        ctx['acknowledgement'] = ack
        ctx['is_acknowledged'] = bool(ack and ack.is_locked)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        return ctx

    def post(self, request, pk):
        policy = get_object_or_404(m.Policy, pk=pk)
        emp = get_employee_profile(request.user)
        if not emp:
            messages.error(request, "Your login is not linked to an employee record.")
            return redirect('hrms:policy_list')

        # Check existing acknowledgment
        ack, created = m.PolicyAcknowledgement.objects.get_or_create(policy=policy, employee=emp)
        if ack.is_locked:
            messages.info(request, "You have already acknowledged this policy.")
            return redirect('hrms:policy_detail', pk=pk)

        # 1. Validate acknowledgment checkbox
        confirmed = request.POST.get('acknowledge_checkbox')
        if not confirmed:
            messages.error(request, "You must check the acknowledgment statement checkbox to proceed.")
            return redirect('hrms:policy_detail', pk=pk)

        # 2. Score Quiz if policy has questions
        score = Decimal('100.0')
        passed = True
        responses = []

        if policy.quiz_data and len(policy.quiz_data) > 0:
            total_q = len(policy.quiz_data)
            correct_count = 0

            for idx, q_item in enumerate(policy.quiz_data):
                user_ans = request.POST.get(f'question_{idx}')
                correct_ans = str(q_item.get('correct', ''))
                is_correct = (str(user_ans).strip().lower() == correct_ans.strip().lower())
                if is_correct:
                    correct_count += 1
                responses.append({
                    'question_index': idx,
                    'user_answer': user_ans,
                    'correct': is_correct
                })

            score = Decimal(round((correct_count / total_q * 100), 2))
            if score < Decimal('80.0'):
                passed = False
                messages.error(
                    request,
                    f"You scored {score}%. You need at least 80% to pass the Summary Quiz. Please review the policy and re-attempt."
                )
                return redirect('hrms:policy_detail', pk=pk)

        # 3. Save Acknowledgment
        ack.quiz_responses = responses
        ack.quiz_score = score
        ack.quiz_passed = passed
        ack.is_locked = True
        ack.acknowledgment_text = request.POST.get('ack_statement',
                                                   'I acknowledge that I have read and fully understand all terms and conditions of this policy.')
        ack.save()

        messages.success(request, f"Policy '{policy.title}' acknowledged successfully! Quiz Score: {score}%.")
        return redirect('hrms:policy_detail', pk=pk)


# --- Company Notices ---
class CompanyNoticeListView(LoginRequiredMixin, SidebarContextMixin, ListView):
    model = m.CompanyNotice
    template_name = 'hrms/notice/notice_list.html'
    context_object_name = 'notices'
    active_group, active_item = 'policy', 'notice'

    def get_queryset(self):
        return m.CompanyNotice.objects.filter(is_active=True).order_by('-notice_date')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        return ctx


class CompanyNoticeCreateView(HRRequiredMixin, SidebarContextMixin, CreateView):
    model = m.CompanyNotice
    form_class = f.CompanyNoticeForm
    template_name = 'hrms/notice/notice_form.html'
    success_url = reverse_lazy('hrms:notice_list')
    active_group, active_item = 'policy', 'notice'

    def form_valid(self, form):
        messages.success(self.request, "Notice posted.")
        return super().form_valid(form)


class CompanyNoticeDetailView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    model = m.CompanyNotice
    template_name = 'hrms/notice/notice_detail.html'
    context_object_name = 'notice'
    active_group, active_item = 'policy', 'notice'

    def get(self, request, *args, **kwargs):
        res = super().get(request, *args, **kwargs)
        # Mark as read
        emp = get_employee_profile(request.user)
        if emp:
            m.NoticeRead.objects.get_or_create(notice=self.object, employee=emp)
        return res

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['is_hr'] = is_hr_or_above(self.request.user)
        ctx['read_count'] = self.object.reads.count()
        return ctx


class CompanyNoticeUpdateView(HRRequiredMixin, SidebarContextMixin, UpdateView):
    model = m.CompanyNotice
    form_class = f.CompanyNoticeForm
    template_name = 'hrms/notice/notice_form.html'
    success_url = reverse_lazy('hrms:notice_list')
    active_group, active_item = 'policy', 'notice'

    def form_valid(self, form):
        messages.success(self.request, "Notice updated.")
        return super().form_valid(form)


class CompanyNoticeDeleteView(HRRequiredMixin, SidebarContextMixin, DeleteView):
    model = m.CompanyNotice
    template_name = 'hrms/notice/notice_confirm_delete.html'
    success_url = reverse_lazy('hrms:notice_list')
    active_group, active_item = 'policy', 'notice'

    def form_valid(self, form):
        messages.success(self.request, "Notice deleted.")
        return super().form_valid(form)


# ===========================================================================
# MODULE C: LEAVE DETAIL & REAPPLY VIEWS
# ===========================================================================
class LeaveDetailView(LoginRequiredMixin, SidebarContextMixin, DetailView):
    """
    Visual Timeline Stepper & Full Audit Log for Leave Application.
    """
    model = m.LeaveApplication
    template_name = 'hrms/leave/leave_detail.html'
    context_object_name = 'application'
    active_group, active_item = 'leave', 'my_leave'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        app = self.object
        user = self.request.user
        emp = get_employee_profile(user)
        ctx['is_hr'] = is_hr_or_above(user)
        ctx['is_manager'] = (emp and emp.is_manager)
        ctx['approval_logs'] = app.approval_logs.select_related('performed_by').all()
        ctx['reapplications'] = app.reapplications.all()
        ctx['reject_form'] = f.LeaveRejectForm()
        return ctx


class LeaveReapplyView(LoginRequiredMixin, SidebarContextMixin, FormView):
    """Allows an employee to re-apply for a rejected leave application with updated reason."""
    template_name = 'hrms/leave/leave_reapply.html'
    form_class = f.LeaveApplicationForm
    active_group, active_item = 'leave', 'my_leave'

    def dispatch(self, request, *args, **kwargs):
        self.original_app = get_object_or_404(m.LeaveApplication, pk=kwargs['pk'])
        emp = get_employee_profile(request.user)
        if not is_hr_or_above(request.user) and (not emp or self.original_app.employee != emp):
            raise PermissionDenied("You can only re-apply for your own rejected leaves.")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {
            'leave_type': self.original_app.leave_type,
            'day_type': self.original_app.day_type,
            'start_date': self.original_app.start_date,
            'end_date': self.original_app.end_date,
            'relationship': self.original_app.relationship,
            'leave_stage': self.original_app.leave_stage,
            'reason': f"Re-applying for #{self.original_app.pk}: ",
        }

    def form_valid(self, form):
        emp = self.original_app.employee
        try:
            new_app = lv.reapply_leave(
                original_application=self.original_app,
                employee=emp,
                reason=form.cleaned_data.get('reason', ''),
                start_date=form.cleaned_data['start_date'],
                end_date=form.cleaned_data['end_date'],
                day_type=form.cleaned_data['day_type'],
                supporting_document=form.cleaned_data.get('supporting_document'),
                relationship=form.cleaned_data.get('relationship', ''),
                leave_stage=form.cleaned_data.get('leave_stage', ''),
            )
            messages.success(self.request, f"Re-application #{new_app.pk} submitted successfully.")
            return redirect('hrms:leave_detail', pk=new_app.pk)
        except lv.LeaveError as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)


# ---------------------------

import json
from django.views.generic import TemplateView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from .models import Employee, EmployeeBiometric, AttendanceRecord, EmployeeLocationLog
from .biometrics import verify_1_to_1, match_1_to_n, extract_face_encoding, DUPLICATE_THRESHOLD

import base64
import io
import logging
from concurrent.futures import ThreadPoolExecutor

from django.core.files.base import ContentFile

_bio_logger = logging.getLogger('hrms.biometrics')

# Background thread pool for async face verification (3 workers for peak hour)
_BIO_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix='bio-verify')


# ─────────────────────────────────────────────────────────────
# Background Verification Helpers
# ─────────────────────────────────────────────────────────────
def _verify_mobile_async(record_id, photo_bytes, reference_encoding):
    """
    Runs in a background thread after the HTTP response has already been sent.
    Verifies the captured face against the employee's registered embedding.
    """
    from django.db import connection
    try:
        is_match, code, msg, dist = verify_1_to_1(photo_bytes, reference_encoding)
        record = AttendanceRecord.objects.get(pk=record_id)

        if is_match:
            record.is_face_verified = True
            record.save(update_fields=['is_face_verified', 'updated_at'])
            _bio_logger.info("[Async Verify] Record %s — MATCH (%s)", record_id, msg)
        else:
            record.remarks = f"Biometric mismatch – Flagged for HR review ({msg})"
            record.save(update_fields=['remarks', 'updated_at'])
            _bio_logger.warning("[Async Verify] Record %s — MISMATCH (%s)", record_id, msg)

    except Exception as exc:
        _bio_logger.error("[Async Verify] Record %s — ERROR: %s", record_id, exc)
    finally:
        connection.close()


def _verify_kiosk_async(record_id, photo_bytes, matched_employee_id):
    """
    Runs in a background thread to re-confirm kiosk match and update record.
    The kiosk already identified the employee via vectorized 1:N; this re-verifies 1:1.
    """
    from django.db import connection
    try:
        biometric = EmployeeBiometric.objects.filter(employee_id=matched_employee_id).first()
        if not biometric:
            return

        is_match, code, msg, dist = verify_1_to_1(photo_bytes, biometric.face_encoding)
        record = AttendanceRecord.objects.get(pk=record_id)

        if is_match:
            record.is_face_verified = True
            record.save(update_fields=['is_face_verified', 'updated_at'])
        else:
            record.remarks = f"Kiosk re-verify mismatch ({msg}) – Flagged for HR"
            record.save(update_fields=['remarks', 'updated_at'])

    except Exception as exc:
        _bio_logger.error("[Async Kiosk Verify] Record %s — ERROR: %s", record_id, exc)
    finally:
        connection.close()


# ─────────────────────────────────────────────────────────────
# 1. FACE ENROLLMENT (HR / Admin Action)
# ─────────────────────────────────────────────────────────────
class FaceEnrollmentView(LoginRequiredMixin, TemplateView):
    template_name = 'hrms/attendance/enroll_face.html'

    def get_context_data(self, **kwargs):
        """GET request: Loads the HTML page with the employee dropdown."""
        context = super().get_context_data(**kwargs)
        context['employees'] = Employee.objects.filter(status='active').order_by('first_name')
        return context

    def _get_image_source(self, request):
        """
        Extract image from either a file upload or a base64 canvas capture.
        Returns (image_source_for_extraction, photo_file_for_storage, error_message).
        """
        # Priority 1: Standard file upload
        photo = request.FILES.get('photo')
        if photo:
            return photo, photo, None

        # Priority 2: Base64 string from canvas capture
        image_data = request.POST.get('image_data', '').strip()
        if image_data:
            # Return the raw base64 string for extraction (biometrics.py handles it)
            # Also build a ContentFile for saving to registered_photo
            try:
                import re
                cleaned = re.sub(r'^data:image/[^;]+;base64,', '', image_data, flags=re.IGNORECASE)
                raw_bytes = base64.b64decode(cleaned)
                photo_file = ContentFile(raw_bytes, name='captured_face.jpg')
                return image_data, photo_file, None
            except Exception:
                return None, None, 'Invalid image data received from camera.'

        return None, None, 'Please capture a photo using the webcam or select an image file.'

    def post(self, request, *args, **kwargs):
        """POST request: Receives image (file or base64), extracts vector, saves biometrics."""
        employee_id = request.POST.get('employee_id')
        if not employee_id:
            return JsonResponse({'success': False, 'error': 'Please select an employee.'}, status=400)

        employee = Employee.objects.filter(id=employee_id, status='active').first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found or inactive.'}, status=404)

        # Get image from file upload or base64 canvas
        image_source, photo_file, img_error = self._get_image_source(request)
        if img_error:
            return JsonResponse({'success': False, 'error': img_error}, status=400)

        # Extract face encoding (tuple return: encoding, error_dict)
        encoding, extract_error = extract_face_encoding(image_source)
        if encoding is None:
            err_msg = extract_error.get('message') if isinstance(extract_error, dict) else (
                    extract_error or (
                'Could not detect a clear face in the photo. '
                'Ensure good lighting, face the camera directly, '
                'and avoid tilting your head.'
            )
            )
            err_code = extract_error.get('code') if isinstance(extract_error, dict) else 'NO_FACE'
            return JsonResponse({
                'success': False,
                'code': err_code,
                'error': err_msg,
                'message': err_msg
            }, status=400)

        # Check if this face is ALREADY enrolled for ANY OTHER employee
        existing_biometrics = EmployeeBiometric.objects.exclude(employee=employee).select_related('employee')
        duplicate_emp, dup_code, dup_msg, dup_dist = match_1_to_n(image_source, existing_biometrics,
                                                                  threshold=DUPLICATE_THRESHOLD)
        if duplicate_emp:
            return JsonResponse({
                'success': False,
                'code': 'DUPLICATE_FACE',
                'error': f'Duplicate Face Detected! This face is already enrolled under '
                         f'{duplicate_emp.full_name} ({duplicate_emp.employee_code}).',
                'message': f'Duplicate Face Detected! This face is already enrolled under '
                           f'{duplicate_emp.full_name} ({duplicate_emp.employee_code}).'
            }, status=409)

        EmployeeBiometric.objects.update_or_create(
            employee=employee,
            defaults={'face_encoding': encoding, 'registered_photo': photo_file}
        )
        return JsonResponse({
            'success': True,
            'message': f'Face enrolled successfully for {employee.full_name}'
        })


# ─────────────────────────────────────────────────────────────
# 2. MOBILE PUNCH (1:1 — Synchronous Verification, Strict Rejection)
# ─────────────────────────────────────────────────────────────
class MobilePunchInView(LoginRequiredMixin, View):
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            employee = request.user.employee_profile
        except AttributeError:
            return JsonResponse({'success': False, 'code': 'NO_PROFILE', 'error': 'No linked employee profile found.'},
                                status=400)

        # Security check for attendance mode
        if employee.attendance_mode != Employee.AttendanceMode.REMOTE_FIELD:
            return JsonResponse({
                'success': False,
                'code': 'OFFICE_ONLY',
                'error': 'Remote mobile punch is disabled for your profile. '
                         'Please punch using the office tablet at reception.'
            }, status=403)

        photo = request.FILES.get('punch_photo')
        lat = request.POST.get('latitude')
        lng = request.POST.get('longitude')

        if not photo or not lat or not lng:
            return JsonResponse(
                {'success': False, 'code': 'MISSING_DATA', 'error': 'Photo and GPS coordinates are required.'},
                status=400)

        biometric = getattr(employee, 'biometric', None)
        if not biometric:
            return JsonResponse({
                'success': False,
                'code': 'NOT_ENROLLED',
                'error': 'Face not enrolled. You must register your biometric face profile first.',
                'message': 'Face not enrolled. You must register your biometric face profile first.'
            }, status=400)

        # ──────────────────────────────────────────────────────
        # SYNCHRONOUS 1:1 VERIFICATION — Must pass BEFORE record
        # ──────────────────────────────────────────────────────
        punch_time = timezone.now()
        today = timezone.localdate()

        # Read photo bytes for verification
        photo_bytes = photo.read()
        photo.seek(0)  # Reset for Django's file save

        is_match, verify_code, verify_msg, verify_dist = verify_1_to_1(photo_bytes, biometric.face_encoding)
        _bio_logger.info(
            "[Mobile Punch] Employee %s (%s) — match=%s, code=%s, msg=%s, dist=%s",
            employee.full_name, employee.employee_code, is_match, verify_code, verify_msg, verify_dist
        )

        if not is_match:
            return JsonResponse({
                'success': False,
                'code': verify_code or 'VERIFICATION_FAILED',
                'error': f'Face verification failed. {verify_msg} '
                         f'Please ensure you are facing the camera directly with good lighting.',
                'message': f'Face verification failed. {verify_msg} '
                           f'Please ensure you are facing the camera directly with good lighting.',
                'status': 'rejected'
            }, status=403)

        # ── Fetch today's attendance record (preserve existing punches on face update) ──
        attendance = AttendanceRecord.objects.filter(employee=employee, attendance_date=today).first()

        # ── Strict 3-State Flow with Double-Tap Protection ──
        if attendance is None:
            # State 1: Fresh Punch In
            attendance = AttendanceRecord.objects.create(
                employee=employee,
                attendance_date=today,
                check_in=punch_time,
                punch_in_latitude=lat,
                punch_in_longitude=lng,
                punch_in_photo=photo,
                is_face_verified=True,
                punch_source='mobile',
                status=AttendanceRecord.Status.PRESENT,
            )
            action_type = "Punch In"
        elif not attendance.check_in:
            # Record exists (e.g. created by admin/leave) but no check-in -> Punch In
            attendance.check_in = punch_time
            attendance.punch_in_latitude = lat
            attendance.punch_in_longitude = lng
            attendance.punch_in_photo = photo
            attendance.is_face_verified = True
            attendance.punch_source = 'mobile'
            attendance.status = AttendanceRecord.Status.PRESENT
            attendance.save()
            action_type = "Punch In"
        elif not attendance.check_out:
            # State 2: Check-in exists, attempting Punch Out
            # Double-tap guard: prevent accidental immediate punch-out within 60 seconds
            seconds_since_check_in = (punch_time - attendance.check_in).total_seconds()
            if seconds_since_check_in < 60:
                return JsonResponse({
                    'success': False,
                    'code': 'DOUBLE_TAP_GUARD',
                    'error': f"Punch In already recorded {int(seconds_since_check_in)}s ago. Please wait 1 minute before punching out.",
                    'message': f"Punch In already recorded {int(seconds_since_check_in)}s ago. Please wait 1 minute before punching out."
                }, status=400)

            attendance.check_out = punch_time
            attendance.punch_out_latitude = lat
            attendance.punch_out_longitude = lng
            attendance.save()  # Triggers total_hours and status recalculation
            action_type = "Punch Out"
        else:
            # State 3: Both check_in and check_out already recorded -> Day is complete!
            return JsonResponse({
                'success': False,
                'code': 'DAY_COMPLETE',
                'error': f"{employee.full_name}: You have already punched in and out today. Attendance is complete.",
                'message': f"{employee.full_name}: You have already punched in and out today. Attendance is complete."
            }, status=400)

        # ── Comp Off Credit (Punch In on Sunday / Holiday only) ───────────
        if action_type == "Punch In":
            try:
                from .comp_off_logic import is_off_day, credit_comp_off
                is_off, _reason = is_off_day(employee, today)
                if is_off:
                    credit_comp_off(employee, attendance)
            except Exception:
                pass  # Never block the punch on a CO error

        return JsonResponse({
            'success': True,
            'status': 'success',
            'action': action_type,
            'attendance_id': attendance.id,
            'time': punch_time.strftime('%I:%M %p'),
            'message': f'{action_type} recorded at {punch_time.strftime("%I:%M %p")}. Face verified successfully.'
        })


# ─────────────────────────────────────────────────────────────
# 3. KIOSK PUNCH (1:N — Vectorised Matching, Async Re-verify)
# ─────────────────────────────────────────────────────────────
class KioskPunchInView(View):
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def _get_image_source(self, request):
        """
        Extract image from file upload or base64 canvas data.
        Returns (image_source, photo_file_for_storage, photo_bytes, error).
        """
        photo = request.FILES.get('punch_photo')
        if photo:
            photo_bytes = photo.read()
            photo.seek(0)
            return photo, photo, photo_bytes, None

        image_data = request.POST.get('image_data', '').strip()
        if image_data:
            try:
                import re
                cleaned = re.sub(r'^data:image/[^;]+;base64,', '', image_data, flags=re.IGNORECASE)
                raw_bytes = base64.b64decode(cleaned)
                photo_file = ContentFile(raw_bytes, name='kiosk_frame.jpg')
                return image_data, photo_file, raw_bytes, None
            except Exception:
                return None, None, None, 'Invalid image data from camera.'

        return None, None, None, 'Camera frame required.'

    def post(self, request, *args, **kwargs):
        image_source, photo_file, photo_bytes, img_error = self._get_image_source(request)
        if img_error:
            return JsonResponse({'success': False, 'code': 'INVALID_IMAGE', 'error': img_error, 'message': img_error},
                                status=400)

        # Verify biometric enrollment exists in system
        all_biometrics = EmployeeBiometric.objects.select_related('employee').all()
        if not all_biometrics.exists():
            return JsonResponse({
                'success': False,
                'code': 'NO_ENROLLED_FACES',
                'error': 'No enrolled biometrics found in the system. Please enroll your face first.',
                'message': 'No enrolled biometrics found in the system. Please enroll your face first.'
            }, status=400)

        # Vectorised 1:N match with calibrated threshold
        matched_employee, match_code, match_msg, match_dist = match_1_to_n(image_source, all_biometrics)

        if not matched_employee:
            return JsonResponse({
                'success': False,
                'code': match_code or 'NOT_RECOGNIZED',
                'error': match_msg or 'Face not recognized. Ensure you are enrolled and facing the camera directly.',
                'message': match_msg or 'Face not recognized. Ensure you are enrolled and facing the camera directly.'
            }, status=401)

        # Check biometric profile of matched employee
        biometric = getattr(matched_employee, 'biometric', None)
        if not biometric:
            return JsonResponse({
                'success': False,
                'code': 'NOT_ENROLLED',
                'employee_name': matched_employee.full_name,
                'error': f'{matched_employee.full_name} is not enrolled in biometrics. Please enroll your face first.',
                'message': f'{matched_employee.full_name} is not enrolled in biometrics. Please enroll your face first.'
            }, status=400)

        today = timezone.localdate()
        now = timezone.now()

        # ── Fetch today's attendance record (preserve existing punches on face update) ──
        attendance = AttendanceRecord.objects.filter(employee=matched_employee, attendance_date=today).first()

        # ── Strict 3-State Flow with Double-Tap Protection ──
        if attendance is None:
            # State 1: Fresh Punch In
            attendance = AttendanceRecord.objects.create(
                employee=matched_employee,
                attendance_date=today,
                check_in=now,
                punch_in_photo=photo_file,
                is_face_verified=True,
                punch_source='kiosk',
                status=AttendanceRecord.Status.PRESENT,
            )
            action_type = "Punch In"
        elif not attendance.check_in:
            # Record exists but no check_in yet -> Punch In
            attendance.check_in = now
            attendance.punch_in_photo = photo_file
            attendance.is_face_verified = True
            attendance.punch_source = 'kiosk'
            attendance.status = AttendanceRecord.Status.PRESENT
            attendance.save()
            action_type = "Punch In"
        elif not attendance.check_out:
            # State 2: Check-in exists, attempting Punch Out
            # Double-tap guard: prevent accidental immediate punch-out within 60 seconds
            seconds_since_check_in = (now - attendance.check_in).total_seconds()
            if seconds_since_check_in < 60:
                return JsonResponse({
                    'success': False,
                    'code': 'DOUBLE_TAP_GUARD',
                    'employee_name': matched_employee.full_name,
                    'error': f"{matched_employee.full_name}: Punch In already recorded {int(seconds_since_check_in)}s ago. Please wait 1 minute before punching out.",
                    'message': f"{matched_employee.full_name}: Punch In already recorded {int(seconds_since_check_in)}s ago. Please wait 1 minute before punching out."
                }, status=400)

            attendance.check_out = now
            attendance.save()  # Recalculates gross hours and thresholds
            action_type = "Punch Out"
        else:
            # State 3: Both check_in and check_out already recorded -> Day is complete!
            return JsonResponse({
                'success': False,
                'code': 'DAY_COMPLETE',
                'employee_name': matched_employee.full_name,
                'error': f"{matched_employee.full_name}: You have already punched in and out today. Attendance is complete.",
                'message': f"{matched_employee.full_name}: You have already punched in and out today. Attendance is complete."
            }, status=400)

        # ── Comp Off Credit (Punch In on Sunday / Holiday only) ───────────
        if action_type == "Punch In":
            try:
                from .comp_off_logic import is_off_day, credit_comp_off
                is_off, _reason = is_off_day(matched_employee, today)
                if is_off:
                    credit_comp_off(matched_employee, attendance)
            except Exception:
                pass  # Never block the punch on a CO error

        # Optional: background re-verification for audit trail
        if photo_bytes:
            _BIO_POOL.submit(
                _verify_kiosk_async,
                attendance.id,
                photo_bytes,
                matched_employee.id,
            )

        return JsonResponse({
            'success': True,
            'employee_name': matched_employee.full_name,
            'employee_code': matched_employee.employee_code,
            'action': action_type,
            'time': now.strftime('%I:%M %p'),
            'message': f'{matched_employee.full_name}: {action_type} recorded at {now.strftime("%I:%M %p")}'
        })


# ─────────────────────────────────────────────────────────────
# 4. LOCATION PING API (Background Tracking Loop)
# ─────────────────────────────────────────────────────────────
class LocationPingView(LoginRequiredMixin, View):
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            EmployeeLocationLog.objects.create(
                employee=request.user.employee_profile,
                attendance_record_id=data.get('attendance_id'),
                latitude=data.get('latitude'),
                longitude=data.get('longitude'),
                accuracy_meters=data.get('accuracy')
            )
            return JsonResponse({'status': 'Location logged.'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


# ─────────────────────────────────────────────────────────────
# 5. LIVE TRACKING DASHBOARD & DATA FEED
# ─────────────────────────────────────────────────────────────
class LiveTrackingDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'hrms/attendance/live_tracking.html'


class LiveTrackingFeedAPIView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        records = AttendanceRecord.objects.filter(attendance_date=today, check_in__isnull=False).select_related(
            'employee')

        feed = []
        for att in records:
            logs = att.locations.all().order_by('recorded_at')
            path = [[float(l.latitude), float(l.longitude)] for l in logs]
            last_log = logs.last()

            lat = float(last_log.latitude) if last_log else (
                float(att.punch_in_latitude) if att.punch_in_latitude else None)
            lng = float(last_log.longitude) if last_log else (
                float(att.punch_in_longitude) if att.punch_in_longitude else None)

            if lat and lng:
                feed.append({
                    'name': att.employee.full_name,
                    'code': att.employee.employee_code,
                    'check_in': att.check_in.strftime('%I:%M %p'),
                    'last_seen': last_log.recorded_at.strftime('%I:%M %p') if last_log else att.check_in.strftime(
                        '%I:%M %p'),
                    'coords': [lat, lng],
                    'photo': att.punch_in_photo.url if att.punch_in_photo else None,
                    'route': path
                })

        return JsonResponse({'active_staff': feed})


# ─────────────────────────────────────────────────────────────
# 6. PAGE VIEWS (Template Renderers)
# ─────────────────────────────────────────────────────────────
class PunchInPageView(LoginRequiredMixin, TemplateView):
    template_name = 'hrms/attendance/punch_in.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        emp = getattr(self.request.user, 'employee_profile', None)
        # Pass remote permission status and biometric enrollment to template
        ctx['is_remote_allowed'] = emp.attendance_mode == Employee.AttendanceMode.REMOTE_FIELD if emp else False
        ctx['is_face_enrolled'] = hasattr(emp, 'biometric') and emp.biometric is not None if emp else False
        return ctx


class KioskPageView(TemplateView):
    template_name = 'hrms/attendance/kiosk.html'


from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import Employee, EmployeeBiometric


class BiometricStatusListView(LoginRequiredMixin, TemplateView):
    template_name = 'hrms/attendance/biometric_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Fetch active employees and annotate biometric existence
        employees = Employee.objects.filter(status='active').select_related('biometric', 'department', 'designation')

        enrolled_list = []
        pending_list = []

        for emp in employees:
            if hasattr(emp, 'biometric') and emp.biometric:
                enrolled_list.append(emp)
            else:
                pending_list.append(emp)

        context['enrolled_employees'] = enrolled_list
        context['pending_employees'] = pending_list
        context['total_count'] = len(employees)
        context['enrolled_count'] = len(enrolled_list)
        context['pending_count'] = len(pending_list)
        return context


import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class FinanceRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restricts access to Superadmins, HRs, and employees with is_finance=True."""

    def test_func(self):
        user = self.request.user
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        profile = getattr(user, 'employee_profile', None)
        if profile:
            return bool(profile.is_finance or is_hr_or_above(user))
        return False

    def handle_no_permission(self):
        messages.error(self.request,
                       "Access Denied: Only Finance & Accounts personnel can access the Bulk Payout module.")
        return redirect('hrms:dashboard')


class BulkPaymentDisbursementView(RBACViewMixin, SidebarContextMixin, View):
    """
    Excel-like interactive workbench for Finance to review, adjust,
    and export the Corporate Bank Bulk Payout spreadsheet.
    """
    template_name = 'hrms/payroll/bulk_payment_payout.html'
    active_group, active_item = 'payroll', 'bulk_payout'
    rbac_permission = "payroll.payout"

    def get(self, request):
        today = timezone.localdate()
        sel_month = int(request.GET.get('month', today.month))
        sel_year = int(request.GET.get('year', today.year))
        activation_date = request.GET.get('activation_date', today.strftime('%Y-%m-%d'))
        action = request.GET.get('action')

        active_company_id = request.session.get('active_company_id')
        comp_qs = m.Company.objects.all()
        selected_company = None
        if active_company_id and active_company_id != 'all':
            selected_company = m.Company.objects.filter(id=active_company_id).first()
        if not selected_company:
            selected_company = comp_qs.first()

        # 1. Fetch active staff with their salary and bank details
        employees = m.Employee.objects.filter(
            status=m.Employee.Status.ACTIVE
        ).select_related('bank_detail', 'department', 'designation', 'company').order_by('employee_code')

        if selected_company:
            employees = employees.filter(company=selected_company)

        # 2. Fetch processed payslips for the selected month/year
        payslips = {
            p.employee_id: p
            for p in m.PaySlip.objects.filter(
                payroll_run__month=sel_month,
                payroll_run__year=sel_year
            )
        }

        # 3. Build the automated rows
        rows = []
        crn_counter = 1
        month_name = calendar.month_name[sel_month]
        default_debit_acc = "926030003897014"  # Default corporate debit account

        for emp in employees:
            bank = getattr(emp, 'bank_detail', None)
            slip = payslips.get(emp.id)

            # Resolve salary amount
            if slip and slip.net_pay > 0:
                salary_amount = float(slip.net_pay)
            else:
                active_sal = emp.salaries.filter(is_active=True).first()
                salary_amount = float(round(active_sal.ctc_annual / Decimal('12.0'), 2)) if active_sal else 0.0

            beneficiary_name = (bank.account_holder if bank and bank.account_holder else emp.full_name).upper()
            acc_num = bank.account_number if bank else ""
            ifsc = bank.ifsc_code.upper() if bank else ""
            crn_code = f"OHC{crn_counter:03d}"
            remarks = f"{month_name} Salary"

            row_data = {
                'emp': emp,
                'payment_method': 'I',  # 'I' for IMPS/NEFT corporate bulk
                'amount': salary_amount,
                'activation_date': activation_date,
                'beneficiary_name': beneficiary_name,
                'account_number': acc_num,
                'email': emp.email or "",
                'email_body': "",
                'debit_account': default_debit_acc,
                'crn_no': crn_code,
                'ifsc': ifsc,
                'account_type': '10',  # 10: Savings
                'remarks': remarks,
                'phone': emp.phone or "",
                'has_bank': bool(bank and bank.account_number and bank.ifsc_code),
                'bank_name': bank.bank_name if bank else 'No Bank Linked',
            }
            rows.append(row_data)
            crn_counter += 1

        # 4. Handle Excel Export matching the corporate bank upload template
        if action == 'export_excel':
            return self.export_bank_excel(rows, selected_company, month_name, sel_year)

        total_payout = sum(r['amount'] for r in rows)

        context = {
            'active_group': self.active_group,
            'active_item': self.active_item,
            'rows': rows,
            'total_payout': total_payout,
            'total_employees': len(rows),
            'sel_month': sel_month,
            'sel_year': sel_year,
            'month_name': month_name,
            'activation_date': activation_date,
            'months_choices': [(i, calendar.month_name[i]) for i in range(1, 13)],
            'year_choices': [sel_year - 1, sel_year, sel_year + 1],
            'selected_company': selected_company,
        }
        return render(request, self.template_name, context)

    def export_bank_excel(self, rows, company, month_name, year):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"

        # Headers matching corporate bank bulk payment specification
        headers = [
            'Payment Method Name', 'Payment Amount (Request)', 'Activation Date',
            'Beneficiary Name (Request)', 'Account No', 'Email', 'Email Body',
            'Debit Account No', 'CRN No', 'RECEIVER IFSC Code', 'RECEIVER Account Type',
            'Remarks', 'Phone No'
        ]
        ws.append(headers)

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="101B2D", end_color="101B2D", fill_type="solid")
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r in rows:
            ws.append([
                r['payment_method'],
                r['amount'],
                r['activation_date'],
                r['beneficiary_name'],
                str(r['account_number']),
                r['email'],
                r['email_body'],
                str(r['debit_account']),
                r['crn_no'],
                r['ifsc'],
                r['account_type'],
                r['remarks'],
                r['phone']
            ])

        # Formatting column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

        filename = f"Bulk_Salary_Payout_{month_name}_{year}.xlsx"
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response


# ---------------------------------------------------------------------------
# COMP OFF HISTORY VIEWS (Module F)
# ---------------------------------------------------------------------------

class CompOffHistoryView(LoginRequiredMixin, SidebarContextMixin, ListView):
    """
    Employee self-service view: shows the logged-in employee's own
    CompOffRecord history — earned dates, credits, and redemption status.
    """
    template_name = 'hrms/leave/comp_off_history.html'
    context_object_name = 'comp_off_records'
    paginate_by = 20
    active_group, active_item = 'leave', 'comp_off'

    def get_queryset(self):
        try:
            employee = self.request.user.employee_profile
        except AttributeError:
            return m.CompOffRecord.objects.none()
        return (
            m.CompOffRecord.objects
            .filter(employee=employee)
            .select_related('attendance_record', 'availed_leave_app',
                            'availed_leave_app__leave_type',
                            'availed_leave_app__approved_by')
            .order_by('-worked_date')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            employee = self.request.user.employee_profile
            all_records = m.CompOffRecord.objects.filter(employee=employee)
            from django.db.models import Sum
            totals = all_records.aggregate(
                total_earned=Sum('credits_earned'),
                availed_credits=Sum('credits_used'),
            )
            total_earned = totals['total_earned'] or Decimal('0.0')
            availed_credits = totals['availed_credits'] or Decimal('0.0')

            lt_co = m.LeaveType.objects.filter(code='CO').first()
            if lt_co:
                from . import leave_logic as lv
                rem_bal = lv.get_monthly_available_days(employee, lt_co)
            else:
                rem_bal = max(Decimal('0.0'), total_earned - availed_credits)

            ctx['total_earned'] = float(total_earned)
            ctx['availed_credits'] = float(availed_credits)
            ctx['comp_off_balance'] = float(rem_bal)

            ctx['redeemed_leaves'] = m.LeaveApplication.objects.filter(
                employee=employee,
                leave_type__code='CO',
            ).select_related('leave_type', 'approved_by').order_by('-start_date')
        except AttributeError:
            ctx['total_earned'] = ctx['availed_credits'] = ctx['comp_off_balance'] = 0
            ctx['redeemed_leaves'] = []
        return ctx


class CompOffHRView(HRRequiredMixin, SidebarContextMixin, ListView):
    """
    HR view: all employees' CompOffRecord rows with filters for
    company, employee, status. Includes top-line metric counters.
    """
    template_name = 'hrms/leave/comp_off_hr.html'
    context_object_name = 'comp_off_records'
    paginate_by = 30
    active_group, active_item = 'leave', 'comp_off'

    def get_queryset(self):
        qs = (
            m.CompOffRecord.objects
            .select_related(
                'employee', 'employee__department',
                'attendance_record',
                'availed_leave_app', 'availed_leave_app__approved_by'
            )
            .order_by('-worked_date')
        )
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        emp_id = self.request.GET.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        # Company scoping: only filter if the user is locked to a specific company
        # Superadmin (is_superuser) always sees all companies
        if not self.request.user.is_superuser:
            active_company_id = self.request.session.get('active_company_id')
            if active_company_id:
                qs = qs.filter(employee__company_id=active_company_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Sum, Count

        # Global stats (unfiltered for the counters at top)
        all_qs = m.CompOffRecord.objects.all()
        active_company_id = None  # default — superadmin sees all companies
        if not self.request.user.is_superuser:
            active_company_id = self.request.session.get('active_company_id')
            if active_company_id:
                all_qs = all_qs.filter(employee__company_id=active_company_id)

        agg = all_qs.aggregate(
            total_earned=Sum('credits_earned'),
            total_availed=Sum(
                'credits_earned',
                filter=Q(status=m.CompOffRecord.Status.AVAILED)
            ),
            total_available=Sum(
                'credits_earned',
                filter=Q(status=m.CompOffRecord.Status.AVAILABLE)
            ),
            total_records=Count('id'),
        )
        ctx['total_earned'] = agg['total_earned'] or 0
        ctx['total_availed'] = agg['total_availed'] or 0
        ctx['total_available'] = agg['total_available'] or 0
        ctx['total_records'] = agg['total_records'] or 0

        ctx['status_choices'] = m.CompOffRecord.Status.choices
        ctx['selected_status'] = self.request.GET.get('status', '')
        ctx['selected_employee'] = self.request.GET.get('employee', '')

        # Employee list for filter dropdown
        if active_company_id:
            ctx['employees'] = m.Employee.objects.filter(
                company_id=active_company_id, status='active'
            ).order_by('first_name')
        else:
            ctx['employees'] = m.Employee.objects.filter(
                status='active'
            ).order_by('first_name')
        return ctx


# ===========================================================================
# HIRING MODULE — NEW VIEWS (Bulk Upload Page, Talent Pool, Pipeline APIs)
# ===========================================================================

class BulkUploadPageView(HRRequiredMixin, SidebarContextMixin, DetailView):
    """Page view that renders the Alpine.js dropzone for bulk resume uploads.
    The actual upload POST hits BulkResumeUploadAPIView (api_views.py)."""
    model = m.JobPosting
    template_name = 'hrms/hiring/bulk_upload.html'
    context_object_name = 'job'
    active_group, active_item = 'hiring', 'jobposting'


class TalentPoolView(HRRequiredMixin, SidebarContextMixin, ListView):
    """Keyword search across all Candidates — by name, email, company, experience range."""
    model = m.Candidate
    template_name = 'hrms/hiring/talent_pool.html'
    context_object_name = 'candidates'
    paginate_by = 50
    active_group, active_item = 'hiring', 'talent_pool'

    def get_queryset(self):
        qs = m.Candidate.objects.prefetch_related(
            'applications__job_posting'
        ).order_by('-created_at')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) |
                Q(last_name__icontains=q) |
                Q(email__icontains=q) |
                Q(phone__icontains=q) |
                Q(current_company__icontains=q)
            )
        exp_min = self.request.GET.get('exp_min', '').strip()
        exp_max = self.request.GET.get('exp_max', '').strip()
        if exp_min:
            try:
                qs = qs.filter(experience_years__gte=float(exp_min))
            except ValueError:
                pass
        if exp_max:
            try:
                qs = qs.filter(experience_years__lte=float(exp_max))
            except ValueError:
                pass
        company = self.request.GET.get('company', '').strip()
        if company:
            qs = qs.filter(current_company__icontains=company)
        return qs


# ---------------------------------------------------------------------------
# Pipeline Management API Endpoints
# ---------------------------------------------------------------------------

class PipelineReorderAPIView(HRRequiredMixin, View):
    """POST /hrms/api/jobs/<pk>/pipeline/reorder/
    Body: {"stages": [{"pipeline_pk": 3, "order": 1}, ...]}
    """

    def post(self, request, pk):
        import json as _json
        from django.http import JsonResponse
        job = get_object_or_404(m.JobPosting, pk=pk)
        try:
            payload = _json.loads(request.body)
            stages = payload.get('stages', [])
            if not stages:
                return JsonResponse({'error': 'No stages provided.'}, status=400)
            with transaction.atomic():
                for item in stages:
                    m.JobPipeline.objects.filter(
                        pk=item['pipeline_pk'], job=job
                    ).update(order=item['order'])
            return JsonResponse({'ok': True, 'reordered': len(stages)})
        except Exception as exc:
            return JsonResponse({'error': str(exc)}, status=400)


class PipelineAddStageAPIView(HRRequiredMixin, View):
    """POST /hrms/api/jobs/<pk>/pipeline/add-stage/
    Body: {existing_stage_id: int} OR {new_stage_name: str, new_stage_description: str, evaluation_criteria: dict}
    """

    def post(self, request, pk):
        import json as _json
        from django.http import JsonResponse
        job = get_object_or_404(m.JobPosting, pk=pk)
        try:
            payload = _json.loads(request.body)
            existing_id = payload.get('existing_stage_id')
            if existing_id:
                stage = get_object_or_404(m.RecruitmentStage, pk=existing_id)
            else:
                name = (payload.get('new_stage_name') or '').strip()
                if not name:
                    return JsonResponse({'error': 'Stage name is required.'}, status=400)
                stage, _ = m.RecruitmentStage.objects.get_or_create(
                    name=name,
                    defaults={
                        'description': payload.get('new_stage_description', ''),
                        'evaluation_criteria': payload.get('evaluation_criteria', {}),
                    }
                )

            # Check not already in pipeline
            if m.JobPipeline.objects.filter(job=job, stage=stage).exists():
                return JsonResponse({'error': f'"{stage.name}" is already in this pipeline.'}, status=400)

            max_order = m.JobPipeline.objects.filter(job=job).aggregate(
                m=models.Max('order'))['m'] or 0
            m.JobPipeline.objects.create(job=job, stage=stage, order=max_order + 1)
            return JsonResponse({'ok': True, 'stage_id': stage.pk, 'stage_name': stage.name})
        except Exception as exc:
            return JsonResponse({'error': str(exc)}, status=400)


class PipelineRemoveStageAPIView(HRRequiredMixin, View):
    """DELETE /hrms/api/pipeline-stages/<pk>/remove/"""

    def delete(self, request, pk):
        from django.http import JsonResponse
        pipeline_entry = get_object_or_404(m.JobPipeline, pk=pk)
        pipeline_entry.delete()
        return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Quick Email API (Application Detail page modal)
# ---------------------------------------------------------------------------

class ApplicationSendEmailAPIView(HRRequiredMixin, View):
    """POST /hrms/api/applications/<pk>/send-email/
    Body: {"to": str, "subject": str, "body": str}
    Sends a plain-text email from the configured DEFAULT_FROM_EMAIL.
    """

    def post(self, request, pk):
        import json as _json
        from django.http import JsonResponse
        from django.core.mail import send_mail
        from django.conf import settings
        application = get_object_or_404(m.Application, pk=pk)
        try:
            payload = _json.loads(request.body)
            to = payload.get('to', '').strip()
            subject = payload.get('subject', '').strip()
            body = payload.get('body', '').strip()
            if not (to and subject and body):
                return JsonResponse({'ok': False, 'error': 'to, subject and body are required.'}, status=400)
            send_mail(
                subject, body,
                getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@oblu.com'),
                [to],
                fail_silently=False
            )
            m.RecruitmentAuditLog.objects.create(
                application=application,
                from_status=application.status,
                to_status=application.status,
                action=f'Quick email sent: "{subject}"',
                performed_by=request.user,
            )
            return JsonResponse({'ok': True})
        except Exception as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Enhanced ConvertToEmployeeView (with resume migration + leave balance seeding)
# ---------------------------------------------------------------------------

class ConvertToEmployeeView(HRRequiredMixin, SidebarContextMixin, View):
    """Once an offer is ACCEPTED, turn the candidate into a real Employee record.
    Also:
      - Migrates the candidate's resume to EmployeeDocument (document_type='resume')
      - Auto-creates EmployeeLeaveBalance and EmployeeLeaveBalanceLive rows
      - Sets Application.is_locked = True and writes an audit log
    """
    active_group, active_item = 'hiring', 'application'
    template_name = 'hrms/hiring/convert_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.offer = get_object_or_404(m.OfferLetter, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = f.ConvertToEmployeeForm()
        return render(request, self.template_name, {
            'form': form, 'offer': self.offer,
            'active_group': self.active_group, 'active_item': self.active_item,
        })

    def post(self, request, pk):
        from . import hiring_logic as hire
        form = f.ConvertToEmployeeForm(request.POST)
        if form.is_valid():
            try:
                employee = hire.convert_to_employee(
                    self.offer, request.user,
                    company=form.cleaned_data['company'],
                    department=form.cleaned_data.get('department'),
                    designation=form.cleaned_data.get('designation'),
                    employee_code=form.cleaned_data['employee_code'],
                )

                candidate = self.offer.application.candidate

                # ── Migrate resume to EmployeeDocument ──────────────────────
                if candidate.resume:
                    try:
                        from django.core.files.base import ContentFile
                        resume_content = candidate.resume.read()
                        doc = m.EmployeeDocument(
                            employee=employee,
                            document_type='resume',
                            document_name=f'Resume - {candidate}',
                        )
                        doc.file.save(
                            f'resume_{employee.employee_code}.pdf',
                            ContentFile(resume_content),
                            save=True
                        )
                    except Exception as e:
                        logger.warning(f'Resume migration failed for {employee}: {e}')

                # ── Seed leave balances from company LeaveTypes ──────────────
                try:
                    leave_types = m.LeaveType.objects.filter(
                        company=employee.company, is_active=True
                    )
                    live_bal, _ = m.EmployeeLeaveBalanceLive.objects.get_or_create(e_name=employee)
                    bank_bal, _ = m.EmployeeLeaveBalance.objects.get_or_create(e_name=employee)
                    for lt in leave_types:
                        alloc = float(lt.default_days_per_year or 0)
                        if 'casual' in lt.name.lower() or lt.name.upper() in ('CL',):
                            if not live_bal.casual_leave:
                                live_bal.casual_leave = alloc
                            if not bank_bal.casual_leave:
                                bank_bal.casual_leave = alloc
                        elif 'earned' in lt.name.lower() or lt.name.upper() in ('EL', 'PL'):
                            if not live_bal.earned_leave:
                                live_bal.earned_leave = alloc
                            if not bank_bal.earned_leave:
                                bank_bal.earned_leave = alloc
                        elif 'sick' in lt.name.lower() or lt.name.upper() in ('SL', 'ML'):
                            if not live_bal.sick_leave:
                                live_bal.sick_leave = alloc
                            if not bank_bal.sick_leave:
                                bank_bal.sick_leave = alloc
                    live_bal.save()
                    bank_bal.save()
                except Exception as e:
                    logger.warning(f'Leave balance seeding failed for {employee}: {e}')

                # ── Lock application + audit ─────────────────────────────────
                application = self.offer.application
                if not application.is_locked:
                    application.is_locked = True
                    application.locked_by = request.user
                    application.locked_at = timezone.now()
                    application.save(update_fields=['is_locked', 'locked_by', 'locked_at', 'updated_at'])
                m.RecruitmentAuditLog.objects.create(
                    application=application,
                    from_status=application.status,
                    to_status=application.status,
                    action=f'Converted to Employee ({employee.employee_code})',
                    performed_by=request.user,
                    note='Resume migrated; leave balances seeded.',
                )

                messages.success(request, f'{employee.full_name} onboarded as {employee.employee_code}.')
                return redirect('hrms:employee_detail', pk=employee.pk)
            except Exception as e:
                form.add_error(None, str(e))
        return render(request, self.template_name, {
            'form': form, 'offer': self.offer,
            'active_group': self.active_group, 'active_item': self.active_item,
        })