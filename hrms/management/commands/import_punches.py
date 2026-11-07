import re
from datetime import datetime, date, time
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from hrms import models as m
# python manage.py import_punches --file "C:\Users\Lenovo\Downloads\daily_punch_report (3).xlsx"

class Command(BaseCommand):
    help = "Imports daily/monthly attendance punches and leave codes from daily_punch_report.xlsx"

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='daily_punch_report.xlsx',
            help='Path to the Excel file (default: daily_punch_report.xlsx)'
        )

    def parse_time_str(self, time_val, att_date):
        """Converts time string (e.g., '09:12 AM', '09:12', '18:45') into aware datetime."""
        if not time_val:
            return None
        if isinstance(time_val, datetime):
            if timezone.is_naive(time_val):
                return timezone.make_aware(time_val)
            return time_val
        if isinstance(time_val, time):
            dt = datetime.combine(att_date, time_val)
            return timezone.make_aware(dt)

        cleaned = str(time_val).strip().upper()
        for fmt in ('%I:%M %p', '%I:%M%p', '%H:%M:%S', '%H:%M', '%I:%M:%S %p'):
            try:
                parsed_t = datetime.strptime(cleaned, fmt).time()
                dt = datetime.combine(att_date, parsed_t)
                return timezone.make_aware(dt)
            except ValueError:
                continue
        return None

    def handle(self, *args, **options):
        file_path = options['file']
        self.stdout.write(self.style.NOTICE(f"Opening Punch Matrix Excel file: {file_path}"))

        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)
            sheet = wb.active
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to load Excel file {file_path}: {e}"))
            return

        # 1. Identify Date Columns in Header
        header_row = [str(cell.value or '').strip() for cell in sheet[1]]
        date_col_map = {}  # col_idx -> date_obj

        date_regex = re.compile(r'(\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3})[-/.]\d{2,4})')

        for idx, h_text in enumerate(header_row):
            match = date_regex.search(h_text)
            if match:
                raw_d = match.group(1)
                parsed_date = None
                for dfmt in ('%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y', '%Y-%m-%d', '%d-%b-%Y', '%d/%b/%Y', '%d-%m-%y'):
                    try:
                        parsed_date = datetime.strptime(raw_d, dfmt).date()
                        break
                    except ValueError:
                        continue
                if parsed_date:
                    date_col_map[idx] = parsed_date

        if not date_col_map:
            self.stdout.write(self.style.ERROR("No date columns identified in header row."))
            return

        self.stdout.write(self.style.NOTICE(f"Identified {len(date_col_map)} date columns: {sorted(list(date_col_map.values()))[0]} to {sorted(list(date_col_map.values()))[-1]}"))

        # Pre-fetch employees
        employees_by_code = {e.employee_code.lower(): e for e in m.Employee.objects.all()}
        employees_by_name = {e.full_name.lower(): e for e in m.Employee.objects.all()}

        time_regex = re.compile(r'(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)')

        created_punches = 0
        updated_punches = 0
        leave_records = 0
        skipped_rows = 0

        with transaction.atomic():
            for row_idx, row in enumerate(sheet.iter_rows(min_row=2), start=2):
                # First two columns usually contain Code and Name
                raw_code = str(row[0].value or '').strip()
                raw_name = str(row[1].value or '').strip() if len(row) > 1 else ''

                if not raw_code and not raw_name:
                    continue

                emp = employees_by_code.get(raw_code.lower())
                if not emp:
                    # Try digit padding match e.g. OHCE0012 vs 12
                    cleaned_num = re.sub(r'[^\d]', '', raw_code)
                    if cleaned_num:
                        for k, v in employees_by_code.items():
                            if str(int(cleaned_num)) in k:
                                emp = v
                                break

                if not emp and raw_name:
                    emp = employees_by_name.get(raw_name.lower())

                if not emp:
                    skipped_rows += 1
                    continue

                # Process each date column
                for col_idx, att_date in date_col_map.items():
                    if col_idx >= len(row):
                        continue
                    cell_val = str(row[col_idx].value or '').strip()
                    if not cell_val or cell_val in ('None', '-'):
                        continue

                    upper_val = cell_val.upper()

                    # Check for explicit leave / absent tokens
                    if upper_val in ('ABS', 'ABSENT', 'A'):
                        m.AttendanceRecord.objects.update_or_create(
                            employee=emp,
                            attendance_date=att_date,
                            defaults={
                                'status': m.AttendanceRecord.Status.ABSENT,
                                'check_in': None,
                                'check_out': None,
                            }
                        )
                        continue

                    if upper_val in ('WO', 'OFF', 'WEEK OFF', 'WEEKOFF', 'SUNDAY'):
                        m.AttendanceRecord.objects.update_or_create(
                            employee=emp,
                            attendance_date=att_date,
                            defaults={
                                'status': m.AttendanceRecord.Status.WEEK_OFF,
                                                                                                                                                                                              'check_in': None,
                                'check_out': None,
                            }
                        )
                        continue

                    if upper_val in ('H', 'HOLIDAY'):
                        m.AttendanceRecord.objects.update_or_create(
                            employee=emp,
                            attendance_date=att_date,
                            defaults={
                                'status': m.AttendanceRecord.Status.HOLIDAY,
                                'check_in': None,
                                'check_out': None,
                            }
                        )
                        continue

                    # Leave codes
                    leave_type_match = None
                    for code in ('CL', 'EL', 'SL', 'LWP', 'UL', 'ML', 'BL', 'COMP_OFF'):
                        if code in upper_val:
                            leave_type_match = m.LeaveType.objects.filter(code__iexact=code).first()
                            break

                    if leave_type_match:
                        m.AttendanceRecord.objects.update_or_create(
                            employee=emp,
                            attendance_date=att_date,
                            defaults={
                                'status': m.AttendanceRecord.Status.ON_LEAVE,
                                'check_in': None,
                                'check_out': None,
                            }
                        )
                        leave_records += 1
                        continue

                    # Extract Times
                    times_found = time_regex.findall(cell_val)
                    if times_found:
                        in_time_raw = times_found[0]
                        out_time_raw = times_found[1] if len(times_found) > 1 else None

                        in_dt = self.parse_time_str(in_time_raw, att_date)
                        out_dt = self.parse_time_str(out_time_raw, att_date) if out_time_raw else None

                        # Determine Status
                        rec_status = m.AttendanceRecord.Status.PRESENT
                        if not in_dt:
                            rec_status = m.AttendanceRecord.Status.ABSENT

                        rec, created = m.AttendanceRecord.objects.update_or_create(
                            employee=emp,
                            attendance_date=att_date,
                            defaults={
                                'status': rec_status,
                                'check_in': in_dt,
                                'check_out': out_dt,
                            }
                        )
                        if created:
                            created_punches += 1
                        else:
                            updated_punches += 1

        self.stdout.write(self.style.SUCCESS(
            f"Punch Matrix Ingestion Complete!\n"
            f"Created Punches: {created_punches}\n"
            f"Updated Punches: {updated_punches}\n"
            f"Leave / Absent Records: {leave_records}\n"
            f"Skipped Unknown Employees: {skipped_rows}"
        ))