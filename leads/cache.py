"""Small, tenant-safe Redis cache helpers for lead list responses."""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from django.core.cache import cache
from redis.exceptions import RedisError


LEAD_API_CACHE_TTL = 60
_CACHE_ERRORS = (RedisError, OSError)
_CACHEABLE_QUERY_PARAMS = (
    'stage',
    'source',
    'assigned_user',
    'product',
    'search',
    'ordering',
    'page',
    'page_size',
    'limit',
    'offset',
)


def _version_key(business_id):
    return f'lead-api-version:{business_id}'


def lead_cache_version(business_id):
    """Return a per-business cache version, falling back cleanly without Redis."""
    key = _version_key(business_id)
    try:
        value = cache.get(key)
        if value is None:
            cache.add(key, 1, timeout=None)
            value = cache.get(key, 1)
        return int(value or 1)
    except _CACHE_ERRORS:
        return 1


def invalidate_business_lead_cache(business_id):
    """Invalidate every lead-list cache entry for one tenant in O(1)."""
    key = _version_key(business_id)
    try:
        value = cache.get(key)
        cache.set(key, int(value or 1) + 1, timeout=None)
    except _CACHE_ERRORS:
        # Redis is a performance layer. A temporary outage must never make a
        # lead write fail; entries also expire after a short TTL.
        return


def lead_api_cache_key(*, business_id, user, action, query_params):
    """Build a compact key with tenant, role scope, endpoint, and filters."""
    scope = str(user.id) if user.role == user.Role.SALESPERSON else 'business'
    query_items = []
    for name in _CACHEABLE_QUERY_PARAMS:
        for value in query_params.getlist(name):
            query_items.append((name, value))
    query = urlencode(sorted(query_items))
    version = lead_cache_version(business_id)
    digest = hashlib.sha256(
        f'{business_id}:{scope}:{action}:{version}:{query}'.encode(),
    ).hexdigest()
    return f'lead-api:{digest}'


def get_cached_lead_response(key):
    try:
        return cache.get(key)
    except _CACHE_ERRORS:
        return None


def cache_lead_response(key, payload):
    try:
        cache.set(key, payload, timeout=LEAD_API_CACHE_TTL)
    except _CACHE_ERRORS:
        return
