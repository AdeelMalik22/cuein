from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Business, User
from followups.models import FollowUpTask

from .models import Activity, Lead, Product, SiteVisit


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

    def test_product_name_is_unique_case_insensitively_per_business(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(business=self.business, name='SOLAR INSTALLATION')

    def test_product_api_rejects_a_case_insensitive_duplicate_name(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('product-list'),
            {'name': 'SOLAR INSTALLATION'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', response.data)

    def test_api_lead_creation_adds_a_timeline_event(self):
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('lead-list'),
            {'customer_name': 'New customer', 'phone': '03220000000'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Activity.objects.filter(
            lead_id=response.data['id'],
            kind=Activity.Kind.SYSTEM,
            content='Lead captured.',
            created_by=self.owner,
        ).exists())

    def test_activity_api_creates_a_manual_event_and_updates_the_lead(self):
        self.client.force_authenticate(self.salesperson)
        previous_activity_at = self.lead.last_activity_at

        response = self.client.post(
            reverse('activity-list'),
            {
                'lead': str(self.lead.id),
                'kind': Activity.Kind.CALL,
                'content': 'Called to confirm the site visit.',
                'metadata': {'spoofed': 'value'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('spoofed', response.data['metadata'])
        self.assertEqual(response.data['created_by'], str(self.salesperson.id))
        activity = Activity.objects.get(pk=response.data['id'])
        self.assertEqual(activity.kind, Activity.Kind.CALL)
        self.assertEqual(activity.created_by, self.salesperson)
        self.assertEqual(activity.metadata, {})
        self.lead.refresh_from_db()
        self.assertGreater(self.lead.last_activity_at, previous_activity_at)

    def test_activity_api_scopes_timeline_to_the_visible_leads(self):
        own_activity = Activity.objects.create(
            business=self.business,
            lead=self.lead,
            kind=Activity.Kind.NOTE,
            content='Own timeline item.',
            created_by=self.salesperson,
        )
        other_activity = Activity.objects.create(
            business=self.other_business,
            lead=self.other_lead,
            kind=Activity.Kind.NOTE,
            content='Private timeline item.',
            created_by=self.other_user,
        )
        self.client.force_authenticate(self.salesperson)

        response = self.client.get(reverse('activity-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(own_activity.id))
        detail = self.client.get(reverse('activity-detail', args=[other_activity.id]))
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_activity_api_rejects_system_events(self):
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(
            reverse('activity-list'),
            {
                'lead': str(self.lead.id),
                'kind': Activity.Kind.SYSTEM,
                'content': 'Pretend system event.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('kind', response.data)

    def test_activity_api_rejects_another_business_lead(self):
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(
            reverse('activity-list'),
            {
                'lead': str(self.other_lead.id),
                'kind': Activity.Kind.NOTE,
                'content': 'Attempt to write outside the workspace.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('lead', response.data)

    @patch('leads.services.schedule_follow_up.delay')
    def test_transition_records_activity_and_schedules_once(self, schedule_follow_up):
        self.client.force_authenticate(self.salesperson)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('lead-transition', args=[self.lead.id]),
                {'stage': Lead.Stage.QUOTATION_SENT},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        activity = Activity.objects.get(lead=self.lead, kind=Activity.Kind.STAGE_CHANGE)
        self.assertEqual(activity.metadata, {
            'from': Lead.Stage.NEW_INQUIRY,
            'to': Lead.Stage.QUOTATION_SENT,
        })
        schedule_follow_up.assert_called_once_with(
            str(self.business.id), str(self.lead.id), 'quote_followup_v1',
        )

    @patch('leads.services.schedule_follow_up.delay')
    def test_same_stage_transition_does_not_schedule_another_follow_up(self, schedule_follow_up):
        self.lead.stage = Lead.Stage.QUOTATION_SENT
        self.lead.save()
        self.client.force_authenticate(self.salesperson)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('lead-transition', args=[self.lead.id]),
                {'stage': Lead.Stage.QUOTATION_SENT},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Activity.objects.filter(lead=self.lead).exists())
        schedule_follow_up.assert_not_called()

    @patch('leads.services.schedule_follow_up.delay')
    def test_needs_time_updates_the_timeline_and_activity_timestamp(self, schedule_follow_up):
        self.client.force_authenticate(self.salesperson)
        previous_activity_at = self.lead.last_activity_at

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('lead-needs-time', args=[self.lead.id]))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.lead.refresh_from_db()
        self.assertGreater(self.lead.last_activity_at, previous_activity_at)
        self.assertTrue(Activity.objects.filter(
            lead=self.lead,
            kind=Activity.Kind.NOTE,
            content='Customer needs more time. A follow-up has been set for seven days from now.',
        ).exists())
        schedule_follow_up.assert_called_once_with(
            str(self.business.id), str(self.lead.id), 'delayed_followup_v1',
        )

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


class SiteVisitApiTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.owner = User.objects.create_user(
            username='visit-owner', password='test-password', business=self.business, role=User.Role.OWNER,
        )
        self.salesperson = User.objects.create_user(
            username='visit-sales', password='test-password', business=self.business, role=User.Role.SALESPERSON,
        )
        self.other_owner = User.objects.create_user(
            username='visit-other', password='test-password', business=self.other_business, role=User.Role.OWNER,
        )
        self.lead = Lead.objects.create(
            business=self.business,
            customer_name='Ayesha Site',
            phone='03000000000',
            assigned_user=self.salesperson,
        )
        self.other_lead = Lead.objects.create(
            business=self.other_business,
            customer_name='Private Site',
            phone='03110000000',
            assigned_user=self.other_owner,
        )

    def create_visit(self, **overrides):
        values = {
            'business': self.business,
            'lead': self.lead,
            'scheduled_at': timezone.now() + timedelta(hours=3),
            'address': '14 Solar Road',
            'assigned_user': self.salesperson,
            'reminder_enabled': True,
        }
        values.update(overrides)
        return SiteVisit.objects.create(**values)

    def test_api_creates_visit_activity_and_one_hour_reminder(self):
        self.lead.stage = Lead.Stage.SITE_VISIT
        self.lead.save()
        scheduled_at = timezone.now() + timedelta(hours=3)
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('site-visit-list'),
            {
                'lead': str(self.lead.id),
                'scheduled_at': scheduled_at.isoformat(),
                'address': '14 Solar Road',
                'assigned_user': str(self.salesperson.id),
                'reminder_enabled': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        visit = SiteVisit.objects.get(pk=response.data['id'])
        self.assertEqual(visit.business, self.business)
        self.assertEqual(visit.assigned_user, self.salesperson)
        self.assertTrue(Activity.objects.filter(
            business=self.business,
            lead=self.lead,
            kind=Activity.Kind.SITE_VISIT,
            content='Site visit scheduled.',
            metadata__site_visit_id=str(visit.id),
        ).exists())
        reminder = FollowUpTask.objects.get(
            business=self.business,
            lead=self.lead,
            rule_key=f'site_visit_reminder:{visit.id}',
        )
        self.assertEqual(reminder.assigned_user, self.salesperson)
        self.assertEqual(reminder.due_at, scheduled_at - timedelta(hours=1))

    def test_api_rejects_scheduling_before_the_site_visit_stage(self):
        self.lead.stage = Lead.Stage.CONTACTED
        self.lead.save()
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('site-visit-list'),
            {
                'lead': str(self.lead.id),
                'scheduled_at': (timezone.now() + timedelta(hours=3)).isoformat(),
                'reminder_enabled': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('lead', response.data)
        self.assertEqual(SiteVisit.objects.filter(lead=self.lead).count(), 0)

    def test_transition_to_site_visit_returns_a_non_blocking_schedule_prompt(self):
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(
            reverse('lead-transition', args=[self.lead.id]),
            {'stage': Lead.Stage.SITE_VISIT},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['stage'], Lead.Stage.SITE_VISIT)
        self.assertTrue(response.data['site_visit_scheduling_recommended'])
        self.assertEqual(SiteVisit.objects.filter(lead=self.lead).count(), 0)

    def test_salesperson_cannot_see_or_schedule_another_persons_visit(self):
        self.lead.stage = Lead.Stage.SITE_VISIT
        self.lead.save()
        manager_visit = SiteVisit.objects.create(
            business=self.business,
            lead=self.lead,
            scheduled_at=timezone.now() + timedelta(hours=3),
            assigned_user=self.owner,
        )
        self.client.force_authenticate(self.salesperson)

        list_response = self.client.get(reverse('site-visit-list'))
        detail_response = self.client.get(reverse('site-visit-detail', args=[manager_visit.id]))
        create_response = self.client.post(
            reverse('site-visit-list'),
            {
                'lead': str(self.lead.id),
                'scheduled_at': (timezone.now() + timedelta(hours=3)).isoformat(),
                'assigned_user': str(self.owner.id),
            },
            format='json',
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data['count'], 0)
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(create_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('assigned_user', create_response.data)

    def test_complete_cancels_the_reminder_and_records_activity(self):
        visit = self.create_visit()
        reminder = FollowUpTask.objects.create(
            business=self.business,
            lead=self.lead,
            assigned_user=self.salesperson,
            due_at=visit.scheduled_at - timedelta(hours=1),
            description='Site visit reminder for Ayesha Site.',
            rule_key=f'site_visit_reminder:{visit.id}',
        )
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(reverse('site-visit-complete', args=[visit.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visit.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(visit.status, SiteVisit.Status.COMPLETED)
        self.assertIsNotNone(visit.completed_at)
        self.assertEqual(reminder.status, FollowUpTask.Status.CANCELLED)
        self.assertTrue(Activity.objects.filter(
            lead=self.lead,
            kind=Activity.Kind.SITE_VISIT,
            content='Site visit completed.',
            metadata__site_visit_id=str(visit.id),
        ).exists())

    def test_cancel_records_a_distinct_activity_and_resolves_its_reminder(self):
        visit = self.create_visit()
        reminder = FollowUpTask.objects.create(
            business=self.business,
            lead=self.lead,
            assigned_user=self.salesperson,
            due_at=visit.scheduled_at - timedelta(hours=1),
            description='Site visit reminder for Ayesha Site.',
            rule_key=f'site_visit_reminder:{visit.id}',
        )
        self.client.force_authenticate(self.salesperson)

        response = self.client.post(reverse('site-visit-cancel', args=[visit.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visit.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(visit.status, SiteVisit.Status.CANCELLED)
        self.assertIsNotNone(visit.cancelled_at)
        self.assertEqual(reminder.status, FollowUpTask.Status.CANCELLED)
        self.assertTrue(Activity.objects.filter(
            lead=self.lead,
            kind=Activity.Kind.SITE_VISIT,
            content='Site visit cancelled.',
            metadata__site_visit_id=str(visit.id),
        ).exists())

    def test_reschedule_updates_the_existing_reminder_without_duplication(self):
        visit = self.create_visit()
        original_due_at = visit.scheduled_at - timedelta(hours=1)
        reminder = FollowUpTask.objects.create(
            business=self.business,
            lead=self.lead,
            assigned_user=self.salesperson,
            due_at=original_due_at,
            description='Site visit reminder for Ayesha Site.',
            rule_key=f'site_visit_reminder:{visit.id}',
        )
        new_time = timezone.now() + timedelta(days=2, hours=2)
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse('site-visit-reschedule', args=[visit.id]),
            {
                'scheduled_at': new_time.isoformat(),
                'address': '22 Measurement Lane',
                'reminder_enabled': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visit.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(visit.scheduled_at, new_time)
        self.assertEqual(visit.address, '22 Measurement Lane')
        self.assertEqual(reminder.due_at, new_time - timedelta(hours=1))
        self.assertEqual(FollowUpTask.objects.filter(
            business=self.business,
            lead=self.lead,
            rule_key=f'site_visit_reminder:{visit.id}',
            status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
        ).count(), 1)


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
