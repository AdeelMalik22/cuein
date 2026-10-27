"""Race-safe creation of pending registrations."""

from django.db import IntegrityError, transaction

from .models import PendingRegistration, User


class SignupConflictError(ValueError):
    """The requested signup identity was claimed by another request."""


def create_pending_registration(*, username, auto_username=False, **values):
    """Create a pending registration without leaking unique-constraint errors.

    Availability checks improve the normal user experience, but they are not
    enough when two signup requests arrive at the same time. The database is
    still the authority, so an automatically generated username is retried
    with the next suffix when its insert loses a race.
    """
    base_username = str(username).strip()
    max_attempts = 8 if auto_username else 1

    for attempt in range(max_attempts):
        if auto_username:
            suffix = '' if attempt == 0 else str(attempt + 1)
            candidate = f'{base_username[:150 - len(suffix)]}{suffix}'
        else:
            candidate = base_username

        try:
            with transaction.atomic():
                # Keep the friendly validation for requests that race with a
                # committed row; the unique constraints handle the remaining
                # interleavings safely.
                if User.objects.filter(username__iexact=candidate).exists() or PendingRegistration.objects.filter(
                    username__iexact=candidate,
                ).exists():
                    if auto_username:
                        continue
                    raise SignupConflictError

                return PendingRegistration.objects.create(username=candidate, **values)
        except IntegrityError:
            if auto_username and attempt < max_attempts - 1:
                continue
            raise SignupConflictError from None

    raise SignupConflictError
