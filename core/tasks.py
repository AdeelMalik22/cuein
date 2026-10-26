from html import escape
from urllib.parse import urlencode

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.signing import TimestampSigner
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import EmailCampaign, EmailCampaignRecipient


def _personalize(value, customer_name):
    """Support the small, safe set of merge tags offered by campaign emails."""
    return (
        (value or '')
        .replace('{{ customer_name }}', customer_name)
        .replace('{{customer_name}}', customer_name)
        .replace('{{ name }}', customer_name)
        .replace('{{name}}', customer_name)
    )


def _plain_text_as_html(body):
    paragraphs = []
    for paragraph in escape(body or '').split('\n\n'):
        paragraphs.append(
            '<p style="color:#262622;font-size:16px;line-height:1.65;margin:0 0 18px;">'
            f'{paragraph.replace(chr(10), "<br>")}</p>'
        )
    return ''.join(paragraphs)


def _message_for_recipient(
    *,
    subject,
    body,
    html_body,
    preheader,
    email,
    customer_name='',
    reply_to='',
    unsubscribe_url='',
    is_test=False,
):
    personalized_subject = _personalize(subject, customer_name)
    personalized_body = _personalize(body, customer_name)
    personalized_html = _personalize(html_body, customer_name)
    content_body = personalized_body

    if unsubscribe_url:
        personalized_body = (
            f'{personalized_body.rstrip()}\n\n---\n'
            f'To stop receiving marketing emails, unsubscribe here:\n{unsubscribe_url}'
        )
        footer = (
            '<p style="border-top:1px solid #dfdfdc;color:#70706c;font-size:13px;'
            'line-height:1.5;margin:28px 0 0;padding-top:18px;">'
            f'To stop receiving marketing emails, <a href="{escape(unsubscribe_url)}">unsubscribe here</a>.'
            '</p>'
        )
    elif is_test:
        personalized_body = f'This is a preview email from Cuein.\n\n{personalized_body}'
        footer = '<p style="color:#70706c;font-size:13px;">This is a test message.</p>'
    else:
        footer = ''

    content_html = personalized_html or _plain_text_as_html(content_body)
    html = (
        '<!doctype html><html><body style="background:#f7f7f4;margin:0;padding:32px 16px;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f'{escape(_personalize(preheader, customer_name))}</div>'
        '<div style="background:#ffffff;border:1px solid #e4e4df;margin:0 auto;max-width:640px;'
        'padding:34px 32px;font-family:Arial,sans-serif;">'
        f'{content_html}{footer}</div></body></html>'
    )
    headers = {}
    if unsubscribe_url:
        headers = {
            'List-Unsubscribe': f'<{unsubscribe_url}>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
        }
    return EmailMultiAlternatives(
        subject=personalized_subject,
        body=personalized_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
        reply_to=[reply_to] if reply_to else [],
        headers=headers,
    ), html


def _ensure_recipient_records(campaign):
    existing = set(campaign.recipients.values_list('email', flat=True))
    missing = []
    for item in campaign.recipient_emails or []:
        if isinstance(item, dict):
            email = str(item.get('email', '')).strip()
            customer_name = str(item.get('customer_name', '')).strip()
        else:
            email = str(item).strip()
            customer_name = ''
        if email and email not in existing:
            existing.add(email)
            missing.append(EmailCampaignRecipient(
                campaign=campaign,
                email=email,
                customer_name=customer_name,
            ))
    if missing:
        EmailCampaignRecipient.objects.bulk_create(missing, ignore_conflicts=True)
    return campaign.recipients.all()


