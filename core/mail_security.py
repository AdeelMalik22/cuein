"""Small helpers for encrypting mailbox secrets at rest.

Mailbox access tokens and IMAP/SMTP passwords must be recoverable by the
worker, so password hashing is not appropriate here.  Fernet provides
authenticated encryption; the key is derived from the deployment secret and
may be rotated explicitly with ``MAIL_TOKEN_ENCRYPTION_KEY``.
"""

import base64
import hashlib
from functools import lru_cache

import jwt
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    configured_key = getattr(settings, 'MAIL_TOKEN_ENCRYPTION_KEY', '')
    source = configured_key or settings.SECRET_KEY
    digest = hashlib.sha256(source.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_mail_secret(value):
    """Return an encrypted string, preserving empty optional credentials."""
    if not value:
        return ''
    return _fernet().encrypt(str(value).encode('utf-8')).decode('ascii')


def decrypt_mail_secret(value):
    """Decrypt a stored secret and fail closed on a corrupt/old value."""
    if not value:
        return ''
    try:
        return _fernet().decrypt(value.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return ''


@lru_cache(maxsize=1)
def _google_pubsub_jwk_client():
    return jwt.PyJWKClient(
        getattr(settings, 'GOOGLE_PUBSUB_JWKS_URL', 'https://www.googleapis.com/oauth2/v3/certs'),
    )


def verify_google_pubsub_request(request):
    """Verify an authenticated Google Pub/Sub push request."""
    audience = getattr(settings, 'GMAIL_PUBSUB_AUDIENCE', '')
    if not audience:
        return False
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    if scheme.casefold() != 'bearer' or not token:
        return False
    try:
        signing_key = _google_pubsub_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=('RS256',),
            audience=audience,
            options={'require': ('exp', 'iat', 'iss', 'aud')},
            leeway=10,
        )
    except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError):
        return False
    if claims.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}:
        return False
    expected_service_account = getattr(settings, 'GMAIL_PUBSUB_SERVICE_ACCOUNT', '')
    return not expected_service_account or claims.get('email') == expected_service_account
