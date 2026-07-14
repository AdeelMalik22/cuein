from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Business, User

from .models import Lead, Product


TEST_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'lead-api-tests',
    },
}


class LeadApiTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.owner = User.objects.create_user(
            username='owner', password='test-password', business=self.business, role=User.Role.OWNER,
        )
        self.salesperson = User.objects.create_user(
            username='sales', password='test-password', business=self.business, role=User.Role.SALESPERSON,
        )
        self.other_user = User.objects.create_user(
            username='other', password='test-password', business=self.other_business, role=User.Role.OWNER,
        )
        self.product = Product.objects.create(business=self.business, name='Solar installation')
        self.other_product = Product.objects.create(business=self.other_business, name='CCTV installation')
        self.lead = Lead.objects.create(
            business=self.business, customer_name='Ali', phone='03000000000', assigned_user=self.salesperson,
        )
        self.other_lead = Lead.objects.create(
            business=self.other_business, customer_name='Sara', phone='03110000000', assigned_user=self.other_user,
        )

    def test_owner_cannot_retrieve_another_business_lead(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse('lead-detail', args=[self.other_lead.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_salesperson_sees_only_assigned_leads(self):
        self.client.force_authenticate(self.salesperson)

        response = self.client.get(reverse('lead-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(self.lead.id))

    def test_product_from_another_business_cannot_be_used(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('lead-list'),
            {'customer_name': 'Hassan', 'phone': '03220000000', 'product': self.other_product.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lost_transition_requires_reason(self):
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(
            reverse('lead-transition', args=[self.lead.id]),
            {'stage': Lead.Stage.LOST},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_assign_lead_to_business_user(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('lead-assign', args=[self.lead.id]),
            {'assigned_user': self.owner.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_user, self.owner)


@override_settings(CACHES=TEST_CACHES)
class LeadKanbanApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.business = Business.objects.create(name='North Star Solar')
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.owner = User.objects.create_user(
            username='owner', password='test-password', business=self.business, role=User.Role.OWNER,
        )
        self.other_owner = User.objects.create_user(
            username='other-owner', password='test-password', business=self.other_business, role=User.Role.OWNER,
        )
        self.product = Product.objects.create(business=self.business, name='Solar installation')
        now = timezone.now()
        self.leads = [
            Lead.objects.create(
                business=self.business,
                customer_name=f'Kanban lead {number:02d}',
                phone=f'0300{number:07d}',
                product=self.product,
                assigned_user=self.owner,
                stage=Lead.Stage.NEW_INQUIRY,
                last_activity_at=now - timedelta(minutes=number),
            )
            for number in range(23)
        ]
        Lead.objects.create(
            business=self.other_business,
            customer_name='Private kanban lead',
            phone='03110000000',
            assigned_user=self.other_owner,
            stage=Lead.Stage.NEW_INQUIRY,
        )
        self.url = reverse('lead-kanban')

    def test_kanban_returns_tenant_scoped_ten_card_pages_without_contact_fields(self):
        self.client.force_authenticate(self.owner)

        first_page = self.client.get(self.url, {'stage': Lead.Stage.NEW_INQUIRY, 'limit': 10, 'offset': 0})
        second_page = self.client.get(self.url, {'stage': Lead.Stage.NEW_INQUIRY, 'limit': 10, 'offset': 10})

        self.assertEqual(first_page.status_code, status.HTTP_200_OK)
        self.assertEqual(first_page.data['count'], 23)
        self.assertEqual(len(first_page.data['results']), 10)
        self.assertEqual(len(second_page.data['results']), 10)
        self.assertTrue(first_page.data['next'])
        self.assertEqual(
            set(first_page.data['results'][0]),
            {
                'id', 'customer_name', 'product_name', 'stage', 'quoted_price', 'last_activity_at',
                'detail_url', 'transition_url',
            },
        )
        self.assertNotIn(
            first_page.data['results'][0]['id'],
            {lead['id'] for lead in second_page.data['results']},
        )

    def test_kanban_accepts_the_logged_in_web_session(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url, {'stage': Lead.Stage.NEW_INQUIRY, 'limit': 10})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 23)

    def test_lead_creation_invalidates_the_cached_kanban_count(self):
        self.client.force_authenticate(self.owner)
        self.client.get(self.url, {'stage': Lead.Stage.NEW_INQUIRY, 'limit': 10})

        with self.captureOnCommitCallbacks(execute=True):
            created = self.client.post(
                reverse('lead-list'),
                {'customer_name': 'Fresh cached lead', 'phone': '03220000000'},
                format='json',
            )
        refreshed = self.client.get(self.url, {'stage': Lead.Stage.NEW_INQUIRY, 'limit': 10})

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)
        self.assertEqual(refreshed.data['count'], 24)

# Create your tests here.
