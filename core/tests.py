from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Business, User


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
        self.assertEqual(response.data['business']['id'], self.business.id)
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

    def test_signup_creates_a_business_and_owner(self):
        response = self.client.post(
            reverse('signup'),
            {
                'business_name': 'Skyline AC',
                'username': 'skyline-owner',
                'password': 'Strong-test-password-123',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['user']['role'], User.Role.OWNER)
        self.assertEqual(response.data['user']['business']['name'], 'Skyline AC')
        self.assertIn('access', response.data)

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
