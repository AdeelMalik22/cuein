from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core import mail
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Business, PendingRegistration, User
from followups.models import FollowUpTask
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
        self.assertContains(response, 'data-live-clock')
        self.assertContains(response, 'data-sidebar-toggle')


class EmailVerificationTests(TestCase):
    def setUp(self):
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

        self.assertRedirects(first_response, reverse('web:onboarding'))
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
