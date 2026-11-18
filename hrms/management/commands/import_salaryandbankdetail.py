import re
from datetime import date
from decimal import Decimal, InvalidOperation
import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from hrms import models as m


class Command(BaseCommand):
    help = 'Import Employee Salary and Bank Details from Excel'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the Excel file')
        parser.add_argument(
            '--sheet',
            type=str,
            default=None,
            help='Specific sheet name to read (defaults to auto-detecting sheet with salary columns)'
        )

    def clean_decimal(self, val):
        """Converts strings with currency symbols/commas to a clean Decimal."""
        if pd.isna(val) or val is None or str(val).strip() in ('', '-', '--', 'nan', 'None'):
            return Decimal('0.00')
        clean_str = re.sub(r'[^\d.]', '', str(val).strip())
        try:
            return Decimal(clean_str).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            return Decimal('0.00')

    def clean_str(self, val):
        """Trims whitespace and strips floating point formatting like '.0' from Excel account numbers."""
        if pd.isna(val) or val is None or str(val).strip() in ('', '-', '--', 'nan', 'None', 'NaN'):
            return ''
        s = str(val).strip()
        if s.endswith('.0'):
            s = s[:-2]
        return s

    def find_employee(self, emp_id_raw, emp_name_raw):
        """
        Flexibly looks up an employee by code (OHCE0003, 3, etc.) or full/first name.
        """
        emp_id = self.clean_str(emp_id_raw)
        emp_name = self.clean_str(emp_name_raw)

        # 1. Direct match on code
        if emp_id:
            emp = m.Employee.objects.filter(employee_code__iexact=emp_id).first()
            if emp:
                return emp

            # Handle numeric variations (e.g., '3' -> 'OHCE0003')
            digits_only = re.sub(r'\D', '', emp_id)
            if digits_only:
                padded_code = f"OHCE{int(digits_only):04d}"
                emp = m.Employee.objects.filter(employee_code__iexact=padded_code).first()
                if emp:
                    return emp

        # 2. Fallback: Match by name if code didn't match
        if emp_name:
            name_parts = emp_name.split()
            first = name_parts[0]
            last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            if last:
                emp = m.Employee.objects.filter(
                    first_name__iexact=first,
                    last_name__iexact=last
                ).first()
                if emp:
                    return emp

            emp = m.Employee.objects.filter(
                Q(first_name__iexact=emp_name) |
                Q(first_name__icontains=first)
            ).first()
            if emp:
                return emp

        return None

    def handle(self, *args, **options):
        file_path = options['file_path']
        sheet_param = options['sheet']
        effective_date = date.today()

        self.stdout.write(self.style.NOTICE(f"Loading Excel file: {file_path}"))

        try:
            excel_file = pd.ExcelFile(file_path)
            sheet_names = excel_file.sheet_names
            self.stdout.write(f"Found sheets: {sheet_names}")

            # Pick target sheet
            target_sheet = sheet_names[0]
            if sheet_param and sheet_param in sheet_names:
                target_sheet = sheet_param
            else:
                for name in sheet_names:
                    preview_df = pd.read_excel(excel_file, sheet_name=name, nrows=2)
                    cols = [str(c).lower() for c in preview_df.columns]
                    if any('salary' in c or 'bank' in c for c in cols):
                        target_sheet = name
                        break

            self.stdout.write(self.style.NOTICE(f"Reading sheet: '{target_sheet}'..."))
            df = pd.read_excel(excel_file, sheet_name=target_sheet)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error reading Excel file: {e}"))
            return

        # Normalize column mapping
        col_map = {}
        for col in df.columns:
            norm = str(col).lower().replace(' ', '_').replace('.', '').replace(':', '').strip()
            col_map[norm] = col

        def get_row_val(row, *aliases):
            for a in aliases:
                norm_a = a.lower().replace(' ', '_').replace('.', '').replace(':', '').strip()
                if norm_a in col_map:
                    val = row.get(col_map[norm_a])
                    if pd.notna(val):
                        return val
            return None

        count_salary_updated = 0
        count_bank_updated = 0
        count_skipped = 0

        self.stdout.write(self.style.NOTICE(f"Processing {len(df)} rows..."))

        with transaction.atomic():
            for index, row in df.iterrows():
                emp_id_raw = get_row_val(row, 'employee_id', 'employee_code', 'emp_id', 'id', 'code')
                emp_name_raw = get_row_val(row, 'employee_name', 'name', 'emp_name')
                salary_raw = get_row_val(row, 'salary', 'gross_wages', 'gross_salary', 'ctc')

                bank_name = self.clean_str(get_row_val(row, 'bank_name', 'bank'))
                ifsc_code = self.clean_str(get_row_val(row, 'ifsc_code', 'ifsc'))
                account_no = self.clean_str(get_row_val(row, 'bank_account_no', 'bank_account_no.', 'account_no', 'account_number'))
                branch_name = self.clean_str(get_row_val(row, 'bank_branch_name', 'branch_name', 'branch'))
                account_type = self.clean_str(get_row_val(row, 'account', 'account_type')) or 'saving'

                employee = self.find_employee(emp_id_raw, emp_name_raw)

                if not employee:
                    self.stdout.write(self.style.WARNING(
                        f"Row {index + 2}: Employee '{emp_id_raw or emp_name_raw}' not found in database. Skipping..."
                    ))
                    count_skipped += 1
                    continue

                # 1. Update/Create Salary
                salary_amount = self.clean_decimal(salary_raw)
                salary_status_str = "No salary provided"
                if salary_amount > 0:
                    annual_ctc = salary_amount * 12
                    salary_record, created = m.EmployeeSalary.objects.update_or_create(
                        employee=employee,
                        is_active=True,
                        defaults={
                            'ctc_annual': annual_ctc,
                            'basic': salary_amount,
                            'effective_from': effective_date,
                        }
                    )
                    count_salary_updated += 1
                    salary_status_str = f"Salary: ₹{salary_amount:,.2f}"

                # 2. Update/Create Bank Details
                bank_status_str = "No bank details provided"
                if account_no or bank_name:
                    holder_name = employee.full_name or f"{employee.first_name} {employee.last_name}".strip()
                    bank_record, created = m.EmployeeBankDetail.objects.update_or_create(
                        employee=employee,
                        defaults={
                            'account_holder': holder_name,
                            'account_number': account_no,
                            'ifsc_code': ifsc_code,
                            'bank_name': bank_name,
                            'branch_name': branch_name,
                            'account_type': account_type.lower(),
                        }
                    )
                    count_bank_updated += 1
                    bank_status_str = f"Bank: {bank_name} (A/C: {account_no})"

                self.stdout.write(self.style.SUCCESS(
                    f"Row {index + 2}: [OK] {employee.employee_code} ({employee.full_name}) -> {salary_status_str} | {bank_status_str}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"\nFinished Import:\n"
            f"  - Salaries Updated: {count_salary_updated}\n"
            f"  - Bank Details Updated: {count_bank_updated}\n"
            f"  - Skipped: {count_skipped}"
        ))