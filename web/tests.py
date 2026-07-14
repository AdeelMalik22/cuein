from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Business, User
from leads.models import Lead


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
        self.assertContains(response, 'See the shape of your pipeline.')
        self.assertContains(response, 'data-sidebar-toggle')
