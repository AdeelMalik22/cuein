"""Shared cache-backed safeguards for public browser authentication flows."""

import hashlib
from ipaddress import ip_address
import logging
import math
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)

_RATE_PERIODS = {
    'second': 1,
    'minute': 60,
    'hour': 60 * 60,
    'day': 24 * 60 * 60,
}


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


@dataclass(frozen=True)
class LoginAttemptState:
    failures: int
    retry_after: int
    captcha_required: bool


def client_ip(request) -> str:
    """Use a client address without trusting proxy headers by default."""
    remote_addr = request.META.get('REMOTE_ADDR', 'unknown')[:64]
    try:
        proxy_is_trusted = any(
            ip_address(remote_addr) in network
            for network in settings.TRUSTED_PROXY_NETWORKS
        )
    except ValueError:
        proxy_is_trusted = False
    if settings.TRUST_X_FORWARDED_FOR and proxy_is_trusted:
        forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded_for:
            return forwarded_for.split(',', 1)[0].strip()[:64]
    return remote_addr


def _hashed_identifier(identifier: str) -> str:
    return hashlib.sha256(str(identifier).strip().lower().encode()).hexdigest()


def _parse_rate(rate: str) -> tuple[int, int]:
    count, period = rate.split('/', 1)
    period = period.rstrip('s').lower()
    try:
        return int(count), _RATE_PERIODS[period]
    except (KeyError, ValueError) as error:
        raise ValueError(f'Invalid security rate: {rate!r}') from error


def consume_rate_limit(scope: str, identifier: str, rate: str) -> RateLimitResult:
    """Consume one fixed-window quota without putting personal data in cache keys."""
    limit, period = _parse_rate(rate)
    now = int(time.time())
    bucket = now // period
    key = f'cuein:rate-limit:{scope}:{bucket}:{_hashed_identifier(identifier)}'

    try:
        if cache.add(key, 1, timeout=period):
            count = 1
        else:
            count = cache.incr(key)
    except ValueError:
        # A key expiring between add() and incr() is harmless; start a new
        # window rather than turning a legitimate sign-in into a server error.
        try:
            count = 1 if cache.add(key, 1, timeout=period) else cache.incr(key)
        except Exception:
            logger.warning('Security rate limit cache was unavailable.', exc_info=True)
            return RateLimitResult(
                allowed=not settings.AUTH_CACHE_FAIL_CLOSED,
                retry_after=settings.AUTH_CACHE_FAILURE_RETRY_SECONDS,
            )
    except Exception:
        logger.warning('Security rate limit cache was unavailable.', exc_info=True)
        return RateLimitResult(
            allowed=not settings.AUTH_CACHE_FAIL_CLOSED,
            retry_after=settings.AUTH_CACHE_FAILURE_RETRY_SECONDS,
        )

    retry_after = max(1, period - (now % period))
    return RateLimitResult(allowed=count <= limit, retry_after=retry_after)


def consume_browser_auth_rate_limit(request, scope: str) -> RateLimitResult:
    return consume_rate_limit(
        scope,
        client_ip(request),
        settings.BROWSER_AUTH_THROTTLE_RATES[scope],
    )


def _login_cache_key(request, credential: str) -> str:
    # Pairing account identifier and IP avoids a permanent account-only lock
    # while still making repeated guesses from one source progressively slower.
    identifier = f'{credential.strip().lower()}:{client_ip(request)}'
    return f'cuein:login-backoff:{_hashed_identifier(identifier)}'


def _read_login_state(request, credential: str) -> tuple[int, float] | None:
    try:
        state = cache.get(_login_cache_key(request, credential), {})
    except Exception:
        logger.warning('Login backoff cache was unavailable.', exc_info=True)
        if settings.AUTH_CACHE_FAIL_CLOSED:
            return None
        return 0, 0
    return int(state.get('failures', 0)), float(state.get('blocked_until', 0))


def login_attempt_state(request, credential: str) -> LoginAttemptState:
    state = _read_login_state(request, credential)
    if state is None:
        return LoginAttemptState(
            failures=0,
            retry_after=settings.AUTH_CACHE_FAILURE_RETRY_SECONDS,
            captcha_required=False,
        )
    failures, blocked_until = state
    retry_after = max(0, math.ceil(blocked_until - time.time()))
    return LoginAttemptState(
        failures=failures,
        retry_after=retry_after,
        captcha_required=failures >= settings.LOGIN_CAPTCHA_FAILURE_THRESHOLD,
    )


def record_failed_login(request, credential: str) -> LoginAttemptState:
    """Record a failed login and apply a bounded, progressively longer pause."""
    state = _read_login_state(request, credential)
    if state is None:
        return LoginAttemptState(
            failures=0,
            retry_after=settings.AUTH_CACHE_FAILURE_RETRY_SECONDS,
            captcha_required=False,
        )
    failures, _blocked_until = state
    failures += 1
    threshold = settings.LOGIN_BACKOFF_FAILURE_THRESHOLD
    over_threshold = failures - threshold
    delay = 0
    if over_threshold >= 0:
        delay = min(
            settings.LOGIN_BACKOFF_BASE_SECONDS * (2 ** over_threshold),
            settings.LOGIN_BACKOFF_MAX_SECONDS,
        )

    blocked_until = time.time() + delay
    state = {'failures': failures, 'blocked_until': blocked_until}
    try:
        cache.set(_login_cache_key(request, credential), state, settings.LOGIN_FAILURE_WINDOW)
    except Exception:
        logger.warning('Login backoff cache was unavailable.', exc_info=True)

    return LoginAttemptState(
        failures=failures,
        retry_after=math.ceil(delay),
        captcha_required=failures >= settings.LOGIN_CAPTCHA_FAILURE_THRESHOLD,
    )


def clear_login_failures(request, credential: str) -> None:
    try:
        cache.delete(_login_cache_key(request, credential))
    except Exception:
        logger.warning('Login backoff cache was unavailable.', exc_info=True)
