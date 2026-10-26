"""Mailbox providers, message normalization, matching, and threading."""

import base64
import imaplib
import json
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
from email import policy
from email.message import EmailMessage as MimeMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.signing import TimestampSigner
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from leads.models import Activity, Lead

from .mail_security import decrypt_mail_secret, encrypt_mail_secret
from .models import (
    EmailInternalNote,
    EmailMessage,
    EmailMessageAttachment,
    EmailTrackingEvent,
    EmailThread,
    EmailUnmatched,
    MailboxConnection,
)


class MailProviderError(Exception):
    """A provider request or mailbox configuration failed."""


def normalize_subject(subject):
    value = ' '.join((subject or '').split()).strip()
    while re.match(r'^(re|fw|fwd)\s*:', value, flags=re.IGNORECASE):
        value = re.sub(r'^(re|fw|fwd)\s*:\s*', '', value, count=1, flags=re.IGNORECASE).strip()
    return value.casefold()


def _decode_header(value):
    return str(value or '').strip()


def _decode_part(part):
    try:
        return part.get_content()
    except (LookupError, AttributeError):
        payload = part.get_payload(decode=True) or b''
        return payload.decode(part.get_content_charset() or 'utf-8', errors='replace')


def _message_addresses(value):
    return [email.casefold() for _name, email in getaddresses([value or '']) if email]


def parse_rfc822(raw_bytes):
    """Convert provider MIME bytes into a safe provider-independent payload."""
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    text_body = ''
    html_body = ''
    attachments = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        payload = part.get_payload(decode=True) or b''
        filename = part.get_filename()
        if disposition == 'attachment' or filename:
            attachments.append({
                'filename': filename or f'attachment-{uuid.uuid4().hex}',
                'content_type': part.get_content_type(),
                'content_id': str(part.get('Content-ID', '')).strip('<>'),
                'content': payload,
                'is_inline': disposition == 'inline',
            })
            continue
        content = _decode_part(part)
        if part.get_content_type() == 'text/html':
            html_body = html_body or content
        elif part.get_content_type() == 'text/plain':
            text_body = text_body or content
    received_at = None
    if message.get('Date'):
        try:
            received_at = parsedate_to_datetime(message.get('Date'))
            if timezone.is_naive(received_at):
                received_at = timezone.make_aware(received_at)
        except (TypeError, ValueError, OverflowError):
            received_at = None
    return {
        'message_id': _decode_header(message.get('Message-ID')),
        'in_reply_to': _decode_header(message.get('In-Reply-To')),
        'references': [item for item in _decode_header(message.get('References')).split() if item],
        'subject': _decode_header(message.get('Subject')),
        'from_email': (_message_addresses(message.get('From')) or [''])[0],
        'to_emails': _message_addresses(message.get('To')),
        'cc_emails': _message_addresses(message.get('Cc')),
        'bcc_emails': _message_addresses(message.get('Bcc')),
        'text_body': text_body.strip(),
        'html_body': html_body,
        'received_at': received_at or timezone.now(),
        'raw_headers': {
            key: _decode_header(value)
            for key, value in message.items()
            if key.lower() in {
                'message-id', 'in-reply-to', 'references', 'date', 'from', 'to', 'cc',
                'subject', 'reply-to', 'x-gmail-thread-id', 'x-gm-msgid',
            }
        },
        'attachments': attachments,
    }


def _provider_request(url, *, method='GET', headers=None, payload=None):
    request_data = None
    request_headers = headers.copy() if headers else {}
    if payload is not None:
        request_data = json.dumps(payload).encode('utf-8')
        request_headers.setdefault('Content-Type', 'application/json')
    request = urllib.request.Request(url, data=request_data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode('utf-8')) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        raise MailProviderError(f'Provider request failed ({error.code}): {detail[:500]}') from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise MailProviderError(f'Provider request failed: {error}') from error


