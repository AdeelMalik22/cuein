"""Validation helpers shared by core models and serializers."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError


def validate_iana_timezone(value: str) -> None:
    """Require a timezone name that Python's IANA timezone database knows."""
    try:
        ZoneInfo(value)
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise ValidationError(
            '%(value)s is not a valid IANA time zone.',
            code='invalid_timezone',
            params={'value': value},
        ) from error
