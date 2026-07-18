"""Small public probes for process liveness and dependency readiness."""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


def _database_is_available() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        # A readiness response must not expose connection details or turn an
        # expected dependency outage into an application error page.
        return False
    return True


def _cache_is_available() -> bool:
    try:
        # A read is enough to establish a Redis/Valkey connection without
        # adding health-check keys to the shared cache.
        cache.get('cuein:healthcheck')
    except Exception:
        return False
    return True


@require_GET
def healthz(request):
    """Return success when Django can serve requests, without dependencies."""
    return JsonResponse({'status': 'ok'})


@require_GET
def readyz(request):
    """Return success only when the database and shared cache are reachable."""
    checks = {
        'database': 'ok' if _database_is_available() else 'unavailable',
        'cache': 'ok' if _cache_is_available() else 'unavailable',
    }
    ready = all(status == 'ok' for status in checks.values())
    return JsonResponse(
        {'status': 'ok' if ready else 'unavailable', 'checks': checks},
        status=200 if ready else 503,
    )
