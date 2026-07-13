from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Business, User

from .models import Lead, Product


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
        self.assertEqual(response.data['results'][0]['id'], self.lead.id)

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

# Create your tests here.
