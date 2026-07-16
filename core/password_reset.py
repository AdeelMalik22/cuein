"""Expiring six-digit email codes used to reset an existing account password."""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import PasswordResetRequest, User


PASSWORD_RESET_CODE_LENGTH = 6
MAX_PASSWORD_RESET_ATTEMPTS = 5
logger = logging.getLogger(__name__)


class PasswordResetError(ValueError):
    """A reset code is invalid, expired, or can no longer be used."""


class PasswordResetDeliveryError(RuntimeError):
    """The reset message could not be handed to the configured backend."""


def _new_reset_code() -> str:
    return f'{secrets.randbelow(10 ** PASSWORD_RESET_CODE_LENGTH):0{PASSWORD_RESET_CODE_LENGTH}d}'


def _reset_code_expired(reset_request: PasswordResetRequest) -> bool:
    if not reset_request.sent_at:
        return True
    expires_at = reset_request.sent_at + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)
    return timezone.now() >= expires_at


def send_password_reset_code(user: User) -> None:
    """Issue a new code for an account, replacing any earlier code."""
    code = _new_reset_code()
    try:
        with transaction.atomic():
            # Lock the user too, so concurrent requests cannot create two
            # records for the OneToOne relation or race to issue a code.
            user = User.objects.select_for_update().get(pk=user.pk)
            reset_request, _ = PasswordResetRequest.objects.get_or_create(user=user)
            reset_request.code_hash = make_password(code)
            reset_request.attempts = 0
            reset_request.sent_at = timezone.now()
            reset_request.save(update_fields=('code_hash', 'attempts', 'sent_at'))

            context = {
                'user': user,
                'reset_code': code,
                'password_reset_timeout_minutes': max(1, settings.PASSWORD_RESET_TIMEOUT // 60),
            }
            message = EmailMultiAlternatives(
                subject='Your Cuein password reset code',
                body=render_to_string('web/emails/password_reset.txt', context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            message.attach_alternative(
                render_to_string('web/emails/password_reset.html', context),
                'text/html',
            )
            sent_count = message.send(fail_silently=False)
            if sent_count != 1:
                raise PasswordResetDeliveryError('Unable to send the password reset email.')
    except PasswordResetDeliveryError:
        raise
    except (User.DoesNotExist, PasswordResetRequest.DoesNotExist) as error:
        raise PasswordResetError('This account is no longer available.') from error
    except Exception as error:
        logger.exception('Password reset code delivery failed.')
        raise PasswordResetDeliveryError('Unable to send the password reset email.') from error


def reset_password(email: str, code: str, new_password: str) -> User:
    """Set a new password exactly once after a valid reset code is supplied."""
    failure_message = None
    user = None
    with transaction.atomic():
        reset_request = (
            PasswordResetRequest.objects.select_for_update()
            .select_related('user')
            .filter(user__email__iexact=email, user__is_active=True)
            .first()
        )
        if reset_request is None:
            failure_message = 'That email address or reset code is not valid.'
        elif _reset_code_expired(reset_request):
            failure_message = 'That reset code has expired. Request a new code and try again.'
        elif reset_request.attempts >= MAX_PASSWORD_RESET_ATTEMPTS:
            failure_message = 'Too many incorrect attempts. Request a new reset code.'
        elif not reset_request.code_hash or not check_password(code, reset_request.code_hash):
            reset_request.attempts += 1
            reset_request.save(update_fields=('attempts',))
            remaining_attempts = MAX_PASSWORD_RESET_ATTEMPTS - reset_request.attempts
            if remaining_attempts <= 0:
                failure_message = 'Too many incorrect attempts. Request a new reset code.'
            else:
                failure_message = 'That reset code is incorrect.'
        else:
            user = reset_request.user
            user.set_password(new_password)
            user.save(update_fields=('password',))
            reset_request.delete()

    if failure_message:
        raise PasswordResetError(failure_message)
    return user
