from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Business, Membership, User
from leads.models import Lead

from .models import FollowUpTask, Notification
from .rules import QUOTE_FOLLOWUP, STALE_LEAD_ESCALATION
from .services import flag_overdue_tasks, schedule_rule, schedule_stale_escalations


class FollowUpApiTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.other_business = Business.objects.create(name='Bright CCTV')
        self.owner = User.objects.create_user(username='owner', password='test-password', business=self.business, role=User.Role.OWNER)
        self.salesperson = User.objects.create_user(username='sales', password='test-password', business=self.business, role=User.Role.SALESPERSON)
        self.other_user = User.objects.create_user(username='other', password='test-password', business=self.other_business, role=User.Role.SALESPERSON)
        self.lead = Lead.objects.create(business=self.business, customer_name='Ali', phone='03000000000', assigned_user=self.salesperson)
        self.other_lead = Lead.objects.create(business=self.other_business, customer_name='Sara', phone='03110000000', assigned_user=self.other_user)

    def test_task_from_another_business_is_not_visible(self):
        task = FollowUpTask.objects.create(business=self.other_business, lead=self.other_lead, assigned_user=self.other_user, due_at=timezone.now() + timedelta(days=1), description='Private task')
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse('follow-up-task-detail', args=[task.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_salesperson_only_sees_their_tasks(self):
        FollowUpTask.objects.create(business=self.business, lead=self.lead, assigned_user=self.owner, due_at=timezone.now() + timedelta(days=1), description='Owner task')
        salesperson_task = FollowUpTask.objects.create(business=self.business, lead=self.lead, assigned_user=self.salesperson, due_at=timezone.now() + timedelta(days=1), description='Sales task')
        self.client.force_authenticate(self.salesperson)
        response = self.client.get(reverse('follow-up-task-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(salesperson_task.id))

    def test_today_filter_uses_the_business_local_day(self):
        self.business.timezone = 'Asia/Karachi'
        self.business.save(update_fields=('timezone',))
        due_during_local_today = FollowUpTask.objects.create(
            business=self.business,
            lead=self.lead,
            assigned_user=self.salesperson,
            due_at=datetime(2026, 1, 1, 20, 0, tzinfo=datetime_timezone.utc),
            description='Local-day task',
        )
        FollowUpTask.objects.create(
            business=self.business,
            lead=self.lead,
            assigned_user=self.salesperson,
            due_at=datetime(2026, 1, 2, 20, 0, tzinfo=datetime_timezone.utc),
            description='Tomorrow local',
        )
        self.client.force_authenticate(self.owner)

        with patch(
            'followups.views.timezone.now',
            return_value=datetime(2026, 1, 2, 0, 30, tzinfo=datetime_timezone.utc),
        ):
            response = self.client.get(f'{reverse("follow-up-task-list")}?due=today')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], str(due_during_local_today.id))

    def test_completion_creates_a_next_action(self):
        task = FollowUpTask.objects.create(business=self.business, lead=self.lead, assigned_user=self.salesperson, due_at=timezone.now() + timedelta(days=1), description='Call Ali')
        self.client.force_authenticate(self.salesperson)
        response = self.client.post(reverse('follow-up-task-complete', args=[task.id]), {'next_due_at': (timezone.now() + timedelta(days=3)).isoformat(), 'next_description': 'Call again'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task.refresh_from_db()
        self.assertEqual(task.status, FollowUpTask.Status.DONE)
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(FollowUpTask.objects.filter(lead=self.lead, status=FollowUpTask.Status.PENDING).count(), 1)


class FollowUpServiceTests(APITestCase):
    def setUp(self):
        self.business = Business.objects.create(name='North Star Solar')
        self.owner = User.objects.create_user(username='owner', password='test-password', business=self.business, role=User.Role.OWNER)
        self.salesperson = User.objects.create_user(username='sales', password='test-password', business=self.business, role=User.Role.SALESPERSON)
        self.lead = Lead.objects.create(business=self.business, customer_name='Ali', phone='03000000000', assigned_user=self.salesperson)

    def test_automated_rule_is_idempotent(self):
        first, first_created = schedule_rule(business_id=self.business.id, lead_id=self.lead.id, rule_key=QUOTE_FOLLOWUP.key)
        second, second_created = schedule_rule(business_id=self.business.id, lead_id=self.lead.id, rule_key=QUOTE_FOLLOWUP.key)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)

    def test_overdue_sweep_creates_one_notification(self):
        task = FollowUpTask.objects.create(business=self.business, lead=self.lead, assigned_user=self.salesperson, due_at=timezone.now() - timedelta(minutes=1), description='Call Ali')
        self.assertEqual(flag_overdue_tasks(), 1)
        self.assertEqual(flag_overdue_tasks(), 0)
        task.refresh_from_db()
        self.assertEqual(task.status, FollowUpTask.Status.OVERDUE)
        self.assertEqual(Notification.objects.filter(task=task).count(), 1)

    def test_stale_sweep_excludes_terminal_leads_and_is_idempotent(self):
        self.lead.last_activity_at = timezone.now() - timedelta(days=11)
        self.lead.save(update_fields=('last_activity_at',))
        terminal = Lead.objects.create(business=self.business, customer_name='Won', phone='03110000000', assigned_user=self.salesperson, stage=Lead.Stage.WON, closed_at=timezone.now(), last_activity_at=timezone.now() - timedelta(days=11))
        self.assertEqual(schedule_stale_escalations(), 1)
        self.assertEqual(schedule_stale_escalations(), 0)
        self.assertTrue(FollowUpTask.objects.filter(lead=self.lead, rule_key=STALE_LEAD_ESCALATION.key).exists())
        self.assertFalse(FollowUpTask.objects.filter(lead=terminal, rule_key=STALE_LEAD_ESCALATION.key).exists())

    def test_stale_sweep_uses_an_active_shared_membership(self):
        other_business = Business.objects.create(name='Bright CCTV')
        shared_manager = User.objects.create_user(
            username='shared-manager',
            password='test-password',
            business=other_business,
            role=User.Role.OWNER,
        )
        Membership.objects.create(
            user=shared_manager,
            business=self.business,
            role=User.Role.MANAGER,
        )
        self.lead.last_activity_at = timezone.now() - timedelta(days=11)
        self.lead.save(update_fields=('last_activity_at',))

        self.assertEqual(schedule_stale_escalations(), 1)
        task = FollowUpTask.objects.get(lead=self.lead, rule_key=STALE_LEAD_ESCALATION.key)

        self.assertEqual(task.assigned_user, shared_manager)
