from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Business, User
from leads.models import Lead, Product


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
