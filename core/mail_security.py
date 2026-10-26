"""Small helpers for encrypting mailbox secrets at rest.

Mailbox access tokens and IMAP/SMTP passwords must be recoverable by the
worker, so password hashing is not appropriate here.  Fernet provides
authenticated encryption; the key is derived from the deployment secret and
may be rotated explicitly with ``MAIL_TOKEN_ENCRYPTION_KEY``.
"""

import base64
import hashlib

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
