import base64
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Business, PasswordResetRequest, PendingRegistration, User
from followups.models import FollowUpTask
from leads.models import Lead, Product
from web.views import DashboardView


ONE_PIXEL_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGM8EmDBwMDAxMDAwMDAAAAQRgFQfoqMXQAAAABJRU5ErkJggg=='
)


class RejectingAuthenticationBackend:
    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        return None


class LoginPageTests(SimpleTestCase):
    def test_password_field_has_an_accessible_visibility_toggle(self):
        response = self.client.get(reverse('web:login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<label for="login-password">Password</label>', html=True)
        self.assertContains(response, 'id="login-password"')
        self.assertContains(response, 'data-password-toggle')
        self.assertContains(response, 'aria-controls="login-password"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, 'web/app.js')
        self.assertContains(response, reverse('web:password-reset-request'))

    @override_settings(
        AUTHENTICATION_BACKENDS=('web.tests.RejectingAuthenticationBackend',),
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'login-page-tests',
            },
        },
    )
    def test_invalid_credentials_error_is_below_the_password_field(self):
        response = self.client.post(reverse('web:login'), {'username': 'wrong', 'password': 'wrong'})
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="login-credentials-error"')
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'aria-describedby="login-credentials-error"', count=2)
        self.assertLess(content.index('id="login-password"'), content.index('id="login-credentials-error"'))


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'browser-auth-security-tests',
        },
    },
    BROWSER_AUTH_THROTTLE_RATES={
        'web_signup': '2/minute',
        'web_login': '10/minute',
    },
)
class BrowserAuthSecurityTests(TestCase):
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

    def test_signup_form_is_throttled_before_a_third_post(self):
        self.client.post(reverse('web:signup'), {})
        self.client.post(reverse('web:signup'), {})
        throttled_response = self.client.post(reverse('web:signup'), {})

        self.assertRedirects(
            throttled_response,
            reverse('web:signup'),
            fetch_redirect_response=False,
        )

    @override_settings(
        LOGIN_BACKOFF_FAILURE_THRESHOLD=1,
        LOGIN_BACKOFF_BASE_SECONDS=0,
        LOGIN_CAPTCHA_FAILURE_THRESHOLD=1,
        TURNSTILE_SITE_KEY='test-site-key',
        TURNSTILE_SECRET_KEY='test-secret-key',
    )
    @patch('web.forms.verify_turnstile', side_effect=lambda token, _ip: token == 'valid-captcha')
    def test_login_shows_captcha_only_after_a_failed_attempt(self, _verify_turnstile):
        failed_response = self.client.post(
            reverse('web:login'),
            {'username': self.user.username, 'password': 'wrong-password'},
        )
        missing_captcha_response = self.client.post(
            reverse('web:login'),
            {'username': self.user.username, 'password': 'Original-password-5172!'},
        )
        verified_response = self.client.post(
            reverse('web:login'),
            {
                'username': self.user.username,
                'password': 'Original-password-5172!',
                'captcha_token': 'valid-captcha',
            },
        )

        self.assertEqual(failed_response.status_code, 200)
        self.assertContains(failed_response, 'cf-turnstile')
        self.assertContains(missing_captcha_response, 'Please complete the security check')
        self.assertEqual(verified_response.status_code, 302)


class ProfileAndAvatarTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='owner',
            password='test-password',
            first_name='Adeel',
            last_name='Khan',
            email='owner@northstar.example',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.teammate = User.objects.create_user(
            username='taken-name',
            password='test-password',
            first_name='Sara',
            last_name='Brown',
            email='sara@northstar.example',
            business=self.business,
            role=User.Role.SALESPERSON,
            profile_picture='profile_pictures/sara.png',
        )
        self.client.force_login(self.owner)

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_profile_updates_the_signed_in_user_and_saves_a_picture(self):
        response = self.client.post(
            reverse('web:profile'),
            {
                'first_name': 'Adeel',
                'last_name': 'Ahmed',
                'email': 'adeel@northstar.example',
                'username': 'adeel-ahmed',
                'phone': '0300 1234567',
                'profile_picture': SimpleUploadedFile('portrait.png', ONE_PIXEL_PNG, content_type='image/png'),
            },
        )

        self.owner.refresh_from_db()
        self.assertRedirects(response, reverse('web:account-settings-profile'))
        self.assertEqual(self.owner.username, 'adeel-ahmed')
        self.assertEqual(self.owner.email, 'adeel@northstar.example')
        self.assertTrue(self.owner.profile_picture.name.startswith('profile_pictures/'))

    def test_profile_rejects_a_username_that_is_already_taken(self):
        response = self.client.post(
            reverse('web:profile'),
            {
                'first_name': self.owner.first_name,
                'last_name': self.owner.last_name,
                'email': self.owner.email,
                'username': self.teammate.username,
                'phone': '',
            },
        )

        self.owner.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner.username, 'owner')
        self.assertContains(response, 'This username is already in use.')

    def test_assignee_picker_shows_profile_photos_and_the_fallback_avatar(self):
        response = self.client.get(reverse('web:lead-create'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_owner'])
        self.assertContains(response, 'assignee-dropdown')
        self.assertContains(response, 'Assign to me')
        self.assertContains(response, 'Sara Brown')
        self.assertContains(response, 'profile_pictures/sara.png')
        self.assertContains(response, 'default-profile-avatar.svg')
        self.assertContains(
            response,
            f'name="assigned_user" value="{self.teammate.pk}"',
        )
        self.assertContains(response, reverse('web:team-list'))
        self.assertContains(response, reverse('web:product-list'))
        self.assertContains(response, reverse('web:business-settings'))

    def test_owner_can_assign_a_new_lead_from_the_dropdown(self):
        response = self.client.post(
            reverse('web:lead-create'),
            {
                'customer_name': 'New customer',
                'phone': '03000000000',
                'source': Lead.Source.PHONE_CALL,
                'assigned_user': self.teammate.pk,
            },
        )

        lead = Lead.objects.get(customer_name='New customer')
        self.assertRedirects(response, reverse('web:lead-detail', args=[lead.pk]))
        self.assertEqual(lead.assigned_user, self.teammate)

    def test_account_menu_and_management_status_switches_are_available(self):
        profile_response = self.client.get(reverse('web:account-settings-profile'))
        security_response = self.client.get(reverse('web:security-settings'))
        team_response = self.client.get(reverse('web:team-list'))
        service_response = self.client.get(reverse('web:product-list'))

        self.assertContains(profile_response, 'Save profile')
        self.assertContains(profile_response, 'Settings')
        self.assertContains(profile_response, 'Personal information')
        self.assertContains(profile_response, reverse('web:security-settings'))
        self.assertContains(security_response, 'Password')
        self.assertContains(security_response, 'Forgot your current password?')
        self.assertContains(profile_response, 'Log out')
        self.assertContains(profile_response, 'Workspace settings')
        self.assertNotContains(profile_response, '<span>Settings</span>')
        self.assertContains(team_response, 'status-toggle')
        self.assertContains(service_response, 'status-toggle')

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'profile-password-token-revocation-tests',
            },
        },
    )
    def test_signed_in_user_can_change_password_from_security_settings_and_keep_their_session(self):
        security_response = self.client.get(reverse('web:security-settings'))
        refresh = RefreshToken.for_user(self.owner)

        self.assertContains(security_response, 'Password')
        self.assertContains(security_response, 'Forgot your current password?')
        self.assertContains(security_response, reverse('web:security-password-change'))

        response = self.client.post(
            reverse('web:security-password-change'),
            {
                'current_password': 'test-password',
                'new_password': 'Profile-new-passphrase-5172!',
                'new_password_confirmation': 'Profile-new-passphrase-5172!',
            },
        )

        self.owner.refresh_from_db()
        self.assertRedirects(response, reverse('web:security-settings'))
        self.assertTrue(self.owner.check_password('Profile-new-passphrase-5172!'))
        self.assertEqual(self.client.get(reverse('web:security-settings')).status_code, 200)
        refresh_response = self.client.post(reverse('token_refresh'), {'refresh': str(refresh)})
        self.assertEqual(refresh_response.status_code, 401)

    def test_security_password_change_rejects_an_incorrect_current_password(self):
        response = self.client.post(
            reverse('web:security-password-change'),
            {
                'current_password': 'incorrect-password',
                'new_password': 'Profile-new-passphrase-5172!',
                'new_password_confirmation': 'Profile-new-passphrase-5172!',
            },
        )

        self.owner.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your current password is incorrect.')
        self.assertTrue(self.owner.check_password('test-password'))


class BusinessSettingsTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name='North Star Solar',
            industry=Business.Industry.SOLAR,
            timezone='Asia/Karachi',
        )
        self.owner = User.objects.create_user(
            username='owner',
            password='test-password',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.client.force_login(self.owner)

    def test_business_details_are_wider_and_offer_grouped_timezone_choices(self):
        response = self.client.get(reverse('web:business-settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'business-settings-layout')
        self.assertContains(response, 'Business details')
        self.assertContains(response, 'Time zone')
        self.assertContains(response, 'Choose the time zone where your team normally works.')
        self.assertContains(response, '<optgroup label="Asia">')
        self.assertContains(response, 'Asia/Karachi')

    def test_owner_can_choose_a_timezone_from_the_dropdown(self):
        response = self.client.post(
            reverse('web:business-settings'),
            {
                'name': 'North Star Solar',
                'industry': Business.Industry.SOLAR,
                'timezone': 'Europe/London',
            },
        )

        self.business.refresh_from_db()
        self.assertRedirects(response, reverse('web:business-settings'))
        self.assertEqual(self.business.timezone, 'Europe/London')

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(
            reverse('web:business-settings'),
            {
                'name': self.business.name,
                'industry': self.business.industry,
                'timezone': 'Not/A-Timezone',
            },
        )

        self.business.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice.')
        self.assertEqual(self.business.timezone, 'Asia/Karachi')


class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='owner',
            password='test-password',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.other_owner = User.objects.create_user(
            username='other-owner',
            password='test-password',
            business=self.other_business,
            role=User.Role.OWNER,
        )
        self.client.force_login(self.owner)

    def test_analytics_are_built_from_the_current_business_only(self):
        Lead.objects.create(
            business=self.business,
            customer_name='Ayesha',
            phone='03000000000',
            assigned_user=self.owner,
            source=Lead.Source.REFERRAL,
            stage=Lead.Stage.WON,
            closed_at=timezone.now(),
        )
        Lead.objects.create(
            business=self.business,
            customer_name='Bilal',
            phone='03110000000',
            assigned_user=self.owner,
            source=Lead.Source.WEBSITE,
            stage=Lead.Stage.NEW_INQUIRY,
        )
        Lead.objects.create(
            business=self.other_business,
            customer_name='Private lead',
            phone='03220000000',
            assigned_user=self.other_owner,
            source=Lead.Source.FACEBOOK,
            stage=Lead.Stage.WON,
            closed_at=timezone.now(),
        )

        response = self.client.get(reverse('web:dashboard'))

        self.assertEqual(response.status_code, 200)
        stage_rows = {row['value']: row for row in response.context['analytics_stage_rows']}
        source_rows = {row['source']: row for row in response.context['analytics_source_rows']}
        self.assertEqual(stage_rows[Lead.Stage.WON]['total'], 1)
        self.assertEqual(stage_rows[Lead.Stage.NEW_INQUIRY]['total'], 1)
        self.assertEqual(source_rows[Lead.Source.REFERRAL]['total'], 1)
        self.assertEqual(source_rows[Lead.Source.WEBSITE]['total'], 1)
        self.assertNotIn(Lead.Source.FACEBOOK, source_rows)
        self.assertEqual(response.context['analytics_win_rate'], 100)
        self.assertEqual(response.context['lead_trend_total'], 2)
        self.assertEqual(len(response.context['lead_trend_days']), 14)
        self.assertEqual(sum(day['count'] for day in response.context['lead_trend_days']), 2)
        self.assertContains(response, 'See the shape of your pipeline.')
        self.assertContains(response, 'New leads over time')
        self.assertContains(response, 'lead-trend-chart')
        self.assertContains(response, 'data-lead-trend-point')
        self.assertContains(response, 'data-lead-trend-tooltip')
        self.assertNotContains(response, '<title id="lead-trend-chart-title">')
        self.assertContains(response, 'data-live-clock')
        self.assertContains(response, 'data-dashboard-greeting')
        self.assertContains(response, 'data-sidebar-toggle')

    def test_dashboard_greeting_matches_the_local_hour(self):
        self.assertEqual(DashboardView.greeting_for_hour(6), 'Good morning')
        self.assertEqual(DashboardView.greeting_for_hour(12), 'Good afternoon')
        self.assertEqual(DashboardView.greeting_for_hour(16), 'Good afternoon')
        self.assertEqual(DashboardView.greeting_for_hour(17), 'Good evening')
        self.assertEqual(DashboardView.greeting_for_hour(18), 'Good evening')


class ReportsSalespersonPaginationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='owner',
            password='test-password',
            business=self.business,
            role=User.Role.OWNER,
        )
        self.client.force_login(self.owner)

    def test_salespeople_are_ranked_by_conversion_and_shown_ten_at_a_time(self):
        for salesperson_number in range(12):
            salesperson = User.objects.create_user(
                username=f'agent-{salesperson_number}',
                first_name='Agent',
                last_name=str(salesperson_number),
                password='test-password',
                business=self.business,
                role=User.Role.SALESPERSON,
            )
            for lead_number in range(12):
                Lead.objects.create(
                    business=self.business,
                    customer_name=f'Customer {salesperson_number}-{lead_number}',
                    phone=f'03{salesperson_number:02d}{lead_number:07d}',
                    assigned_user=salesperson,
                    stage=Lead.Stage.WON if lead_number < salesperson_number else Lead.Stage.NEW_INQUIRY,
                    closed_at=timezone.now() if lead_number < salesperson_number else None,
                )

        response = self.client.get(reverse('web:reports'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['salesperson_total_count'], 12)
        self.assertEqual(response.context['salesperson_visible_count'], 10)
        self.assertTrue(response.context['salesperson_has_more'])
        self.assertEqual(response.context['salesperson_more_count'], 2)
        self.assertEqual(
            [row['label'] for row in response.context['salesperson_rows']],
            [f'Agent {number}' for number in range(11, 1, -1)],
        )
        self.assertContains(response, 'Showing 10 of 12 salespeople')
        self.assertContains(response, '?salespeople_limit=12#salesperson-conversion')
        self.assertContains(response, 'The best follow-up is the one your customer never has to chase.')

        expanded_response = self.client.get(reverse('web:reports'), {'salespeople_limit': 20})

        self.assertEqual(expanded_response.context['salesperson_visible_count'], 12)
        self.assertFalse(expanded_response.context['salesperson_has_more'])
        self.assertTrue(expanded_response.context['salesperson_can_collapse'])
        self.assertEqual(len(expanded_response.context['salesperson_rows']), 12)
        self.assertContains(expanded_response, '?salespeople_limit=10#salesperson-conversion')


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'email-verification-web-tests',
        },
    },
)
class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.registration = PendingRegistration.objects.create(
            business_name='North Star Solar',
            industry=Business.Industry.SOLAR,
            timezone='Asia/Karachi',
            username='owner',
            email='owner@northstar.example',
            password=make_password('test-password'),
            verification_code_hash=make_password('123456'),
            verification_sent_at=timezone.now(),
        )

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_web_signup_sends_a_verification_email_and_waits_for_confirmation(self):
        response = self.client.post(
            reverse('web:signup'),
            {
                'business_name': 'Skyline AC',
                'industry': Business.Industry.AC_INSTALLATION,
                'owner_name': 'Skyline Owner',
                'email': 'owner@skyline.example',
                'password': 'Strong-test-password-123',
            },
        )

        registration = PendingRegistration.objects.get(email='owner@skyline.example')
        self.assertRedirects(response, reverse('web:email-verification-sent'))
        self.assertEqual(registration.business_name, 'Skyline AC')
        self.assertFalse(User.objects.filter(email='owner@skyline.example').exists())
        self.assertFalse(Business.objects.filter(name='Skyline AC').exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('six-digit code', mail.outbox[0].body)
        self.assertRegex(mail.outbox[0].body, r'\b\d{6}\b')
        self.assertNotIn('/verify-email/', mail.outbox[0].body)

    def test_verification_code_activates_the_owner_and_starts_their_session(self):
        response = self.client.post(
            reverse('web:email-verify'),
            {'email': 'owner@northstar.example', 'code': '123456'},
        )

        owner = User.objects.get(email='owner@northstar.example')
        self.assertRedirects(response, reverse('web:onboarding'))
        self.assertTrue(owner.is_active)
        self.assertIsNotNone(owner.email_verified_at)
        self.assertEqual(owner.business.name, 'North Star Solar')
        self.assertFalse(PendingRegistration.objects.filter(pk=self.registration.pk).exists())
        self.assertEqual(self.client.session.get('_auth_user_id'), str(owner.pk))

    def test_used_code_cannot_create_a_second_workspace(self):
        first_response = self.client.post(
            reverse('web:email-verify'),
            {'email': 'owner@northstar.example', 'code': '123456'},
        )
        self.client.logout()

        response = self.client.post(
            reverse('web:email-verify'),
            {'email': 'owner@northstar.example', 'code': '123456'},
        )

        # The client is deliberately logged out before this assertion, so do
        # not follow the first response's protected onboarding redirect.
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(first_response.url, reverse('web:onboarding'))
        self.assertRedirects(response, reverse('web:email-verification-sent'))
        self.assertEqual(Business.objects.filter(name='North Star Solar').count(), 1)

    def test_five_incorrect_codes_lock_the_registration_until_resend(self):
        for _ in range(5):
            self.client.post(
                reverse('web:email-verify'),
                {'email': 'owner@northstar.example', 'code': '000000'},
            )

        self.registration.refresh_from_db()
        correct_code_response = self.client.post(
            reverse('web:email-verify'),
            {'email': 'owner@northstar.example', 'code': '123456'},
        )

        self.assertEqual(self.registration.verification_attempts, 5)
        self.assertRedirects(correct_code_response, reverse('web:email-verification-sent'))
        self.assertFalse(User.objects.filter(email='owner@northstar.example').exists())


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'password-reset-web-tests',
        },
    },
)
class PasswordResetWebTests(TestCase):
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
    def test_requesting_a_reset_sends_a_code_and_opens_the_confirmation_form(self):
        request_page = self.client.get(reverse('web:password-reset-request'))

        self.assertEqual(request_page.status_code, 200)
        self.assertContains(request_page, 'Send reset code')

        response = self.client.post(
            reverse('web:password-reset-request'),
            {'email': self.user.email},
        )

        self.assertRedirects(response, reverse('web:password-reset-confirm'))
        self.assertEqual(self.client.session['password_reset_email'], self.user.email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('six-digit code', mail.outbox[0].body)
        self.assertTrue(PasswordResetRequest.objects.filter(user=self.user).exists())

        confirmation_page = self.client.get(reverse('web:password-reset-confirm'))
        self.assertContains(confirmation_page, self.user.email)
        self.assertContains(confirmation_page, 'Reset password')

    def test_valid_code_sets_a_new_password_and_returns_to_sign_in(self):
        PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now(),
        )
        session = self.client.session
        session['password_reset_email'] = self.user.email
        session.save()

        response = self.client.post(
            reverse('web:password-reset-confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Unique-reset-passphrase-5172!',
                'new_password_confirmation': 'Unique-reset-passphrase-5172!',
            },
        )

        self.user.refresh_from_db()
        self.assertRedirects(response, reverse('web:login'))
        self.assertTrue(self.user.check_password('Unique-reset-passphrase-5172!'))
        self.assertFalse(PasswordResetRequest.objects.filter(user=self.user).exists())

    def test_signed_in_user_can_recover_with_a_code_and_stay_signed_in(self):
        self.client.force_login(self.user)
        request_page = self.client.get(reverse('web:password-reset-request'))

        self.assertContains(request_page, f'value="{self.user.email}"')

        PasswordResetRequest.objects.create(
            user=self.user,
            code_hash=make_password('123456'),
            sent_at=timezone.now(),
        )
        session = self.client.session
        session['password_reset_email'] = self.user.email
        session.save()

        response = self.client.post(
            reverse('web:password-reset-confirm'),
            {
                'email': self.user.email,
                'code': '123456',
                'new_password': 'Recovered-while-signed-in-5172!',
                'new_password_confirmation': 'Recovered-while-signed-in-5172!',
            },
        )

        self.user.refresh_from_db()
        self.assertRedirects(response, reverse('web:security-settings'))
        self.assertTrue(self.user.check_password('Recovered-while-signed-in-5172!'))
        self.assertEqual(self.client.get(reverse('web:security-settings')).status_code, 200)


class LeadBoardPaginationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='owner', password='test-password', business=self.business, role=User.Role.OWNER,
        )
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.other_owner = User.objects.create_user(
            username='other-owner', password='test-password', business=self.other_business, role=User.Role.OWNER,
        )
        self.product = Product.objects.create(business=self.business, name='Solar installation')
        self.client.force_login(self.owner)

    def create_lead(self, number, **overrides):
        values = {
            'business': self.business,
            'customer_name': f'Board lead {number:02d}',
            'phone': f'0300{number:07d}',
            'assigned_user': self.owner,
            'stage': Lead.Stage.NEW_INQUIRY,
            'product': self.product,
            'last_activity_at': timezone.now() - timedelta(minutes=number),
        }
        values.update(overrides)
        return Lead.objects.create(**values)

    def test_board_only_renders_the_first_ten_cards_for_each_stage(self):
        for number in range(12):
            self.create_lead(number)
        Lead.objects.create(
            business=self.other_business,
            customer_name='Private board lead',
            phone='03110000000',
            assigned_user=self.other_owner,
            stage=Lead.Stage.NEW_INQUIRY,
        )

        response = self.client.get(reverse('web:lead-list'))
        new_inquiry = next(
            column for column in response.context['pipeline_columns'] if column['value'] == Lead.Stage.NEW_INQUIRY
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(new_inquiry['total'], 12)
        self.assertEqual(new_inquiry['shown'], 10)
        self.assertEqual(len(new_inquiry['leads']), 10)
        self.assertContains(response, 'Showing 10 of 12')
        self.assertContains(response, 'data-load-more')
        self.assertContains(response, 'View all')
        self.assertContains(response, 'SHARED PIPELINE')
        self.assertContains(response, 'Quick add')
        self.assertContains(response, 'class="lead-filter"')
        self.assertContains(response, 'lead-summary-strip')
        self.assertContains(response, 'kanban-stage-1')
        self.assertNotContains(response, 'Board lead 10')

    def test_stage_list_is_paginated_and_applies_server_side_filters(self):
        for number in range(28):
            self.create_lead(number, source=Lead.Source.REFERRAL if number % 2 else Lead.Source.WEBSITE)
        self.create_lead(99, customer_name='Specific matching lead', source=Lead.Source.WEBSITE)
        Lead.objects.create(
            business=self.other_business,
            customer_name='Specific matching lead',
            phone='03110000000',
            assigned_user=self.other_owner,
            stage=Lead.Stage.NEW_INQUIRY,
            source=Lead.Source.WEBSITE,
        )
        url = reverse('web:lead-stage-list', args=[Lead.Stage.NEW_INQUIRY])

        page_two = self.client.get(url, {'page': 2, 'ordering': 'customer_name'})
        filtered = self.client.get(url, {'q': 'Specific matching', 'source': Lead.Source.WEBSITE})

        self.assertEqual(page_two.status_code, 200)
        self.assertEqual(page_two.context['page_obj'].paginator.count, 29)
        self.assertEqual(page_two.context['page_obj'].number, 2)
        self.assertContains(page_two, 'Page 2 of 2')
        self.assertEqual(filtered.context['page_obj'].paginator.count, 1)
        self.assertContains(filtered, 'Specific matching lead')


class TaskListPaginationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='owner', password='test-password', business=self.business, role=User.Role.OWNER,
        )
        self.lead = Lead.objects.create(
            business=self.business,
            customer_name='Ayesha',
            phone='03000000000',
            assigned_user=self.owner,
        )
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.other_owner = User.objects.create_user(
            username='other-owner', password='test-password', business=self.other_business, role=User.Role.OWNER,
        )
        self.other_lead = Lead.objects.create(
            business=self.other_business,
            customer_name='Private lead',
            phone='03110000000',
            assigned_user=self.other_owner,
        )
        self.client.force_login(self.owner)

    def create_task(self, number, status=FollowUpTask.Status.PENDING, **overrides):
        values = {
            'business': self.business,
            'lead': self.lead,
            'assigned_user': self.owner,
            'description': f'Follow-up task {number:02d}',
            'due_at': timezone.now() + timedelta(minutes=number),
            'status': status,
        }
        values.update(overrides)
        return FollowUpTask.objects.create(**values)

    def test_task_list_paginates_ten_open_tasks_and_keeps_status_filter(self):
        for number in range(12):
            self.create_task(number)
        for number in range(12, 15):
            self.create_task(number, status=FollowUpTask.Status.OVERDUE)
        FollowUpTask.objects.create(
            business=self.other_business,
            lead=self.other_lead,
            assigned_user=self.other_owner,
            description='Private follow-up task',
            due_at=timezone.now(),
        )

        page_one = self.client.get(reverse('web:task-list'))
        page_two = self.client.get(reverse('web:task-list'), {'page': 2})
        pending_page_two = self.client.get(reverse('web:task-list'), {'status': 'pending', 'page': 2})

        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(page_one.context['page_obj'].paginator.per_page, 10)
        self.assertEqual(page_one.context['page_obj'].paginator.count, 15)
        self.assertEqual(len(page_one.context['tasks']), 10)
        self.assertEqual(page_one.context['task_counts']['overdue'], 3)
        self.assertContains(page_one, 'task-workspace')
        self.assertContains(page_one, 'task-filter-tabs')
        self.assertNotContains(page_one, 'Follow-up task 10')
        self.assertEqual(page_two.context['page_obj'].number, 2)
        self.assertContains(page_two, 'Follow-up task 10')
        self.assertNotContains(page_two, 'Private follow-up task')
        self.assertEqual(pending_page_two.context['page_obj'].paginator.count, 12)
        self.assertContains(pending_page_two, 'Showing 11–12 of 12')
        self.assertContains(pending_page_two, 'status=pending')
