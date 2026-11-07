import pandas as pd
from django.core.management.base import BaseCommand
from hrms import models as m
from datetime import date


class Command(BaseCommand):
    help = 'Import Employee Salary using Gross Wages from Excel'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the excel file')

    def handle(self, *args, **options):
        file_path = options['file_path']
        june_first = date(2024, 6, 1)

        try:
            # 1. Read the excel file
            df = pd.read_excel(file_path)

            # 2. IMPORTANT: Clean the column names (removes hidden spaces)
            df.columns = [str(col).strip() for col in df.columns]

            # Debug: show which columns were found
            self.stdout.write(self.style.NOTICE(f"Detected columns: {list(df.columns)}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error reading file: {e}"))
            return

        count_salary = 0

        for index, row in df.iterrows():
            # Use the exact column names from your screenshot
            emp_id_raw = row.get('Employee ID')
            gross_wage = row.get('Gross Wages')

            # Skip if ID is empty
            if pd.isna(emp_id_raw):
                self.stdout.write(self.style.WARNING(f"Row {index}: Missing Employee ID. Skipping..."))
                continue

            # Convert ID to string (handles IDs like '50' or '0048' correctly)
            emp_id = str(emp_id_raw).strip()

            try:
                # 1. Find the employee
                employee = m.Employee.objects.get(employee_code=emp_id)

                # 2. Update/Create Salary Structure
                if pd.notna(gross_wage) and float(gross_wage) > 0:
                    monthly_gross = float(gross_wage)
                    annual_ctc = monthly_gross * 12

                    salary_record, created = m.EmployeeSalary.objects.update_or_create(
                        employee=employee,
                        is_active=True,
                        defaults={
                            'ctc_annual': annual_ctc,
                            'basic': monthly_gross,  # Entering 'Gross Wages' as the base salary
                            'effective_from': june_first,
                        }
                    )

                    status = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f"Row {index}: {status} {emp_id} - {monthly_gross}"))
                    count_salary += 1
                else:
                    self.stdout.write(
                        self.style.WARNING(f"Row {index}: {emp_id} has 0 or NaN Gross Wages. Skipping..."))

            except m.Employee.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Row {index}: Employee {emp_id} not found in DB."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Row {index}: Error processing {emp_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nFinished! Processed {count_salary} salary records."))