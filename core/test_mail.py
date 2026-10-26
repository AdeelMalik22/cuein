from email.message import EmailMessage as MimeMessage

from django.test import SimpleTestCase

from .mail_security import decrypt_mail_secret, encrypt_mail_secret
from .mail_services import normalize_subject, parse_rfc822


class MailServiceTests(SimpleTestCase):
    def test_mail_secrets_are_encrypted_and_recoverable(self):
        encrypted = encrypt_mail_secret('provider-refresh-token')
        self.assertNotEqual(encrypted, 'provider-refresh-token')
        self.assertEqual(decrypt_mail_secret(encrypted), 'provider-refresh-token')

    def test_subject_normalization_removes_reply_prefixes(self):
        self.assertEqual(normalize_subject(' Re: Fwd: Customer question '), 'customer question')

    def test_mime_parser_preserves_threading_headers_and_body(self):
        message = MimeMessage()
        message['Message-ID'] = '<message-1@example.com>'
        message['In-Reply-To'] = '<message-0@example.com>'
        message['References'] = '<message-0@example.com>'
        message['From'] = 'customer@example.com'
        message['To'] = 'sales@example.com'
        message['Subject'] = 'Question'
        message.set_content('Please call me back.')

        parsed = parse_rfc822(message.as_bytes())

        self.assertEqual(parsed['message_id'], '<message-1@example.com>')
        self.assertEqual(parsed['in_reply_to'], '<message-0@example.com>')
        self.assertEqual(parsed['references'], ['<message-0@example.com>'])
        self.assertEqual(parsed['from_email'], 'customer@example.com')
        self.assertEqual(parsed['text_body'], 'Please call me back.')