def _unsubscribe_url(campaign, email):
    signer = TimestampSigner()
    token = signer.sign_object({
        'business_id': str(campaign.business_id),
        'email': email,
    })
    site_url = getattr(settings, 'DJANGO_SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    return f'{site_url}{reverse("web:email-unsubscribe")}?{urlencode({"token": token})}'


@shared_task
def send_email_campaign(campaign_id):
    """Deliver a campaign and persist a separate result for every recipient."""
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.status not in (
            EmailCampaign.Status.QUEUED,
            EmailCampaign.Status.PARTIAL,
            EmailCampaign.Status.FAILED,
        ):
            return {'status': campaign.status, 'sent': campaign.sent_count}
        campaign.status = EmailCampaign.Status.SENDING
        campaign.error_message = ''
        campaign.started_at = campaign.started_at or timezone.now()
        campaign.save(update_fields=('status', 'error_message', 'started_at'))

    recipients = _ensure_recipient_records(campaign)
    # A crashed worker must not leave rows permanently unretryable.
    recipients.filter(status=EmailCampaignRecipient.Status.SENDING).update(
        status=EmailCampaignRecipient.Status.FAILED,
        error_message='Previous delivery was interrupted before completion.',
    )
    pending = list(recipients.filter(
        status__in=(EmailCampaignRecipient.Status.QUEUED, EmailCampaignRecipient.Status.FAILED),
    ).order_by('created_at', 'id'))

    connection_error = ''
    try:
        with get_connection() as connection:
            for recipient in pending:
                recipient.status = EmailCampaignRecipient.Status.SENDING
                recipient.error_message = ''
                recipient.save(update_fields=('status', 'error_message'))
                try:
                    message, html = _message_for_recipient(
                        subject=campaign.subject,
                        body=campaign.body,
                        html_body=campaign.html_body,
                        preheader=campaign.preheader,
                        email=recipient.email,
                        customer_name=recipient.customer_name,
                        reply_to=campaign.reply_to,
                        unsubscribe_url=_unsubscribe_url(campaign, recipient.email),
                    )
                    message.attach_alternative(html, 'text/html')
                    sent = connection.send_messages([message])
                    if sent == 1:
                        recipient.status = EmailCampaignRecipient.Status.SENT
                        recipient.sent_at = timezone.now()
                    else:
                        recipient.status = EmailCampaignRecipient.Status.FAILED
                        recipient.error_message = 'The email provider did not accept the message.'
                except Exception as error:  # Keep one bad address from stopping the campaign.
                    recipient.status = EmailCampaignRecipient.Status.FAILED
                    recipient.error_message = str(error)[:2000]
                recipient.save(update_fields=('status', 'sent_at', 'error_message'))
    except Exception as error:
        connection_error = str(error)[:2000]
        recipients.filter(status=EmailCampaignRecipient.Status.SENDING).update(
            status=EmailCampaignRecipient.Status.FAILED,
            error_message=connection_error,
        )

    sent_count = recipients.filter(status=EmailCampaignRecipient.Status.SENT).count()
    failed_count = recipients.filter(status=EmailCampaignRecipient.Status.FAILED).count()
    total_count = recipients.count()
    if failed_count == 0 and sent_count == total_count:
        status = EmailCampaign.Status.SENT
        error_message = ''
    elif sent_count:
        status = EmailCampaign.Status.PARTIAL
        error_message = connection_error or f'{failed_count} recipient(s) failed. Retry to send them again.'
    else:
        status = EmailCampaign.Status.FAILED
        error_message = connection_error or 'No recipient accepted the message. Retry the campaign.'

    completed_at = timezone.now()
    EmailCampaign.objects.filter(pk=campaign_id).update(
        status=status,
        sent_count=sent_count,
        failed_count=failed_count,
        error_message=error_message,
        sent_at=completed_at if sent_count else None,
        completed_at=completed_at,
    )
    return {'status': status, 'sent': sent_count, 'failed': failed_count, 'total': total_count}


@shared_task
def send_email_campaign_test(subject, body, html_body, preheader, reply_to, test_email):
    """Send a preview without creating a campaign or touching customer data."""
    message, html = _message_for_recipient(
        subject=subject,
        body=body,
        html_body=html_body,
        preheader=preheader,
        email=test_email,
        reply_to=reply_to,
        is_test=True,
    )
    message.attach_alternative(html, 'text/html')
    with get_connection() as connection:
        sent = connection.send_messages([message])
    return {'sent': sent == 1, 'email': test_email}


@shared_task
def dispatch_scheduled_email_campaigns():
    """Move due campaigns to the queue; Celery Beat runs this every minute."""
    due_ids = EmailCampaign.objects.filter(
        status=EmailCampaign.Status.SCHEDULED,
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    ).values_list('pk', flat=True)
    queued = 0
    for campaign_id in due_ids:
        updated = EmailCampaign.objects.filter(
            pk=campaign_id,
            status=EmailCampaign.Status.SCHEDULED,
        ).update(status=EmailCampaign.Status.QUEUED)
        if updated:
            send_email_campaign.delay(str(campaign_id))
            queued += 1
    return {'queued': queued}
