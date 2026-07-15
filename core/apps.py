from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Register the idempotent legacy-membership backfill after Django has
        # loaded all models.  The import is intentionally local to avoid app
        # loading side effects.
        from . import signals  # noqa: F401
