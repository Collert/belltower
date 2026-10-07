import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from tasks.cleanup import run_cleanup


class Command(BaseCommand):
    help = 'Periodically delete old completed/expired tasks and lists that have been empty too long.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run the cleanup once and exit (for cron).')
        parser.add_argument(
            '--interval', type=int, default=settings.BELLTOWER['CLEANUP_INTERVAL_SECONDS'],
            help='Seconds between runs (default: BELLTOWER_CLEANUP_INTERVAL_SECONDS).',
        )

    def handle(self, *args, once, interval, **options):
        while True:
            close_old_connections()
            deleted = run_cleanup()
            if any(deleted.values()) or once:
                self.stdout.write(
                    'Cleanup: {completed_tasks} completed tasks, {expired_tasks} expired tasks, '
                    '{empty_lists} empty lists deleted.'.format(**deleted)
                )
            if once:
                return
            time.sleep(interval)
