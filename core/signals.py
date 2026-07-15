"""Safe transitional data maintenance for the membership rollout."""

from django.db import connections
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .apps import CoreConfig
from .models import Membership
from .tenancy import sync_legacy_memberships


@receiver(post_migrate, sender=CoreConfig)
def backfill_legacy_memberships(sender, app_config, using, **kwargs):
    """Create one membership for every existing legacy user workspace link."""
    # A targeted migration to an older core state may not have created this
    # table yet.  In that case the next normal migrate run will perform the
    # idempotent backfill once the table exists.
    table_names = connections[using].introspection.table_names()
    if Membership._meta.db_table not in table_names:
        return
    sync_legacy_memberships(using=using)
