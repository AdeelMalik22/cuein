from django.core.management.base import BaseCommand

from core.tenancy import sync_legacy_memberships


class Command(BaseCommand):
    help = 'Create Membership records from existing User.business and User.role values.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default', help='Database alias to backfill.')

    def handle(self, *args, **options):
        created = sync_legacy_memberships(using=options['database'])
        self.stdout.write(self.style.SUCCESS(f'Membership backfill complete: {created} created.'))
