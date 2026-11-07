import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Q
from hrms import models as m

User = get_user_model()

# python manage.py import_employees --file Employee_data_HRMS.xlsx

class Command(BaseCommand):
    help = "Imports employee master records from Excel matching the 21 standard HRMS columns."

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='Employee_data_HRMS.xlsx',
            help='Path to the Excel file (default: Employee_data_HRMS.xlsx)'
        )
        parser.add_argument(
            '--company',
            type=str,
            default='Oblu Healthcare LLP',
            help='Default company name fallback'
        )

    def parse_date_value(self, val):
        """Converts heterogeneous Excel date strings, timestamps, and dot-separated dates into valid date objects."""
        if not val:
            return None
        if isinstance(val, (datetime, date)):
            return val.date() if isinstance(val, datetime) else val

        val_str = str(val).strip()
        if val_str in ('-', '--', 'nan', 'None', '', 'null', 'NaN', 'N/A', 'n/a'):
            return None

        # Strip trailing timestamps (e.g., '2025-01-05 00:00:00' -> '2025-01-05')
        if ' ' in val_str and (':' in val_str or len(val_str.split()[0]) >= 8):
            val_str = val_str.split()[0].strip()

        # Date pattern matchers (prioritizing DD.MM.YYYY, DD/MM/YYYY, DD-MM-YYYY)
        formats = (
            '%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y',
            '%d/%m/%y', '%d-%m-%y', '%d.%m.%y',
            '%Y-%m-%d', '%Y/%m/%d',
            '%d %b %Y', '%d-%b-%Y', '%d %B %Y',
            '%m/%d/%Y', '%m/%d/%y',
        )
        for fmt in formats:
            try:
                return datetime.strptime(val_str, fmt).date()
            except ValueError:
                continue
        return None

    def clean_phone(self, val):
        if not val or str(val).strip() in ('-', '--', 'nan', 'None', '', 'N/A', 'n/a'):
            return ''
        cleaned = re.sub(r'[^\d+,/ ]', '', str(val).strip())
        if cleaned.endswith('.0'):
            cleaned = cleaned[:-2]
        return cleaned[:50]

    def parse_decimal(self, val):
        if not val or str(val).strip() in ('-', '--', 'nan', 'None', ''):
            return Decimal('0.00')
        # Clean currency commas and whitespace: "17,000.00" -> "17000.00"
        clean_val = re.sub(r'[^\d.]', '', str(val).strip())
        try:
            return Decimal(clean_val).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            return Decimal('0.00')

    def handle(self, *args, **options):
        file_path = options['file']
        default_company_name = options['company'].strip()

        self.stdout.write(self.style.NOTICE(f"Loading Excel file: {file_path}"))

        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)
            sheet = wb.active
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to read file: {e}"))
            return

        header_row = [str(cell.value or '').strip() for cell in sheet[1]]
        header_map = {}
        for idx, h in enumerate(header_row):
            norm = h.lower().replace(' ', '_').replace('.', '').replace(':', '').strip()
            header_map[norm] = idx

        def get_val(row_cells, *possible_keys):
            for k in possible_keys:
                norm_k = k.lower().replace(' ', '_').replace('.', '').replace(':', '').strip()
                if norm_k in header_map:
                    idx = header_map[norm_k]
                    if idx < len(row_cells):
                        v = row_cells[idx].value
                        return str(v).strip() if v is not None else ''
            return ''

        created_count = 0
        updated_count = 0
        linked_users_list = []
        unlinked_users_list = []
        status_breakdown = {'active': 0, 'inactive': 0, 'terminated': 0, 'probation': 0}
        manager_links_to_resolve = []

        rows = list(sheet.iter_rows(min_row=2))
        self.stdout.write(self.style.NOTICE(f"Processing {len(rows)} employee records in atomic transaction..."))

        with transaction.atomic():
            for row_num, row_cells in enumerate(rows, start=2):
                raw_code = get_val(row_cells, 'employee_id', 'employee_code', 'emp_id', 'id')
                raw_name = get_val(row_cells, 'employee_name', 'name', 'emp_name')

                if not raw_code and not raw_name:
                    continue

                emp_code = raw_code.strip() if raw_code else f"OHCE{row_num:04d}"

                # 1. Company, Department & Designation
                raw_comp = get_val(row_cells, 'company_name', 'company')
                comp_name = raw_comp.strip() if raw_comp else default_company_name
                company = m.Company.objects.filter(name__iexact=comp_name).first()
                if not company:
                    company, _ = m.Company.objects.get_or_create(name=comp_name)

                dept_name = get_val(row_cells, 'department', 'dept') or 'General'
                department, _ = m.Department.objects.get_or_create(company=company, name=dept_name.strip())

                desig_title = get_val(row_cells, 'designation', 'role', 'title') or 'Executive'
                designation, _ = m.Designation.objects.get_or_create(
                    company=company, department=department, title=desig_title.strip()
                )

                # 2. Name & Email
                raw_name = raw_name.strip()
                name_parts = (raw_name or f"Employee {emp_code}").split(' ', 1)
                first_name = name_parts[0].strip()
                last_name = name_parts[1].strip() if len(name_parts) > 1 else ''

                email_val = get_val(row_cells, 'email', 'emails', 'email_id')
                if email_val and '@' in email_val and not email_val.startswith('-'):
                    email = email_val.lower().strip()
                else:
                    # Safe unique fallback for employees with blank emails
                    email = f"{emp_code.lower()}@oblu.com"

                # 3. Match Existing User (if any)
                clean_full = f"{first_name}_{last_name}".strip('_')
                existing_user = User.objects.filter(
                    Q(username__iexact=clean_full)
                    | Q(username__iexact=first_name)
                    | Q(username__icontains=f"{first_name.lower()}_oblu")
                    | Q(username__iexact=emp_code)
                    | Q(first_name__iexact=first_name, last_name__iexact=last_name)
                    | (Q(email__iexact=email) if email else Q(pk=None))
                ).exclude(is_superuser=True).first()

                if existing_user:
                    # Guard against OneToOneField unique constraint: ensure user isn't already assigned to another employee
                    already_taken = m.Employee.objects.filter(user=existing_user).exclude(employee_code=emp_code).exists()
                    if already_taken:
                        existing_user = None

                if existing_user:
                    linked_users_list.append(
                        f"  [LINKED]   {first_name} {last_name} ({emp_code}) -> User: '{existing_user.username}'")
                else:
                    unlinked_users_list.append(
                        f"  [NO USER]  {first_name} {last_name} ({emp_code}) -> Needs manual username/password")

                # 4. Dates Parsing (DOB, DOJ, DOC)
                dob = self.parse_date_value(get_val(row_cells, 'dob', 'date_of_birth'))
                doj = self.parse_date_value(get_val(row_cells, 'doj', 'date_of_joining')) or date(2023, 1, 1)
                doc = self.parse_date_value(get_val(row_cells, 'doc', 'date_of_confirmation'))

                raw_gender = get_val(row_cells, 'gender', 'sex').upper().strip()
                gender = m.Employee.Gender.FEMALE if raw_gender.startswith('F') else (
                    m.Employee.Gender.MALE if raw_gender.startswith('M') else m.Employee.Gender.OTHER
                )

                # 5. Employment Status & Type Resolution
                raw_status = get_val(row_cells, 'employement_status', 'employment_status', 'status').lower().strip()

                if any(k in raw_status for k in ('left', 'resigned', 'relieved', 'inactive', 'ex-employee')):
                    emp_status = getattr(m.Employee.Status, 'INACTIVE', 'inactive')
                    status_breakdown['inactive'] += 1
                elif any(k in raw_status for k in ('terminate', 'terminated', 'fired')):
                    emp_status = getattr(m.Employee.Status, 'TERMINATED', 'terminated')
                    status_breakdown['terminated'] += 1
                elif any(k in raw_status for k in ('probation', 'trainee', 'intern')):
                    emp_status = getattr(m.Employee.Status, 'PROBATION', m.Employee.Status.ACTIVE)
                    status_breakdown['probation'] += 1
                else:
                    # 'Confirmed' or default active
                    emp_status = m.Employee.Status.ACTIVE
                    status_breakdown['active'] += 1

                # Employment Type
                emp_type = 'full_time'
                if 'intern' in raw_status or 'intern' in desig_title.lower():
                    emp_type = 'intern'
                elif 'trainee' in raw_status or 'trainee' in desig_title.lower():
                    emp_type = 'trainee'
                elif 'probation' in raw_status:
                    emp_type = 'probation'

                raw_is_mgr = get_val(row_cells, 'is_manager')
                is_manager = raw_is_mgr.lower() in ('1', '1.0', 'true', 'yes', 'y')

                phone_personal = self.clean_phone(get_val(row_cells, 'personal_phone_no', 'personal_phone_no.', 'personal_phone', 'mobile'))
                phone_comp = self.clean_phone(get_val(row_cells, 'company_phone_no', 'company_phone_no.', 'work_phone'))
                address = get_val(row_cells, 'address')
                father_name = get_val(row_cells, 'father_name', 'fathers_name')
                emergency_contact = self.clean_phone(get_val(row_cells, 'emergency_contact'))

                emp_defaults = {
                    'company': company,
                    'department': department,
                    'designation': designation,
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': email,
                    'phone': phone_personal or phone_comp,
                    'gender': gender,
                    'date_of_birth': dob,
                    'date_of_joining': doj,
                    'date_of_confirmation': doc,
                    'status': emp_status,
                    'employment_type': emp_type,
                    'is_manager': is_manager,
                    'address': address,
                    'user': existing_user,
                    'leave_policy_mode': 'annual',  # Default to Annual-wise leave policy
                }

                if hasattr(m.Employee, 'father_name') and father_name:
                    emp_defaults['father_name'] = father_name
                if hasattr(m.Employee, 'emergency_contact') and emergency_contact:
                    emp_defaults['emergency_contact'] = emergency_contact

                emp_obj, created = m.Employee.objects.update_or_create(
                    employee_code=emp_code,
                    defaults=emp_defaults
                )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

                # 6. Salary Structure (e.g. 17,000.00 / 15,100)
                raw_salary = get_val(row_cells, 'salary', 'ctc')
                if raw_salary:
                    monthly_sal = self.parse_decimal(raw_salary)
                    if monthly_sal > 0:
                        annual_ctc = monthly_sal * Decimal('12')
                        salary_struct, _ = m.SalaryStructure.objects.get_or_create(
                            company=company, name="Standard Structure"
                        )
                        m.EmployeeSalary.objects.update_or_create(
                            employee=emp_obj,
                            is_active=True,
                            defaults={
                                'structure': salary_struct,
                                'ctc_annual': annual_ctc,
                                'basic': (monthly_sal * Decimal('0.5')).quantize(Decimal('0.01')),
                                'hra': (monthly_sal * Decimal('0.3')).quantize(Decimal('0.01')),
                                'special_allowance': (monthly_sal * Decimal('0.2')).quantize(Decimal('0.01')),
                                'effective_from': doj,
                            }
                        )

                # 7. Reporting Manager (Collected for pass 2)
                raw_mgr = get_val(row_cells, 'reporting_manager', 'reporting_manager:')
                if raw_mgr and raw_mgr not in ('-', '--', 'nan', 'None', 'N/A', 'n/a'):
                    manager_links_to_resolve.append((emp_code, raw_mgr.strip()))

            # Pass 2: Link Reporting Managers (Matches by Employee Code or Full Name)
            for emp_code, mgr_str in manager_links_to_resolve:
                target_emp = m.Employee.objects.filter(employee_code=emp_code).first()
                if not target_emp:
                    continue

                # 1. Search by employee code
                mgr_obj = m.Employee.objects.filter(employee_code__iexact=mgr_str).first()

                # 2. Search by full name (first_name + last_name)
                if not mgr_obj and ' ' in mgr_str:
                    m_parts = mgr_str.split(' ', 1)
                    mgr_obj = m.Employee.objects.filter(
                        first_name__iexact=m_parts[0].strip(),
                        last_name__iexact=m_parts[1].strip()
                    ).first()

                # 3. Search by first name
                if not mgr_obj:
                    mgr_obj = m.Employee.objects.filter(
                        first_name__iexact=mgr_str.split()[0].strip()
                    ).first()

                if mgr_obj and mgr_obj != target_emp:
                    target_emp.reporting_manager = mgr_obj
                    mgr_obj.is_manager = True
                    mgr_obj.save(update_fields=['is_manager'])
                    target_emp.save(update_fields=['reporting_manager'])

        # Output Summary
        self.stdout.write(self.style.SUCCESS(
            f"\n================ EMPLOYEES IMPORTED ================\n"
            f"Total Processed: {created_count + updated_count} (Created: {created_count}, Updated: {updated_count})\n"
            f"Active: {status_breakdown['active']} | Left/Inactive: {status_breakdown['inactive']} | "
            f"Terminated: {status_breakdown['terminated']} | Probation: {status_breakdown['probation']}"
        ))

        self.stdout.write(self.style.NOTICE(f"\n--- LINKED TO EXISTING INVENTORY USERS ({len(linked_users_list)}) ---"))
        for entry in linked_users_list:
            self.stdout.write(self.style.SUCCESS(entry))

        self.stdout.write(
            self.style.NOTICE(f"\n--- UNLINKED EMPLOYEES (NO ACCOUNT CREATED) ({len(unlinked_users_list)}) ---"))
        for entry in unlinked_users_list:
            self.stdout.write(self.style.WARNING(entry))