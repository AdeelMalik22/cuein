import os
import secrets
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Business, Membership, User
from followups.models import FollowUpTask, Notification
from leads.models import Activity, Lead, Product, SiteVisit


class Command(BaseCommand):
    help = 'Create an idempotent, realistic demo workspace for local development.'

    FIRST_NAMES = (
        'Ayesha', 'Bilal', 'Hassan', 'Sara', 'Usman', 'Maham', 'Zain', 'Iqra',
        'Hamza', 'Fatima', 'Daniyal', 'Hira', 'Owais', 'Sana', 'Talha',
    )
    LAST_NAMES = (
        'Khan', 'Ahmed', 'Malik', 'Raza', 'Sheikh', 'Qureshi', 'Butt', 'Hussain',
        'Iqbal', 'Farooq', 'Nawaz', 'Siddiqui', 'Javed', 'Aslam', 'Mirza',
    )
    AREAS = (
        'DHA Phase 5', 'Gulberg III', 'Bahria Town', 'Model Town', 'Johar Town',
        'PECHS', 'F-8', 'Clifton', 'Satellite Town', 'Cantt',
    )
    LEAD_NOTES = (
        'Called and confirmed that the customer is comparing two providers.',
        'Customer asked for a site measurement before approving the quotation.',
        'Shared the initial estimate and agreed to follow up after the weekend.',
        'Customer is interested but needs approval from a family member.',
        'Sent product details and confirmed the preferred contact number.',
    )

    def add_arguments(self, parser):
        parser.add_argument('--business-name', default='Smith LLC')
        parser.add_argument('--leads', type=int, default=200)
        parser.add_argument(
            '--password',
            default=os.environ.get('CUEIN_DEMO_PASSWORD', ''),
            help='Password for newly created demo users. Defaults to a generated password.',
        )

    def handle(self, *args, **options):
        lead_count = options['leads']
        if lead_count < 1:
            raise CommandError('--leads must be at least 1.')

        password = options['password'] or secrets.token_urlsafe(16)
        with transaction.atomic():
            business, business_created = Business.objects.get_or_create(
                name=options['business_name'],
                defaults={
                    'industry': Business.Industry.SOLAR,
                    'timezone': 'Asia/Karachi',
                },
            )
            users = self._ensure_users(business, password)
            products = self._ensure_products(business)
            lead_stats = self._ensure_leads(business, users, products, lead_count)

        self.stdout.write(self.style.SUCCESS(
            f'Demo workspace ready: {business.name} '
            f'({"created" if business_created else "existing"}).'
        ))
        self.stdout.write(
            f'Users: {", ".join(user.username for user in users.values())}'
        )
        if self.created_usernames:
            self.stdout.write(f'Demo password for newly created users: {password}')
        self.stdout.write(
            'Leads: {created} created, {existing} already present; '
            'products: {products}; activities/tasks/site visits were filled idempotently.'.format(
                products=len(products),
                **lead_stats,
            )
        )

    def _ensure_users(self, business, password):
        self.created_usernames = []
        definitions = (
            ('smith-owner', 'owner@smith.local', 'Adeel', 'Malik', User.Role.OWNER),
            ('smith-manager', 'manager@smith.local', 'Sara', 'Khan', User.Role.MANAGER),
            ('smith-ahmad', 'ahmad@smith.local', 'Ahmad', 'Raza', User.Role.SALESPERSON),
            ('smith-ayesha', 'ayesha@smith.local', 'Ayesha', 'Ahmed', User.Role.SALESPERSON),
            ('smith-hamza', 'hamza@smith.local', 'Hamza', 'Sheikh', User.Role.SALESPERSON),
        )
        users = {}
        for username, email, first_name, last_name, role in definitions:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'first_name': first_name,
                    'last_name': last_name,
                    'business': business,
                    'role': role,
                    'is_active': True,
                    'email_verified_at': timezone.now(),
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=('password',))
                self.created_usernames.append(username)
            Membership.objects.get_or_create(
                user=user,
                business=business,
                defaults={'role': role, 'is_active': True},
            )
            users[role if role != User.Role.SALESPERSON else username] = user
        return users

    def _ensure_products(self, business):
        product_definitions = (
            ('Solar installation', 'Residential and commercial solar systems.'),
            ('CCTV security package', 'Cameras, recording, and remote monitoring.'),
            ('Hybrid inverter upgrade', 'Battery-ready inverter replacement and setup.'),
            ('Annual maintenance plan', 'Scheduled inspection and maintenance visits.'),
            ('Smart home wiring', 'Structured wiring and connected-home installation.'),
        )
        products = []
        for name, description in product_definitions:
            product, _created = Product.objects.get_or_create(
                business=business,
                name=name,
                defaults={'description': description, 'is_active': True},
            )
            products.append(product)
        return products

    def _ensure_leads(self, business, users, products, lead_count):
        now = timezone.now()
        salespeople = [
            users['smith-ahmad'], users['smith-ayesha'], users['smith-hamza'],
        ]
        stages = (
            Lead.Stage.NEW_INQUIRY,
            Lead.Stage.CONTACTED,
            Lead.Stage.SITE_VISIT,
            Lead.Stage.QUOTATION_SENT,
            Lead.Stage.NEGOTIATION,
            Lead.Stage.WON,
            Lead.Stage.LOST,
        )
        sources = tuple(value for value, _label in Lead.Source.choices)
        created_count = 0
        existing_count = 0

        for index in range(lead_count):
            repeat_customer = index < 20
            contact_index = index % 10
            first_name = self.FIRST_NAMES[contact_index % len(self.FIRST_NAMES)]
            last_name = self.LAST_NAMES[(contact_index * 3) % len(self.LAST_NAMES)]
            customer_name = f'{first_name} {last_name}'
            phone = f'0300{1000000 + (contact_index if repeat_customer else index):07d}'
            email = f'{first_name.lower()}.{last_name.lower()}@demo-customer.local'
            product = products[index % len(products)]
            source = sources[index % len(sources)]
            stage = stages[index % len(stages)]
            assigned_user = salespeople[index % len(salespeople)]
            lost_reason = (
                ('Budget was not approved' if index % 2 else 'Customer chose another provider')
                if stage == Lead.Stage.LOST else ''
            )
            lookup = Lead.objects.filter(
                business=business,
                customer_name=customer_name,
                phone=phone,
                email=email,
                product=product,
                source=source,
            ).first()
            if lookup:
                lead = lookup
                existing_count += 1
            else:
                closed_at = now - timedelta(days=(index % 35) + 1) if stage in (
                    Lead.Stage.WON, Lead.Stage.LOST,
                ) else None
                lead = Lead.objects.create(
                    business=business,
                    customer_name=customer_name,
                    phone=phone,
                    email=email,
                    source=source,
                    product=product,
                    stage=stage,
                    quoted_price=Decimal((index % 18 + 3) * 25000),
                    assigned_user=assigned_user,
                    lost_reason=lost_reason,
                    last_activity_at=now - timedelta(days=index % 16, hours=index % 9),
                    closed_at=closed_at,
                )
                created_count += 1

            self._ensure_lead_activity(lead, users[User.Role.OWNER], index)
            self._ensure_lead_task(lead, users, index, now)
            if lead.stage == Lead.Stage.SITE_VISIT:
                self._ensure_site_visit(lead, users, index, now)

        return {'created': created_count, 'existing': existing_count}

    def _ensure_lead_activity(self, lead, actor, index):
        content = self.LEAD_NOTES[index % len(self.LEAD_NOTES)]
        if not Activity.objects.filter(lead=lead, content=content).exists():
            Activity.objects.create(
                business=lead.business,
                lead=lead,
                kind=Activity.Kind.NOTE,
                content=content,
                created_by=actor,
            )

    def _ensure_lead_task(self, lead, users, index, now):
        rule_key = f'demo_followup:{lead.pk}'
        if FollowUpTask.objects.filter(business=lead.business, lead=lead, rule_key=rule_key).exists():
            return
        if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
            status = FollowUpTask.Status.DONE if index % 2 else FollowUpTask.Status.CANCELLED
        elif index % 5 == 0:
            status = FollowUpTask.Status.OVERDUE
        else:
            status = FollowUpTask.Status.PENDING
        completed_at = now - timedelta(days=2) if status == FollowUpTask.Status.DONE else None
        due_at = now - timedelta(days=(index % 6) + 1) if status in (
            FollowUpTask.Status.OVERDUE,
            FollowUpTask.Status.DONE,
        ) else now + timedelta(days=(index % 12) + 1)
        task = FollowUpTask.objects.create(
            business=lead.business,
            lead=lead,
            assigned_user=lead.assigned_user,
            due_at=due_at,
            description=(
                'Follow up on the quotation and confirm the decision.'
                if lead.stage == Lead.Stage.QUOTATION_SENT
                else 'Call the customer and confirm the next step.'
            ),
            status=status,
            rule_key=rule_key,
            completed_at=completed_at,
        )
        if status == FollowUpTask.Status.OVERDUE:
            Notification.objects.get_or_create(
                business=lead.business,
                task=task,
                recipient=lead.assigned_user,
            )

    def _ensure_site_visit(self, lead, users, index, now):
        address = f'{self.AREAS[index % len(self.AREAS)]}, Lahore'
        if SiteVisit.objects.filter(lead=lead, address=address).exists():
            return
        if index % 3 == 0:
            status = SiteVisit.Status.COMPLETED
            visit_time = now - timedelta(days=(index % 14) + 2)
            completed_at = now - timedelta(days=1)
            cancelled_at = None
            reminder_enabled = False
        elif index % 5 == 0:
            status = SiteVisit.Status.CANCELLED
            visit_time = now - timedelta(days=(index % 14) + 2)
            completed_at = None
            cancelled_at = now - timedelta(days=1)
            reminder_enabled = False
        else:
            status = SiteVisit.Status.SCHEDULED
            visit_time = now + timedelta(days=(index % 14) + 2, hours=2)
            completed_at = None
            cancelled_at = None
            reminder_enabled = True
        SiteVisit.objects.create(
            business=lead.business,
            lead=lead,
            scheduled_at=visit_time,
            address=address,
            assigned_user=lead.assigned_user,
            status=status,
            reminder_enabled=reminder_enabled,
            completed_at=completed_at,
            cancelled_at=cancelled_at,
        )
