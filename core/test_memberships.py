from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from leads.models import Lead

from .models import Business, Membership, User
from .tenancy import ACTIVE_BUSINESS_SESSION_KEY


class MembershipBackfillTests(TestCase):
    def test_management_command_creates_a_membership_for_legacy_users(self):
        business = Business.objects.create(name='Legacy Solar')
        user = User.objects.create_user(
            username='legacy-owner',
            password='test-password',
            business=business,
            role=User.Role.OWNER,
        )

        call_command('backfill_memberships')

        membership = Membership.objects.get(user=user, business=business)
        self.assertEqual(membership.role, User.Role.OWNER)
        self.assertTrue(membership.is_active)


class WebWorkspaceSwitchingTests(TestCase):
    def setUp(self):
        self.solar = Business.objects.create(name='North Star Solar')
        self.cctv = Business.objects.create(name='Bright CCTV')
        self.unavailable = Business.objects.create(name='Private HVAC')
        self.user = User.objects.create_user(
            username='shared-owner',
            password='test-password',
            business=self.solar,
            role=User.Role.OWNER,
        )
        Membership.objects.create(user=self.user, business=self.solar, role=User.Role.OWNER)
        Membership.objects.create(user=self.user, business=self.cctv, role=User.Role.SALESPERSON)
        self.cctv_teammate = User.objects.create_user(
            username='cctv-teammate',
            password='test-password',
            business=self.cctv,
            role=User.Role.SALESPERSON,
        )
        Membership.objects.create(
            user=self.cctv_teammate,
            business=self.cctv,
            role=User.Role.SALESPERSON,
        )
        Lead.objects.create(
            business=self.solar,
            customer_name='Solar-only lead',
            phone='03000000000',
            assigned_user=self.user,
        )
        Lead.objects.create(
            business=self.cctv,
            customer_name='CCTV-only lead',
            phone='03110000000',
            assigned_user=self.user,
        )
        self.client.force_login(self.user)

    def test_switching_changes_data_scope_and_workspace_role(self):
        response = self.client.post(
            reverse('web:workspace-switch'),
            {'business_id': str(self.cctv.id)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('web:dashboard'))
        self.assertEqual(self.client.session[ACTIVE_BUSINESS_SESSION_KEY], str(self.cctv.id))

        leads_response = self.client.get(reverse('web:lead-list'))
        self.assertEqual(leads_response.status_code, 200)
        self.assertContains(leads_response, 'CCTV-only lead')
        self.assertNotContains(leads_response, 'Solar-only lead')
        self.assertFalse(leads_response.context['is_owner'])
        self.assertTrue(leads_response.context['is_salesperson'])
        self.assertEqual(
            self.client.get(reverse('web:business-settings')).status_code,
            403,
        )

    def test_switch_rejects_a_business_without_membership(self):
        self.client.get(reverse('web:dashboard'))

        response = self.client.post(
            reverse('web:workspace-switch'),
            {'business_id': str(self.unavailable.id)},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.session[ACTIVE_BUSINESS_SESSION_KEY], str(self.solar.id))

    def test_workspace_switch_confirmation_is_a_dismissible_topbar_toast(self):
        response = self.client.post(
            reverse('web:workspace-switch'),
            {'business_id': str(self.cctv.id)},
            follow=True,
        )

        self.assertContains(response, 'You are now working in Bright CCTV.')
        self.assertContains(response, 'class="flash-stack"')
        self.assertContains(response, 'data-flash-dismiss')


class OwnerBusinessCreationTests(TestCase):
    def setUp(self):
        self.original_business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(
            username='workspace-owner',
            password='test-password',
            business=self.original_business,
            role=User.Role.OWNER,
        )
        Membership.objects.create(
            user=self.owner,
            business=self.original_business,
            role=User.Role.OWNER,
        )
        self.salesperson = User.objects.create_user(
            username='workspace-salesperson',
            password='test-password',
            business=self.original_business,
            role=User.Role.SALESPERSON,
        )
        Membership.objects.create(
            user=self.salesperson,
            business=self.original_business,
            role=User.Role.SALESPERSON,
        )

    def test_owner_can_create_a_new_business_and_is_switched_into_it(self):
        self.client.force_login(self.owner)

        form_response = self.client.get(reverse('web:business-create'))
        response = self.client.post(
            reverse('web:business-create'),
            {
                'name': 'Adeel CCTV',
                'industry': Business.Industry.CCTV,
                'timezone': 'Asia/Karachi',
            },
        )

        created_business = Business.objects.get(name='Adeel CCTV')
        membership = Membership.objects.get(user=self.owner, business=created_business)
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, 'Create business')
        self.assertRedirects(response, reverse('web:dashboard'))
        self.assertEqual(membership.role, User.Role.OWNER)
        self.assertTrue(membership.is_active)
        self.assertEqual(self.client.session[ACTIVE_BUSINESS_SESSION_KEY], str(created_business.id))
        # The old one-to-one compatibility key stays on the original business;
        # active workspace resolution comes from membership + session.
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.business, self.original_business)

    def test_salesperson_cannot_open_the_business_creation_flow(self):
        self.client.force_login(self.salesperson)

        response = self.client.get(reverse('web:business-create'))

        self.assertEqual(response.status_code, 403)


class ApiWorkspaceTokenTests(APITestCase):
    def setUp(self):
        self.solar = Business.objects.create(name='North Star Solar')
        self.cctv = Business.objects.create(name='Bright CCTV')
        self.unavailable = Business.objects.create(name='Private HVAC')
        self.user = User.objects.create_user(
            username='shared-api-user',
            password='test-password',
            business=self.solar,
            role=User.Role.OWNER,
        )
        Membership.objects.create(user=self.user, business=self.solar, role=User.Role.OWNER)
        Membership.objects.create(user=self.user, business=self.cctv, role=User.Role.SALESPERSON)
        self.cctv_teammate = User.objects.create_user(
            username='api-cctv-teammate',
            password='test-password',
            business=self.cctv,
            role=User.Role.SALESPERSON,
        )
        Membership.objects.create(
            user=self.cctv_teammate,
            business=self.cctv,
            role=User.Role.SALESPERSON,
        )
        Lead.objects.create(
            business=self.solar,
            customer_name='API solar lead',
            phone='03000000000',
            assigned_user=self.user,
        )
        Lead.objects.create(
            business=self.cctv,
            customer_name='API CCTV lead',
            phone='03110000000',
            assigned_user=self.user,
        )

    def test_multi_workspace_token_requires_and_enforces_business_choice(self):
        token_url = reverse('token_obtain_pair')
        no_choice = self.client.post(
            token_url,
            {'username': self.user.username, 'password': 'test-password'},
            format='json',
        )
        self.assertEqual(no_choice.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('business_id', no_choice.data)

        response = self.client.post(
            token_url,
            {
                'username': self.user.username,
                'password': 'test-password',
                'business_id': str(self.cctv.id),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

        me = self.client.get(reverse('current_user'))
        leads = self.client.get(reverse('lead-list'))

        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data['business']['id'], str(self.cctv.id))
        self.assertEqual(me.data['role'], User.Role.SALESPERSON)
        self.assertEqual(leads.status_code, status.HTTP_200_OK)
        self.assertEqual(leads.data['count'], 1)
        self.assertEqual(leads.data['results'][0]['customer_name'], 'API CCTV lead')

    def test_token_cannot_be_requested_for_a_business_without_membership(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            {
                'username': self.user.username,
                'password': 'test-password',
                'business_id': str(self.unavailable.id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('business_id', response.data)