def mailbox_oauth_authorization_url(provider, state, redirect_uri):
    """Build a provider consent URL without storing secrets in the browser."""
    if provider == MailboxConnection.Provider.GMAIL:
        client_id = getattr(settings, 'GMAIL_OAUTH_CLIENT_ID', '')
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'access_type': 'offline',
            'prompt': 'consent',
            'scope': 'https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email',
            'state': state,
        }
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    if provider == MailboxConnection.Provider.MICROSOFT:
        client_id = getattr(settings, 'MICROSOFT_OAUTH_CLIENT_ID', '')
        tenant = getattr(settings, 'MICROSOFT_OAUTH_TENANT', 'common')
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'response_mode': 'query',
            'scope': 'offline_access openid profile email User.Read Mail.Read Mail.Send',
            'state': state,
        }
        return f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?' + urllib.parse.urlencode(params)
    raise MailProviderError('OAuth is only available for Gmail and Microsoft.')


def complete_mailbox_oauth(provider, code, redirect_uri):
    if provider == MailboxConnection.Provider.GMAIL:
        client_id = getattr(settings, 'GMAIL_OAUTH_CLIENT_ID', '')
        client_secret = getattr(settings, 'GMAIL_OAUTH_CLIENT_SECRET', '')
        token_url = 'https://oauth2.googleapis.com/token'
        profile_url = 'https://gmail.googleapis.com/gmail/v1/users/me/profile'
    elif provider == MailboxConnection.Provider.MICROSOFT:
        client_id = getattr(settings, 'MICROSOFT_OAUTH_CLIENT_ID', '')
        client_secret = getattr(settings, 'MICROSOFT_OAUTH_CLIENT_SECRET', '')
        tenant = getattr(settings, 'MICROSOFT_OAUTH_TENANT', 'common')
        token_url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
        profile_url = 'https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName,displayName'
    else:
        raise MailProviderError('Unsupported OAuth provider.')
    if not client_id or not client_secret:
        raise MailProviderError('OAuth client credentials are not configured.')
    body = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode('utf-8')
    request = urllib.request.Request(token_url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token_payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise MailProviderError(f'Could not complete mailbox authorization: {error}') from error
    if not token_payload.get('access_token'):
        raise MailProviderError('OAuth provider returned no access token.')
    profile = _provider_request(profile_url, headers={'Authorization': f'Bearer {token_payload["access_token"]}'})
    email = profile.get('emailAddress') or profile.get('mail') or profile.get('userPrincipalName') or profile.get('email')
    if not email:
        raise MailProviderError('The connected provider did not return an email address.')
    return {
        'account_email': email.casefold(),
        'display_name': profile.get('displayName', ''),
        'provider_account_id': profile.get('id') or profile.get('emailAddress', ''),
        'access_token': token_payload['access_token'],
        'refresh_token': token_payload.get('refresh_token', ''),
        'expires_at': timezone.now() + timedelta(seconds=int(token_payload.get('expires_in', 3600))),
        'scopes': token_payload.get('scope', '').split(),
    }


def create_oauth_mailbox(*, business, user, provider, token_data, is_shared=False):
    existing = MailboxConnection.objects.filter(
        business=business,
        account_email=token_data['account_email'],
    ).first()
    refresh_token = token_data.get('refresh_token', '')
    if not refresh_token and existing:
        refresh_token = decrypt_mail_secret(existing.refresh_token_secret)
    mailbox, _created = MailboxConnection.objects.update_or_create(
        business=business,
        account_email=token_data['account_email'],
        defaults={
            'connected_by': user,
            'provider': provider,
            'display_name': token_data.get('display_name', ''),
            'provider_account_id': token_data.get('provider_account_id', ''),
            'is_shared': is_shared,
            'status': MailboxConnection.Status.ACTIVE,
            'scopes': token_data.get('scopes', []),
            'access_token_secret': encrypt_mail_secret(token_data.get('access_token', '')),
            'refresh_token_secret': encrypt_mail_secret(refresh_token),
            'token_expires_at': token_data.get('expires_at'),
            'last_sync_error': '',
        },
    )
    configure_mailbox_push(mailbox)
    return mailbox


def configure_mailbox_push(connection):
    """Register provider push when its external notification service is configured."""
    try:
        if connection.provider == MailboxConnection.Provider.GMAIL and getattr(settings, 'GMAIL_PUBSUB_TOPIC', ''):
            response = _provider_request(
                'https://gmail.googleapis.com/gmail/v1/users/me/watch',
                method='POST',
                headers={'Authorization': f'Bearer {_access_token(connection)}'},
                payload={'labelIds': ['INBOX', 'SENT'], 'topicName': settings.GMAIL_PUBSUB_TOPIC},
            )
            connection.sync_cursor = response.get('historyId', connection.sync_cursor)
            connection.webhook_subscription_id = ''
            connection.webhook_expires_at = timezone.now() + timedelta(days=6)
        elif connection.provider == MailboxConnection.Provider.MICROSOFT and getattr(settings, 'MAIL_WEBHOOK_BASE_URL', ''):
            from django.urls import reverse

            secret = uuid.uuid4().hex
            payload = {
                'changeType': 'created',
                'notificationUrl': f'{settings.MAIL_WEBHOOK_BASE_URL.rstrip("/")}{reverse("web:mailbox-webhook", kwargs={"provider": "microsoft"})}',
                'resource': '/me/mailFolders(\'inbox\')/messages',
                'expirationDateTime': (timezone.now() + timedelta(hours=24)).isoformat().replace('+00:00', 'Z'),
                'clientState': secret,
            }
            response = _provider_request(
                'https://graph.microsoft.com/v1.0/subscriptions',
                method='POST',
                headers={'Authorization': f'Bearer {_access_token(connection)}'},
                payload=payload,
            )
            connection.webhook_subscription_id = response.get('id', '')
            connection.webhook_verification_secret = secret
            expires = response.get('expirationDateTime', '').replace('Z', '+00:00')
            try:
                connection.webhook_expires_at = timezone.datetime.fromisoformat(expires)
            except ValueError:
                connection.webhook_expires_at = timezone.now() + timedelta(hours=24)
        else:
            return connection
        connection.save(update_fields=('sync_cursor', 'webhook_subscription_id', 'webhook_verification_secret', 'webhook_expires_at', 'updated_at'))
    except MailProviderError:
        # Mail still works through the polling fallback when push registration
        # is unavailable or its external topic/subscription is not configured.
        connection.last_sync_error = 'Push registration failed; polling fallback remains active.'
        connection.save(update_fields=('last_sync_error', 'updated_at'))
    return connection


def _oauth_token(connection):
    if connection.provider == MailboxConnection.Provider.GMAIL:
        client_id = getattr(settings, 'GMAIL_OAUTH_CLIENT_ID', '')
        client_secret = getattr(settings, 'GMAIL_OAUTH_CLIENT_SECRET', '')
        token_url = 'https://oauth2.googleapis.com/token'
    elif connection.provider == MailboxConnection.Provider.MICROSOFT:
        client_id = getattr(settings, 'MICROSOFT_OAUTH_CLIENT_ID', '')
        client_secret = getattr(settings, 'MICROSOFT_OAUTH_CLIENT_SECRET', '')
        tenant = getattr(settings, 'MICROSOFT_OAUTH_TENANT', 'common')
        token_url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    else:
        return ''
    if not client_id or not client_secret or not connection.refresh_token_secret:
        raise MailProviderError('OAuth credentials are not configured for this mailbox.')
    body = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': decrypt_mail_secret(connection.refresh_token_secret),
        'grant_type': 'refresh_token',
    }).encode('utf-8')
    request = urllib.request.Request(token_url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise MailProviderError(f'Could not refresh mailbox token: {error}') from error
    if not payload.get('access_token'):
        raise MailProviderError('Mailbox token refresh returned no access token.')
    connection.access_token_secret = encrypt_mail_secret(payload['access_token'])
    connection.token_expires_at = timezone.now() + timedelta(seconds=int(payload.get('expires_in', 3600)))
    connection.save(update_fields=('access_token_secret', 'token_expires_at', 'updated_at'))
    return payload['access_token']


def _access_token(connection):
    if connection.token_expires_at and connection.token_expires_at > timezone.now() + timedelta(minutes=2):
        return decrypt_mail_secret(connection.access_token_secret)
    return _oauth_token(connection)


def _gmail_messages(connection, since):
    token = _access_token(connection)
    query = f'after:{since.strftime("%Y/%m/%d")}' if since else ''
    page_token = ''
    while True:
        params = {'maxResults': '100'}
        if query:
            params['q'] = query
        if page_token:
            params['pageToken'] = page_token
        payload = _provider_request(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages?' + urllib.parse.urlencode(params),
            headers={'Authorization': f'Bearer {token}'},
        )
        for item in payload.get('messages', []):
            detail = _provider_request(
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{item["id"]}?format=raw',
                headers={'Authorization': f'Bearer {token}'},
            )
            raw = base64.urlsafe_b64decode(detail.get('raw', '') + '===')
            yield item['id'], parse_rfc822(raw)
        page_token = payload.get('nextPageToken', '')
        if not page_token:
            break


def _graph_messages(connection, since):
    token = _access_token(connection)
    query = urllib.parse.urlencode({
        '$top': '100',
        '$orderby': 'receivedDateTime asc',
        '$filter': f'receivedDateTime ge {since.strftime("%Y-%m-%dT%H:%M:%SZ")}',
        '$select': 'id,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,bodyPreview,internetMessageHeaders',
    })
    url = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?' + query
    while url:
        payload = _provider_request(url, headers={'Authorization': f'Bearer {token}'})
        for item in payload.get('value', []):
            headers = {header['name'].lower(): header['value'] for header in item.get('internetMessageHeaders', [])}
            body = item.get('body', {})
            from_email = ((item.get('from') or {}).get('emailAddress') or {}).get('address', '').casefold()
            to_emails = [((recipient.get('emailAddress') or {}).get('address', '')).casefold() for recipient in item.get('toRecipients', [])]
            cc_emails = [((recipient.get('emailAddress') or {}).get('address', '')).casefold() for recipient in item.get('ccRecipients', [])]
            received = item.get('receivedDateTime', '').replace('Z', '+00:00')
            try:
                received_at = timezone.datetime.fromisoformat(received)
            except ValueError:
                received_at = timezone.now()
            yield item['id'], {
                'message_id': item.get('internetMessageId', ''),
                'in_reply_to': headers.get('in-reply-to', ''),
                'references': headers.get('references', '').split(),
                'subject': item.get('subject', ''),
                'from_email': from_email,
                'to_emails': [email for email in to_emails if email],
                'cc_emails': [email for email in cc_emails if email],
                'bcc_emails': [],
                'text_body': body.get('content', '') if body.get('contentType') == 'text' else item.get('bodyPreview', ''),
                'html_body': body.get('content', '') if body.get('contentType') == 'html' else '',
                'received_at': received_at,
                'raw_headers': headers,
                'attachments': [],
            }
        url = payload.get('@odata.nextLink', '')


def _imap_messages(connection, since):
    username = decrypt_mail_secret(connection.username_secret) or connection.account_email
    password = decrypt_mail_secret(connection.password_secret)
    if not connection.imap_host or not username or not password:
        raise MailProviderError('IMAP host and credentials are required.')
    try:
        client = imaplib.IMAP4_SSL(connection.imap_host, connection.imap_port) if connection.imap_use_ssl else imaplib.IMAP4(connection.imap_host, connection.imap_port)
    except (OSError, imaplib.IMAP4.error) as error:
        raise MailProviderError(f'Could not connect to IMAP: {error}') from error
    try:
        try:
            client.login(username, password)
            client.select('INBOX')
            criteria = f'SINCE {since.strftime("%d-%b-%Y")}' if since else 'ALL'
            _status, data = client.uid('search', None, criteria)
            for uid in (data[0] or b'').split():
                _status, fetched = client.uid('fetch', uid, '(RFC822)')
                raw = next((item[1] for item in fetched if isinstance(item, tuple)), b'')
                if raw:
                    yield uid.decode(), parse_rfc822(raw)
        except (OSError, imaplib.IMAP4.error) as error:
            raise MailProviderError(f'IMAP sync failed: {error}') from error
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _matching_lead(business_id, addresses):
    clean = {address.casefold() for address in addresses if address}
    if not clean:
        return None
    matches = Q()
    for address in clean:
        matches |= Q(email__iexact=address)
    return Lead.objects.filter(business_id=business_id).filter(matches).order_by('id').first()


def _thread_for_payload(business_id, payload):
    message_ids = [payload.get('in_reply_to', '')] + payload.get('references', [])
    message_ids = [item for item in message_ids if item]
    if message_ids:
        match = EmailMessage.objects.filter(business_id=business_id).filter(
            Q(message_id__in=message_ids) | Q(in_reply_to__in=message_ids),
        ).select_related('thread').first()
        if match:
            return match.thread
    return EmailThread.objects.filter(
        business_id=business_id,
        normalized_subject=normalize_subject(payload.get('subject', '')),
    ).order_by('-last_message_at', '-updated_at').first()


@transaction.atomic
def ingest_message(*, connection, provider_message_id, payload, direction=EmailMessage.Direction.INBOUND, actor=None):
    """Idempotently persist a provider message, thread it, and match a lead."""
    existing = EmailMessage.objects.filter(
        mailbox=connection,
        provider_message_id=provider_message_id,
    ).first() if provider_message_id else None
    if existing:
        return existing, False
    addresses = [payload.get('from_email', '')] + payload.get('to_emails', []) + payload.get('cc_emails', [])
    lead = _matching_lead(connection.business_id, addresses)
    thread = _thread_for_payload(connection.business_id, payload)
    if not thread:
        thread = EmailThread.objects.create(
            business_id=connection.business_id,
            subject=payload.get('subject', ''),
            normalized_subject=normalize_subject(payload.get('subject', '')),
            lead=lead,
        )
    elif lead and not thread.lead_id:
        thread.lead = lead
    timestamp = payload.get('received_at') or timezone.now()
    email_message = EmailMessage.objects.create(
        business_id=connection.business_id,
        thread=thread,
        mailbox=connection,
        provider_message_id=provider_message_id or '',
        message_id=payload.get('message_id', ''),
        in_reply_to=payload.get('in_reply_to', ''),
        references=payload.get('references', []),
        direction=direction,
        status=EmailMessage.Status.SENT,
        from_email=payload.get('from_email') or connection.account_email,
        to_emails=payload.get('to_emails', []),
        cc_emails=payload.get('cc_emails', []),
        bcc_emails=payload.get('bcc_emails', []),
        subject=payload.get('subject', ''),
        text_body=payload.get('text_body', ''),
        html_body=payload.get('html_body', ''),
        received_at=timestamp if direction == EmailMessage.Direction.INBOUND else None,
        sent_at=timestamp if direction == EmailMessage.Direction.OUTBOUND else None,
        raw_headers=payload.get('raw_headers', {}),
    )
    if direction == EmailMessage.Direction.INBOUND and (
        email_message.from_email in {'mailer-daemon@', 'postmaster@'}
        or email_message.from_email.startswith(('mailer-daemon', 'postmaster'))
        or 'delivery status notification' in email_message.subject.casefold()
        or 'undeliverable' in email_message.subject.casefold()
    ):
        bounce_ids = [email_message.in_reply_to] + (email_message.references or [])
        EmailMessage.objects.filter(
            business_id=connection.business_id,
        ).filter(Q(message_id__in=[item for item in bounce_ids if item])).update(
            status=EmailMessage.Status.BOUNCED,
        )
    for attachment in payload.get('attachments', []):
        stored = EmailMessageAttachment.objects.create(
            message=email_message,
            filename=attachment['filename'],
            content_type=attachment.get('content_type', ''),
            content_id=attachment.get('content_id', ''),
            size=len(attachment.get('content', b'')),
            is_inline=attachment.get('is_inline', False),
        )
        stored.file.save(attachment['filename'], ContentFile(attachment.get('content', b'')), save=True)
    if direction == EmailMessage.Direction.INBOUND:
        EmailMessage.objects.filter(
            thread=thread,
            direction=EmailMessage.Direction.OUTBOUND,
            status__in=(EmailMessage.Status.SENT, EmailMessage.Status.DELIVERED),
        ).exclude(pk=email_message.pk).update(status=EmailMessage.Status.REPLIED)
        thread.last_direction = EmailThread.LastDirection.INBOUND
    else:
        thread.last_direction = EmailThread.LastDirection.OUTBOUND
    thread.subject = thread.subject or payload.get('subject', '')
    thread.last_message_at = timestamp
    thread.last_message_preview = (payload.get('text_body') or '').strip()[:500]
    thread.message_count = EmailMessage.objects.filter(thread=thread).count()
    thread.save(update_fields=('lead', 'subject', 'last_direction', 'last_message_at', 'last_message_preview', 'message_count', 'updated_at'))
    if lead:
        Activity.objects.create(
            business_id=connection.business_id,
            lead=lead,
            kind=Activity.Kind.EMAIL,
            content=(payload.get('subject') or '(no subject)')[:255],
            metadata={'email_message_id': str(email_message.id), 'direction': direction},
            created_by=actor if direction == EmailMessage.Direction.OUTBOUND else None,
        )
    elif direction == EmailMessage.Direction.INBOUND:
        EmailUnmatched.objects.get_or_create(
            business_id=connection.business_id,
            message=email_message,
            defaults={
                'mailbox': connection,
                'from_email': email_message.from_email,
                'subject': email_message.subject,
            },
        )
    return email_message, True


def sync_mailbox(connection, *, backfill_days=None):
    since = (
        connection.last_sync_at - timedelta(minutes=5)
        if connection.last_sync_at
        else timezone.now() - timedelta(days=backfill_days or connection.backfill_days or 90)
    )
    if connection.provider == MailboxConnection.Provider.GMAIL:
        messages = _gmail_messages(connection, since)
    elif connection.provider == MailboxConnection.Provider.MICROSOFT:
        messages = _graph_messages(connection, since)
    elif connection.provider == MailboxConnection.Provider.IMAP:
        messages = _imap_messages(connection, since)
    else:
        raise MailProviderError('Unsupported mailbox provider.')
    created = 0
    for provider_message_id, payload in messages:
        sender_domain = payload.get('from_email', '').rsplit('@', 1)[-1].casefold()
        hidden_domains = {str(value).strip().lstrip('@').casefold() for value in (connection.hidden_domains or [])}
        hidden_labels = {str(value).strip().casefold() for value in (connection.hidden_labels or [])}
        message_labels = {
            label.strip().casefold()
            for label in str(payload.get('raw_headers', {}).get('x-gmail-label', '')).split(',')
            if label.strip()
        }
        if sender_domain in hidden_domains or hidden_labels.intersection(message_labels):
            continue
        _message, was_created = ingest_message(
            connection=connection,
            provider_message_id=provider_message_id,
            payload=payload,
        )
        created += int(was_created)
    connection.status = MailboxConnection.Status.ACTIVE
    connection.last_sync_at = timezone.now()
    connection.last_sync_error = ''
    connection.save(update_fields=('status', 'last_sync_at', 'last_sync_error', 'updated_at'))
    return created


def _mime_for_reply(*, connection, thread, subject, body, html_body='', to_emails=None, cc_emails=None, attachments=None):
    message = MimeMessage()
    message['From'] = connection.account_email
    message['To'] = ', '.join(to_emails or [])
    if cc_emails:
        message['Cc'] = ', '.join(cc_emails)
    message['Subject'] = subject or thread.subject or '(no subject)'
    message['Message-ID'] = f'<{uuid.uuid4().hex}@cuein.local>'
    previous = thread.messages.filter(direction=EmailMessage.Direction.INBOUND).order_by('-created_at').first()
    if previous and previous.message_id:
        message['In-Reply-To'] = previous.message_id
        message['References'] = ' '.join((previous.references or []) + [previous.message_id])
    message.set_content(body or '')
    if html_body:
        if getattr(settings, 'MAIL_TRACKING_ENABLED', False):
            html_body = _instrument_tracking_html(html_body, thread=thread, message_id=message['Message-ID'])
        message.add_alternative(html_body, subtype='html')
    for attachment in attachments or []:
        message.add_attachment(
            attachment['content'],
            maintype=(attachment.get('content_type') or 'application/octet-stream').split('/')[0],
            subtype=(attachment.get('content_type') or 'application/octet-stream').split('/')[-1],
            filename=attachment.get('filename') or 'attachment',
        )
    return message


def _instrument_tracking_html(html_body, *, thread, message_id):
    site_url = getattr(settings, 'DJANGO_SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    signer = TimestampSigner()

    def rewrite_link(match):
        url = match.group(2)
        if not url.startswith(('http://', 'https://')):
            return match.group(0)
        token = signer.sign_object({
            'business_id': str(thread.business_id),
            'message_id': message_id,
            'target_url': url,
        })
        tracked = f'{site_url}{reverse("web:mail-click-track")}?{urllib.parse.urlencode({"token": token})}'
        return f'{match.group(1)}{tracked}{match.group(3)}'

    html_body = re.sub(r'(href=["\'])(https?://[^"\']+)(["\'])', rewrite_link, html_body, flags=re.IGNORECASE)
    token = signer.sign_object({'business_id': str(thread.business_id), 'message_id': message_id})
    pixel_url = f'{site_url}{reverse("web:mail-open-track")}?{urllib.parse.urlencode({"token": token})}'
    return f'{html_body}<img src="{pixel_url}" width="1" height="1" alt="" style="display:none!important;" />'


def _send_gmail(connection, mime_message):
    token = _access_token(connection)
    raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode('ascii').rstrip('=')
    return _provider_request(
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
        method='POST',
        headers={'Authorization': f'Bearer {token}'},
        payload={'raw': raw},
    ).get('id', '')


def _send_microsoft(connection, mime_message):
    token = _access_token(connection)
    body_part = mime_message.get_body(preferencelist=('html', 'plain'))
    graph_attachments = []
    for part in mime_message.iter_attachments():
        graph_attachments.append({
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': part.get_filename() or 'attachment',
            'contentType': part.get_content_type(),
            'contentBytes': base64.b64encode(part.get_payload(decode=True) or b'').decode('ascii'),
        })
    _provider_request(
        'https://graph.microsoft.com/v1.0/me/sendMail',
        method='POST',
        headers={'Authorization': f'Bearer {token}'},
        payload={'message': {
            'subject': mime_message['Subject'],
            'body': {'contentType': 'HTML' if body_part and body_part.get_content_type() == 'text/html' else 'Text', 'content': body_part.get_content() if body_part else ''},
            'toRecipients': [{'emailAddress': {'address': address.strip()}} for address in mime_message['To'].split(',') if address.strip()],
            'attachments': graph_attachments,
        }, 'saveToSentItems': True},
    )
    return mime_message['Message-ID']


def _send_smtp(connection, mime_message):
    username = decrypt_mail_secret(connection.username_secret) or connection.account_email
    password = decrypt_mail_secret(connection.password_secret)
    if not connection.smtp_host or not username or not password:
        raise MailProviderError('SMTP host and credentials are required.')
    try:
        client = smtplib.SMTP(connection.smtp_host, connection.smtp_port, timeout=30)
    except (OSError, smtplib.SMTPException) as error:
        raise MailProviderError(f'Could not connect to SMTP: {error}') from error
    try:
        try:
            client.ehlo()
            if connection.smtp_use_tls:
                client.starttls()
                client.ehlo()
            client.login(username, password)
            client.send_message(mime_message)
        except (OSError, smtplib.SMTPException) as error:
            raise MailProviderError(f'SMTP send failed: {error}') from error
    finally:
        try:
            client.quit()
        except Exception:
            pass
    return mime_message['Message-ID']


@transaction.atomic
def _personalize_reply(value, *, thread, company_name):
    customer_name = thread.lead.customer_name if thread.lead_id else ''
    return (
        (value or '')
        .replace('{{ customer_name }}', customer_name)
        .replace('{{customer_name}}', customer_name)
        .replace('{{ company }}', company_name)
        .replace('{{company}}', company_name)
    )


def send_thread_reply(*, thread, connection, actor, body, html_body='', subject='', to_emails=None, cc_emails=None, attachments=None):
    """Send a reply through the connected provider and persist the audit row."""
    if thread.business_id != connection.business_id or connection.status != MailboxConnection.Status.ACTIVE:
        raise MailProviderError('The mailbox and thread must belong to the active workspace.')
    recipients = to_emails or []
    if not recipients:
        previous = thread.messages.order_by('-created_at').first()
        recipients = [previous.from_email] if previous and previous.direction == EmailMessage.Direction.INBOUND else []
    if not recipients:
        raise MailProviderError('This thread has no reply recipient.')
    body = _personalize_reply(body, thread=thread, company_name=thread.business.name)
    html_body = _personalize_reply(html_body, thread=thread, company_name=thread.business.name)
    if connection.signature:
        signature = _personalize_reply(connection.signature, thread=thread, company_name=thread.business.name)
        body = f'{body.rstrip()}\n\n--\n{signature}'
        if html_body:
            html_body = f'{html_body}<p>--<br>{signature.replace(chr(10), "<br>")}</p>'
    subject = _personalize_reply(subject, thread=thread, company_name=thread.business.name)
    mime_message = _mime_for_reply(
        connection=connection,
        thread=thread,
        subject=subject or (thread.subject if thread.subject.lower().startswith('re:') else f'Re: {thread.subject}'),
        body=body,
        html_body=html_body,
        to_emails=recipients,
        cc_emails=cc_emails,
        attachments=attachments,
    )
    if connection.provider == MailboxConnection.Provider.GMAIL:
        provider_id = _send_gmail(connection, mime_message)
    elif connection.provider == MailboxConnection.Provider.MICROSOFT:
        provider_id = _send_microsoft(connection, mime_message)
    else:
        provider_id = _send_smtp(connection, mime_message)
    payload = parse_rfc822(mime_message.as_bytes())
    payload['from_email'] = connection.account_email
    payload['to_emails'] = recipients
    payload['subject'] = mime_message['Subject']
    payload['sent_at'] = timezone.now()
    message, _created = ingest_message(
        connection=connection,
        provider_message_id=provider_id,
        payload=payload,
        direction=EmailMessage.Direction.OUTBOUND,
        actor=actor,
    )
    thread.status = EmailThread.Status.WAITING
    thread.save(update_fields=('status', 'updated_at'))
    return message


def add_internal_note(*, thread, author, body):
    if thread.business_id != getattr(author, 'business_id', None) and not thread.business.memberships.filter(user=author).exists():
        raise MailProviderError('The note author is not a member of this workspace.')
    return EmailInternalNote.objects.create(
        business_id=thread.business_id,
        thread=thread,
        author=author,
        body=body.strip(),
    )
