"""
Management commands: backfill_compoff

Scans all historical AttendanceRecord rows where:
  - attendance_date is a Sunday OR a company holiday for the employee
  - status is 'present' or 'half_day'
  - No CompOffRecord already exists for (employee, worked_date)

Then calls comp_off_logic.credit_comp_off() to create the CompOffRecord and
update EmployeeLeaveBalance + EmployeeLeaveBalanceLive.

Usage:
    python manage.py backfill_compoff
    python manage.py backfill_compoff --dry-run
    python manage.py backfill_compoff --employee-id 12
    python manage.py backfill_compoff --from-date 2026-01-01
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hrms import models as m
from hrms.comp_off_logic import is_off_day, credit_comp_off


class Command(BaseCommand):
    help = 'Backfill CompOffRecord credits for all existing attendance on Sundays/holidays.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Simulate without saving any database changes.',
        )
        parser.add_argument(
            '--employee-id',
            type=int,
            default=None,
            help='Limit backfill to a single employee PK.',
        )
        parser.add_argument(
            '--from-date',
            type=str,
            default=None,
            help='Process records on or after this date (YYYY-MM-DD).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        emp_id  = options['employee_id']
        from_dt = options['from_date']

        if dry_run:
            self.stdout.write(self.style.WARNING('--- DRY RUN MODE — no changes will be saved ---'))

        # Build queryset
        qs = m.AttendanceRecord.objects.select_related(
            'employee', 'employee__holiday_calendar'
        ).filter(
            status__in=[
                m.AttendanceRecord.Status.PRESENT,
                m.AttendanceRecord.Status.HALF_DAY,
            ]
        ).order_by('attendance_date')

        if emp_id:
            qs = qs.filter(employee_id=emp_id)

        if from_dt:
            try:
                from datetime import date
                parts = from_dt.split('-')
                start = date(int(parts[0]), int(parts[1]), int(parts[2]))
                qs = qs.filter(attendance_date__gte=start)
            except (ValueError, IndexError):
                raise CommandError(f'Invalid --from-date format. Use YYYY-MM-DD. Got: {from_dt}')

        total_scanned  = 0
        total_credited = 0
        total_skipped  = 0  # already had a CompOffRecord

        for record in qs.iterator(chunk_size=500):
            total_scanned += 1
            employee = record.employee
            worked_date = record.attendance_date

            # Only off-days earn comp-off
            is_off, reason = is_off_day(employee, worked_date)
            if not is_off:
                continue

            # Skip if already credited
            if m.CompOffRecord.objects.filter(
                employee=employee, worked_date=worked_date
            ).exists():
                total_skipped += 1
                self.stdout.write(
                    f'  SKIP   {employee.employee_code} on {worked_date} — already exists'
                )
                continue

            # Credit
            self.stdout.write(
                f'  {"DRY  " if dry_run else "CREDIT"} {employee.employee_code} | '
                f'{employee.full_name} | {worked_date} ({reason})'
            )

            if not dry_run:
                # half-day attendance → 0.5 credits
                if record.status == m.AttendanceRecord.Status.HALF_DAY:
                    from decimal import Decimal
                    # Temporarily set credits_earned to 0.5 via override after creation
                    co_rec = credit_comp_off(employee, record)
                    if co_rec and co_rec.credits_earned != Decimal('0.5'):
                        # Update to 0.5 and adjust balances
                        diff = co_rec.credits_earned - Decimal('0.5')
                        co_rec.credits_earned = Decimal('0.5')
                        co_rec.save(update_fields=['credits_earned'])
                        # Refund the extra 0.5 from both balance tables
                        live = m.EmployeeLeaveBalanceLive.objects.filter(e_name=employee).first()
                        if live:
                            live.comp_off = max(0, float(live.comp_off or 0) - float(diff))
                            live.save(update_fields=['comp_off'])
                        bank = m.EmployeeLeaveBalance.objects.filter(e_name=employee).first()
                        if bank:
                            bank.comp_off = max(0, float(bank.comp_off or 0) - float(diff))
                            bank.save(update_fields=['comp_off'])
                else:
                    credit_comp_off(employee, record)

            total_credited += 1

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Scanned: {total_scanned} | '
            f'Credited: {total_credited} | '
            f'Already existed (skipped): {total_skipped}'
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing was written to the database.'))
