from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.signing import TimestampSigner
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import EmailCampaign


@shared_task
def send_email_campaign(campaign_id):
    """Send one queued campaign and persist its final delivery status."""
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.status not in (EmailCampaign.Status.QUEUED, EmailCampaign.Status.FAILED):
            return {'status': campaign.status, 'sent': 0}
        campaign.status = EmailCampaign.Status.SENDING
        campaign.error_message = ''
        campaign.save(update_fields=('status', 'error_message'))

    signer = TimestampSigner()
    site_url = getattr(settings, 'DJANGO_SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    messages = []
    for email in campaign.recipient_emails:
        token = signer.sign_object({
            'business_id': str(campaign.business_id),
            'email': email,
        })
        unsubscribe_url = f'{site_url}{reverse("web:email-unsubscribe")}?{urlencode({"token": token})}'
        body = (
            f'{campaign.body.rstrip()}\n\n'
            f'---\nTo stop receiving marketing emails, unsubscribe here:\n{unsubscribe_url}'
        )
        messages.append(EmailMessage(
            subject=campaign.subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        ))

    try:
        with get_connection() as connection:
            sent_count = connection.send_messages(messages) if messages else 0
        status = EmailCampaign.Status.SENT if sent_count == len(messages) else EmailCampaign.Status.FAILED
        error_message = '' if status == EmailCampaign.Status.SENT else (
            f'Sent {sent_count} of {len(messages)} messages.'
        )
    except Exception as error:  # Celery task must persist failures for the UI.
        sent_count = 0
        status = EmailCampaign.Status.FAILED
        error_message = str(error)[:2000]

    EmailCampaign.objects.filter(pk=campaign_id).update(
        status=status,
        error_message=error_message,
        sent_at=timezone.now() if status == EmailCampaign.Status.SENT else None,
    )
    return {'status': status, 'sent': sent_count, 'total': len(messages)}
