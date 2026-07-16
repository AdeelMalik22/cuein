from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .email_verification import EmailVerificationCooldownError, send_email_verification
from .models import Business, Membership, PasswordResetRequest, PendingRegistration, User
from .password_reset import PasswordResetCooldownError, send_password_reset_code


PUBLIC_AUTH_THROTTLE_TEST_RATES = {
    'auth_signup': '2/minute',
    'auth_email_verify': '2/minute',
    'auth_email_resend': '2/minute',
    'auth_password_reset_request': '2/minute',
    'auth_password_reset_confirm': '2/minute',
    'auth_token': '2/minute',
    'auth_token_refresh': '2/minute',
}


class CurrentUserApiTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.user = User.objects.create_user(
            username='adeel',
            password='test-password',
            business=self.business,
            role=User.Role.OWNER,
        )

    def test_me_requires_authentication(self):
        response = self.client.get(reverse('current_user'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_only_the_authenticated_users_business(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse('current_user'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'adeel')
        self.assertEqual(response.data['business']['id'], str(self.business.id))
        self.assertEqual(response.data['business']['name'], 'North Star Solar')


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'business-and-team-api-tests',
        },
    },
)
class BusinessAndTeamApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.business = Business.objects.create(name='North Star Solar')
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.owner = User.objects.create_user(
            username='owner',
            password='test-password',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.other_user = User.objects.create_user(
            username='other-owner',
            password='test-password',
            business=self.other_business,
            role=User.Role.OWNER,
        )

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_signup_holds_registration_until_email_is_verified(self):
        response = self.client.post(
            reverse('signup'),
            {
                'business_name': 'Skyline AC',
                'username': 'skyline-owner',
                'password': 'Strong-test-password-123',
                'email': 'owner@skyline.example',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['verification_required'])
        self.assertEqual(response.data['email'], 'owner@skyline.example')
        self.assertNotIn('access', response.data)
        registration = PendingRegistration.objects.get(username='skyline-owner')
        self.assertEqual(registration.business_name, 'Skyline AC')
        self.assertFalse(User.objects.filter(username='skyline-owner').exists())
        self.assertFalse(Business.objects.filter(name='Skyline AC').exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('six-digit code', mail.outbox[0].body)
        self.assertNotIn('/verify-email/', mail.outbox[0].body)

    def test_verification_code_creates_the_owner_and_returns_tokens(self):
        PendingRegistration.objects.create(
            business_name='Verified AC',
            username='verified-ac-owner',
            email='verified@ac.example',
            password=make_password('Strong-test-password-123'),
            verification_code_hash=make_password('123456'),
            verification_sent_at=timezone.now(),
        )

        response = self.client.post(
            reverse('email_verify'),
            {'email': 'verified@ac.example', 'code': '123456'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        owner = User.objects.get(username='verified-ac-owner')
        self.assertTrue(owner.is_active)
        self.assertEqual(owner.business.name, 'Verified AC')
        self.assertFalse(PendingRegistration.objects.filter(email='verified@ac.example').exists())

    def test_owner_can_update_only_own_business(self):
        self.client.force_authenticate(self.owner)

        response = self.client.patch(
            reverse('current_business'),
            {'name': 'North Star Energy'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.business.refresh_from_db()
        self.other_business.refresh_from_db()
        self.assertEqual(self.business.name, 'North Star Energy')
        self.assertEqual(self.other_business.name, 'Bright CCTV')

    def test_owner_cannot_retrieve_a_user_from_another_business(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse('user-detail', args=[self.other_user.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_creates_user_inside_own_business(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('user-list'),
            {
                'username': 'salesperson',
                'password': 'Strong-test-password-123',
                'role': User.Role.SALESPERSON,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_user = User.objects.get(username='salesperson')
        self.assertEqual(new_user.business, self.business)


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'password-reset-api-tests',
        },
    },
)
class PasswordResetApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.business = Business.objects.create(name='North Star Solar')
        self.user = User.objects.create_user(
            username='owner',
            password='Original-password-5172!',
            email='owner@northstar.example',
            business=self.business,
            role=User.Role.OWNER,
        )

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_request_sends_a_code_for_an_active_account_without_revealing_unknown_addresses(self):
        known_response = self.client.post(
            reverse('password_reset_request'),
            {'email': self.user.email},
            format='json',
        )

        self.assertEqual(known_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('six-digit code', mail.outbox[0].body)
        reset_request = PasswordResetRequest.objects.get(user=self.user)
        self.assertTrue(reset_request.code_hash)
        self.assertEqual(reset_request.attempts, 0)
        self.assertIsNotNone(reset_request.sent_at)

        unknown_response = self.client.post(
            reverse('password_reset_request'),
            {'email': 'missing@example.com'},
            format='json',
        )

        self.assertEqual(unknown_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown_response.data, known_response.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_confirm_changes_the_password_and_makes_the_code_single_use(self):
        PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now(),
        )

        response = self.client.post(
            reverse('password_reset_confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Unique-reset-passphrase-5172!',
            },
            format='json',
        )

        self.user.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.user.check_password('Unique-reset-passphrase-5172!'))
        self.assertFalse(PasswordResetRequest.objects.filter(user=self.user).exists())

        reused_response = self.client.post(
            reverse('password_reset_confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Another-unique-passphrase-5172!',
            },
            format='json',
        )

        self.assertEqual(reused_response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(PASSWORD_RESET_TIMEOUT=60)
    def test_expired_code_cannot_change_the_password(self):
        PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.post(
            reverse('password_reset_confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Unique-reset-passphrase-5172!',
            },
            format='json',
        )

        self.user.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.user.check_password('Original-password-5172!'))

    def test_five_incorrect_codes_lock_the_reset_until_a_fresh_code_is_sent(self):
        reset_request = PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now(),
        )
        wrong_payload = {
            'email': self.user.email,
            'code': '000000',
            'new_password': 'Unique-reset-passphrase-5172!',
        }
        for _ in range(5):
            response = self.client.post(
                reverse('password_reset_confirm'),
                wrong_payload,
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        locked_response = self.client.post(
            reverse('password_reset_confirm'),
            {**wrong_payload, 'code': '123456'},
            format='json',
        )

        reset_request.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(reset_request.attempts, 5)
        self.assertEqual(locked_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.user.check_password('Original-password-5172!'))

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'password-reset-token-revocation-tests',
            },
        },
    )
    def test_reset_blacklists_existing_refresh_tokens(self):
        PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now(),
        )
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            reverse('password_reset_confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Unique-reset-passphrase-5172!',
            },
            format='json',
        )
        refresh_response = self.client.post(
            reverse('token_refresh'),
            {'refresh': str(refresh)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)


class EmailDeliveryCooldownTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.user = User.objects.create_user(
            username='owner',
            password='Original-password-5172!',
            email='owner@northstar.example',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.registration = PendingRegistration.objects.create(
            business_name='Pending Solar',
            username='pending-owner',
            email='pending@northstar.example',
            password=make_password('Strong-test-password-123'),
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        EMAIL_VERIFICATION_RESEND_COOLDOWN=60,
    )
    def test_verification_resend_is_limited_per_email(self):
        send_email_verification(self.registration)

        with self.assertRaises(EmailVerificationCooldownError):
            send_email_verification(self.registration)

        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        PASSWORD_RESET_RESEND_COOLDOWN=60,
    )
    def test_password_reset_resend_is_limited_per_email(self):
        send_password_reset_code(self.user)

        with self.assertRaises(PasswordResetCooldownError):
            send_password_reset_code(self.user)

        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'token-login-protection-tests',
        },
    },
)
class TokenLoginProtectionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.business = Business.objects.create(name='North Star Solar')
        self.user = User.objects.create_user(
            username='owner',
            password='Original-password-5172!',
            business=self.business,
            role=User.Role.OWNER,
        )
        Membership.objects.create(user=self.user, business=self.business, role=User.Role.OWNER)

    @override_settings(
        LOGIN_BACKOFF_FAILURE_THRESHOLD=1,
        LOGIN_BACKOFF_BASE_SECONDS=60,
        LOGIN_CAPTCHA_FAILURE_THRESHOLD=99,
    )
    def test_token_login_uses_progressive_backoff_after_failed_passwords(self):
        invalid_response = self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.user.username, 'password': 'wrong-password'},
            format='json',
        )
        delayed_response = self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.user.username, 'password': 'Original-password-5172!'},
            format='json',
        )

        self.assertEqual(invalid_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(delayed_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', delayed_response)

    @override_settings(
        LOGIN_BACKOFF_FAILURE_THRESHOLD=1,
        LOGIN_BACKOFF_BASE_SECONDS=0,
        LOGIN_CAPTCHA_FAILURE_THRESHOLD=1,
        TURNSTILE_SITE_KEY='test-site-key',
        TURNSTILE_SECRET_KEY='test-secret-key',
    )
    @patch('core.token_views.verify_turnstile', side_effect=lambda token, _ip: token == 'valid-captcha')
    def test_token_login_requires_captcha_only_after_a_failed_attempt(self, _verify_turnstile):
        self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.user.username, 'password': 'wrong-password'},
            format='json',
        )
        missing_captcha_response = self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.user.username, 'password': 'Original-password-5172!'},
            format='json',
        )
        verified_response = self.client.post(
            reverse('token_obtain_pair'),
            {
                'username': self.user.username,
                'password': 'Original-password-5172!',
                'captcha_token': 'valid-captcha',
            },
            format='json',
        )

        self.assertEqual(missing_captcha_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('captcha_token', missing_captcha_response.data)
        self.assertEqual(verified_response.status_code, status.HTTP_200_OK)


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'public-auth-throttle-tests',
        },
    },
)
class PublicAuthThrottlingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_every_public_auth_throttle_scope_has_a_default_rate(self):
        configured_rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']

        self.assertTrue(PUBLIC_AUTH_THROTTLE_TEST_RATES.keys() <= configured_rates.keys())

    @patch.object(ScopedRateThrottle, 'THROTTLE_RATES', PUBLIC_AUTH_THROTTLE_TEST_RATES)
    def test_every_public_auth_endpoint_returns_429_after_its_limit(self):
        endpoint_names = (
            'signup',
            'email_verify',
            'email_verify_resend',
            'password_reset_request',
            'password_reset_confirm',
            'token_obtain_pair',
            'token_refresh',
        )

        for endpoint_name in endpoint_names:
            with self.subTest(endpoint=endpoint_name):
                url = reverse(endpoint_name)
                for _ in range(2):
                    response = self.client.post(url, {}, format='json')
                    self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

                throttled_response = self.client.post(url, {}, format='json')

                self.assertEqual(throttled_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
                self.assertIn('Retry-After', throttled_response)
