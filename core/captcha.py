"""Optional Cloudflare Turnstile verification for suspicious login attempts."""

import json
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)


def captcha_enabled() -> bool:
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def verify_turnstile(token: str, remote_ip: str) -> bool:
    """Verify a Turnstile response server-side; failures are not trusted."""
    if not captcha_enabled():
        return True
    if not token:
        return False

    body = urlencode({
        'secret': settings.TURNSTILE_SECRET_KEY,
        'response': token,
        'remoteip': remote_ip,
    }).encode()
    request = Request(
        settings.TURNSTILE_VERIFY_URL,
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urlopen(request, timeout=settings.TURNSTILE_TIMEOUT) as response:
            result = json.load(response)
    except Exception:
        logger.warning('Turnstile verification could not be completed.', exc_info=True)
        return False
    return bool(result.get('success'))
