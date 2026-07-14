"""Expiring six-digit verification codes for pending workspace registrations."""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Business, PendingRegistration, User


VERIFICATION_CODE_LENGTH = 6
MAX_VERIFICATION_ATTEMPTS = 5
logger = logging.getLogger(__name__)


class EmailVerificationError(ValueError):
    """A verification code is invalid, expired, or can no longer be completed."""


class EmailVerificationDeliveryError(RuntimeError):
    """The verification message could not be handed to the configured backend."""


def _new_verification_code() -> str:
    return f'{secrets.randbelow(10 ** VERIFICATION_CODE_LENGTH):0{VERIFICATION_CODE_LENGTH}d}'


def _verification_expired(registration: PendingRegistration) -> bool:
    if not registration.verification_sent_at:
        return True
    expires_at = registration.verification_sent_at + timedelta(seconds=settings.EMAIL_VERIFICATION_TIMEOUT)
    return timezone.now() >= expires_at


def send_email_verification(registration: PendingRegistration) -> None:
    """Issue a fresh code. Only the hash is persisted, and only after send succeeds."""
    code = _new_verification_code()
    try:
        with transaction.atomic():
            registration = PendingRegistration.objects.select_for_update().get(pk=registration.pk)
            registration.verification_code_hash = make_password(code)
            registration.verification_attempts = 0
            registration.verification_sent_at = timezone.now()
            registration.save(
                update_fields=('verification_code_hash', 'verification_attempts', 'verification_sent_at'),
            )

            context = {
                'registration': registration,
                'verification_code': code,
                'verification_timeout_hours': max(1, settings.EMAIL_VERIFICATION_TIMEOUT // 3600),
            }
            message = EmailMultiAlternatives(
                subject='Your Cuein verification code',
                body=render_to_string('web/emails/verify_email.txt', context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[registration.email],
            )
            message.attach_alternative(render_to_string('web/emails/verify_email.html', context), 'text/html')
            sent_count = message.send(fail_silently=False)
            if sent_count != 1:
                raise EmailVerificationDeliveryError('Unable to send the verification email.')
    except EmailVerificationDeliveryError:
        raise
    except PendingRegistration.DoesNotExist as error:
        raise EmailVerificationError('This registration is no longer available.') from error
    except Exception as error:
        logger.exception('Verification code delivery failed.')
        raise EmailVerificationDeliveryError('Unable to send the verification email.') from error


def activate_pending_registration(registration: PendingRegistration, code: str) -> User:
    """Create the business and active owner exactly once after a correct code."""
    failure_message = None
    user = None
    try:
        with transaction.atomic():
            registration = PendingRegistration.objects.select_for_update().get(pk=registration.pk)
            if _verification_expired(registration):
                failure_message = 'That verification code has expired. Request a new code and try again.'
            elif registration.verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
                failure_message = 'Too many incorrect attempts. Request a new verification code.'
            elif not registration.verification_code_hash or not check_password(code, registration.verification_code_hash):
                registration.verification_attempts += 1
                registration.save(update_fields=('verification_attempts',))
                remaining_attempts = MAX_VERIFICATION_ATTEMPTS - registration.verification_attempts
                if remaining_attempts <= 0:
                    failure_message = 'Too many incorrect attempts. Request a new verification code.'
                else:
                    failure_message = 'That verification code is incorrect.'
            elif (
                User.objects.filter(email__iexact=registration.email).exists()
                or User.objects.filter(username=registration.username).exists()
            ):
                failure_message = 'This registration can no longer be completed.'
            else:
                business = Business.objects.create(
                    name=registration.business_name,
                    industry=registration.industry,
                    timezone=registration.timezone,
                )
                user = User(
                    username=registration.username,
                    first_name=registration.first_name,
                    last_name=registration.last_name,
                    email=registration.email,
                    phone=registration.phone,
                    password=registration.password,
                    business=business,
                    role=User.Role.OWNER,
                    is_active=True,
                    email_verified_at=timezone.now(),
                    email_verification_sent_at=registration.verification_sent_at,
                )
                user.save()
                registration.delete()
        if failure_message:
            raise EmailVerificationError(failure_message)
        return user
    except PendingRegistration.DoesNotExist as error:
        raise EmailVerificationError('This registration is no longer available.') from error
    except IntegrityError as error:
        raise EmailVerificationError('This registration can no longer be completed.') from error
