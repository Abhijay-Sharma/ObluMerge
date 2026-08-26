import json
import os
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from request_logs.models import RequestLog
from django.db import connection


class Command(BaseCommand):
    def handle(self, *args, **options):
        # 1. Where to save the history?
        # This goes one folder ABOVE your project so it stays clean.
        archive_file = os.path.join(settings.BASE_DIR, '..', 'logs_history.jsonl')

        # 2. Which logs are old? (Older than 7 days)
        week_ago = timezone.now() - timedelta(days=7)
        old_logs = RequestLog.objects.filter(created_at__lt=week_ago)

        if not old_logs.exists():
            self.stdout.write("Nothing to clean. The database is already lite!")
            return

        # 3. Write them into the Logbook (JSONL file)
        with open(archive_file, 'a', encoding='utf-8') as f:
            for log in old_logs:
                data = {
                    "time": str(log.created_at),
                    "user": str(log.user),
                    "path": log.path,
                    "status": log.status_code,
                }
                # Save as one line in the text file
                f.write(json.dumps(data) + "\n")

        # 4. Throw away the old receipts from the Database
        count = old_logs.count()
        old_logs.delete()

        # 5. THE MOST IMPORTANT PART: Shrink the Database file
        # In SQLite, deleting data doesn't make the file smaller.
        # VACUUM is the command that physically shrinks the file on your disk.
        with connection.cursor() as cursor:
            cursor.execute("VACUUM")

        self.stdout.write(f"Done! Moved {count} logs to history and made the DB lite.")