from django.contrib.auth.hashers import make_password
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Business, PendingRegistration, User


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


class BusinessAndTeamApiTests(APITestCase):
    def setUp(self):
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
