import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Value, Q
from django.db.models.functions import Concat
from hrms.models import Employee, EmployeeLeaveBalance


class Command(BaseCommand):
    help = 'Imports leave data from Excel by matching names with first_name and last_name'

    def add_arguments(self, parser):
        parser.add_argument('excel_path', type=str, help='Path to the Excel file')

    def clean_val(self, val):
        """Converts 'Not Assigned' or NaN to 0.0"""
        if pd.isna(val) or str(val).strip().lower() == "not assigned":
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def handle(self, *args, **options):
        file_path = options['excel_path']

        try:
            # Load Excel
            df = pd.read_excel(file_path)

            created_count = 0
            updated_count = 0
            error_count = 0

            self.stdout.write(self.style.SUCCESS(f"--- Starting Import from {file_path} ---"))

            for index, row in df.iterrows():
                name_in_excel = str(row.get('Name', '')).strip()
                if not name_in_excel or name_in_excel == 'nan':
                    continue

                # 1. FUZZY NAME MATCHING
                # We combine first_name and last_name in the DB to match the Excel string
                employee = Employee.objects.annotate(
                    full_name_db=Concat('first_name', Value(' '), 'last_name')
                ).filter(
                    Q(full_name_db__iexact=name_in_excel) |
                    Q(first_name__iexact=name_in_excel)
                ).first()

                if not employee:
                    self.stdout.write(self.style.WARNING(
                        f"Row {index + 2}: Employee '{name_in_excel}' NOT FOUND in Employee table. Skipping."))
                    error_count += 1
                    continue

                try:
                    # 2. UPDATE OR CREATE LEAVE BALANCE
                    # Maps Excel columns to your EmployeeLeaveBalance fields
                    obj, created = EmployeeLeaveBalance.objects.update_or_create(
                        e_name=employee,
                        defaults={
                            'status': str(row.get('Status', 'Active')),
                            'bereavement_leave': self.clean_val(row.get('Bereavement Leave')),
                            'menstrual_leave': self.clean_val(row.get('Menstrual Leave')),
                            'sick_leave': self.clean_val(row.get('Sick Leaves')),
                            'earned_leave': self.clean_val(row.get('Earned Leaves')),
                            'casual_leave': self.clean_val(row.get('Casual Leaves')),
                            'comp_off': self.clean_val(row.get('Comp Off')),
                        }
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"Row {index + 2}: Error saving data for {name_in_excel}: {str(e)}"))
                    error_count += 1

            # Final Summary
            self.stdout.write(self.style.SUCCESS(
                f"\nIMPORT COMPLETE\n"
                f"----------------\n"
                f"Successfully Created: {created_count}\n"
                f"Successfully Updated: {updated_count}\n"
                f"Errors/Missing Employees: {error_count}\n"
            ))

        except FileNotFoundError:
            raise CommandError(f"File not found at: {file_path}")
        except Exception as e:
            raise CommandError(f"Critical error: {str(e)}")