import argparse
import os
import random
from datetime import timedelta
from decimal import Decimal

import django
from django.db import transaction
from django.utils import timezone
from faker import Faker

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cuein.settings')
django.setup()

from core.models import Business, User
from followups.models import FollowUpTask
from leads.models import Activity, Lead, Product

fake = Faker()

SMITH_LLC_NAME = 'Dixon-Simmons'
SMITH_LLC_DEMO_LEAD_COUNT = 2_000
SMITH_LLC_DEMO_EMAIL_DOMAIN = '@demo.smithllc.test'


def run():
    businesses = []
    industries = [Business.Industry.CLEANING, Business.Industry.PLUMBING, Business.Industry.CONSULTING]
    for ind in industries:
        b = Business.objects.create(name=fake.company(), industry=ind)
        businesses.append(b)

    users_info = []

    for b in businesses:
        b_products = []
        for _ in range(10):
            p = Product.objects.create(
                business=b,
                name=f"{fake.bs().title()} Service",
                description=fake.catch_phrase()
            )
            b_products.append(p)

        roles = [User.Role.OWNER] + [User.Role.MANAGER] * 3 + [User.Role.SALESPERSON] * 26

        b_users = []
        for i in range(30):
            first_name = fake.first_name()
            last_name = fake.last_name()
            username = f"{first_name.lower()}.{last_name.lower()}{random.randint(1, 9999)}"
            email = f"{username}@{b.name.lower().replace(' ', '').replace(',', '')}.com"
            password = 'Password123!'
            role = roles[i]

            # Development seed data is trusted: create usable accounts directly
            # instead of creating pending registrations or sending email codes.
            u = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                business=b,
                role=role,
                phone=fake.phone_number(),
                is_active=True,
                email_verified_at=timezone.now(),
            )
            b_users.append(u)
            if role in [User.Role.OWNER, User.Role.MANAGER]:
                users_info.append(
                    f"- **Business**: {b.name} | **Role**: {role} | **Email**: `{email}` | **Pass**: `{password}`")
            elif i == 5:
                users_info.append(
                    f"- **Business**: {b.name} | **Role**: {role} | **Email**: `{email}` | **Pass**: `{password}`")

        stages = [Lead.Stage.NEW_INQUIRY, Lead.Stage.CONTACTED, Lead.Stage.SITE_VISIT, Lead.Stage.QUOTATION_SENT,
                  Lead.Stage.NEGOTIATION, Lead.Stage.WON, Lead.Stage.LOST]
        for _ in range(100):
            stage = random.choice(stages)
            lost_reason = fake.sentence() if stage == Lead.Stage.LOST else ''
            closed_at = timezone.now() - timedelta(days=random.randint(1, 30)) if stage in [Lead.Stage.WON,
                                                                                            Lead.Stage.LOST] else None

            Lead.objects.create(
                business=b,
                customer_name=fake.name(),
                phone=fake.phone_number(),
                email=fake.email(),
                source=random.choice(Lead.Source.choices)[0],
                product=random.choice(b_products),
                stage=stage,
                quoted_price=Decimal(str(round(random.uniform(100, 5000), 2))) if stage not in [Lead.Stage.NEW_INQUIRY,
                                                                                                Lead.Stage.CONTACTED] else None,
                assigned_user=random.choice(b_users),
                lost_reason=lost_reason,
                closed_at=closed_at
            )

    with open("seeded_credentials.md", "w") as f:
        f.write("# Seeded Users\n\n")
        f.write("\n".join(users_info))
        f.write("\n\n*All passwords are `Password123!`*")


def _demo_email(index):
    return f'smith-lead-{index:04d}{SMITH_LLC_DEMO_EMAIL_DOMAIN}'


def _stage_sequence(total, rng):
    """Return an exact, intentionally varied distribution of pipeline stages."""
    stage_shares = (
        (Lead.Stage.NEW_INQUIRY, 14),
        (Lead.Stage.CONTACTED, 18),
        (Lead.Stage.SITE_VISIT, 12),
        (Lead.Stage.QUOTATION_SENT, 18),
        (Lead.Stage.NEGOTIATION, 8),
        (Lead.Stage.WON, 18),
        (Lead.Stage.LOST, 12),
    )
    stages = []
    allocated = 0
    for position, (stage, percentage) in enumerate(stage_shares):
        count = total - allocated if position == len(stage_shares) - 1 else round(total * percentage / 100)
        stages.extend([stage] * count)
        allocated += count
    rng.shuffle(stages)
    return stages


def _lead_timeline(stage, now, rng):
    """Create a credible lead lifetime and preserve valid close dates."""
    age_in_days = rng.randint(3, 540)
    created_at = now - timedelta(
        days=age_in_days,
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )

    if stage in (Lead.Stage.WON, Lead.Stage.LOST):
        close_after_days = rng.randint(1, min(120, max(1, age_in_days - 1)))
        closed_at = created_at + timedelta(days=close_after_days, hours=rng.randint(0, 18))
        if closed_at >= now:
            closed_at = now - timedelta(minutes=rng.randint(15, 180))
        return created_at, closed_at, closed_at

    if age_in_days > 12 and rng.random() < .2:
        inactive_days = rng.randint(11, min(60, age_in_days))
    else:
        inactive_days = rng.randint(0, min(9, age_in_days))
    last_activity_at = now - timedelta(days=inactive_days, hours=rng.randint(0, 20))
    if last_activity_at <= created_at:
        last_activity_at = created_at + timedelta(hours=1)
    return created_at, None, last_activity_at


def _task_for_lead(lead, lead_created_at, last_activity_at, now, rng):
    """Give the workspace a mix of real-looking open, overdue, and done work."""
    active_stages = {
        Lead.Stage.NEW_INQUIRY,
        Lead.Stage.CONTACTED,
        Lead.Stage.SITE_VISIT,
        Lead.Stage.QUOTATION_SENT,
        Lead.Stage.NEGOTIATION,
    }
    draw = rng.random()
    lead_age_days = max(1, (now - lead_created_at).days)

    if lead.stage in active_stages:
        if draw < .62:
            status = FollowUpTask.Status.PENDING
        elif draw < .83:
            status = FollowUpTask.Status.OVERDUE
        elif draw < .93:
            status = FollowUpTask.Status.DONE
        else:
            return None
    elif lead.stage == Lead.Stage.WON:
        status = (
            FollowUpTask.Status.PENDING if draw < .35
            else FollowUpTask.Status.DONE if draw < .92
            else FollowUpTask.Status.CANCELLED
        )
    else:
        status = FollowUpTask.Status.DONE if draw < .85 else FollowUpTask.Status.CANCELLED

    if status == FollowUpTask.Status.PENDING:
        due_at = now + timedelta(days=rng.randint(1, 28), hours=rng.randint(8, 17))
        task_created_at = max(
            lead_created_at,
            now - timedelta(days=rng.randint(0, min(10, lead_age_days))),
        )
        completed_at = None
        description = rng.choice((
            f'Call {lead.customer_name} to confirm the next step.',
            f'Check in with {lead.customer_name} about the service.',
            f'Send {lead.customer_name} a quick progress update.',
        ))
    elif status == FollowUpTask.Status.OVERDUE:
        overdue_days = rng.randint(1, min(18, max(1, lead_age_days - 1)))
        due_at = now - timedelta(days=overdue_days, hours=rng.randint(1, 12))
        task_created_at = max(
            lead_created_at,
            due_at - timedelta(days=rng.randint(1, 7)),
        )
        completed_at = None
        description = rng.choice((
            f'Follow up with {lead.customer_name}; no response yet.',
            f'Reconnect with {lead.customer_name} on the open inquiry.',
            f'Check whether {lead.customer_name} has reviewed the quote.',
        ))
    elif status == FollowUpTask.Status.DONE:
        completed_at = max(lead_created_at + timedelta(hours=1), last_activity_at)
        due_at = completed_at - timedelta(days=rng.randint(0, min(7, max(0, (completed_at - lead_created_at).days))))
        task_created_at = min(due_at, max(lead_created_at, due_at - timedelta(days=rng.randint(0, 5))))
        description = rng.choice((
            f'Completed follow-up with {lead.customer_name}.',
            f'Closed the loop with {lead.customer_name}.',
            f'Logged the outcome of the discussion with {lead.customer_name}.',
        ))
    else:
        due_at = last_activity_at + timedelta(days=rng.randint(1, 14))
        task_created_at = min(due_at, max(lead_created_at, due_at - timedelta(days=rng.randint(0, 5))))
        completed_at = None
        description = f'No further follow-up needed for {lead.customer_name}.'

    task = FollowUpTask(
        business=lead.business,
        lead=lead,
        assigned_user=lead.assigned_user,
        due_at=due_at,
        description=description,
        status=status,
        completed_at=completed_at,
    )
    return task, task_created_at


def _activity_for_lead(lead, last_activity_at, rng):
    activity_details = {
        Lead.Stage.NEW_INQUIRY: (Activity.Kind.SYSTEM, 'New inquiry captured and assigned for follow-up.'),
        Lead.Stage.CONTACTED: (Activity.Kind.CALL, 'Initial customer conversation logged.'),
        Lead.Stage.SITE_VISIT: (Activity.Kind.SITE_VISIT, 'Site visit details recorded for the requested service.'),
        Lead.Stage.QUOTATION_SENT: (Activity.Kind.QUOTATION, 'Quotation shared with the customer.'),
        Lead.Stage.NEGOTIATION: (Activity.Kind.CALL, 'Discussed pricing and next steps with the customer.'),
        Lead.Stage.WON: (Activity.Kind.NOTE, 'Customer confirmed the service and the deal was closed.'),
        Lead.Stage.LOST: (Activity.Kind.NOTE, f'Lead closed as lost: {lead.lost_reason}'),
    }
    kind, content = activity_details[lead.stage]
    if rng.random() < .18 and lead.stage not in (Lead.Stage.WON, Lead.Stage.LOST):
        content = 'Customer asked for a little more time before the next step.'
    return Activity(
        business=lead.business,
        lead=lead,
        kind=kind,
        content=content,
        created_by=lead.assigned_user,
        created_at=last_activity_at,
    )


def seed_smith_llc_demo_data(total=SMITH_LLC_DEMO_LEAD_COUNT):
    """Add idempotent, dashboard-ready demo data to the existing Smith LLC tenant."""
    business = Business.objects.filter(name__iexact=SMITH_LLC_NAME).first()
    if not business:
        raise RuntimeError(f'Could not find a business named {SMITH_LLC_NAME!r}.')

    assignees = list(User.objects.filter(
        business=business,
        is_active=True,
        role=User.Role.SALESPERSON,
    ))
    if not assignees:
        assignees = list(User.objects.filter(business=business, is_active=True))
    if not assignees:
        raise RuntimeError(f'{SMITH_LLC_NAME} needs at least one active user before leads can be seeded.')

    products = list(Product.objects.for_business(business).filter(is_active=True))
    if not products:
        products = [
            Product(business=business, name=name, description=description)
            for name, description in (
                ('Service consultation', 'An initial consultation and assessment.'),
                ('Standard installation', 'A standard service installation package.'),
                ('Premium maintenance', 'Priority maintenance and support.'),
            )
        ]
        Product.objects.bulk_create(products)

    existing_demo_emails = set(
        Lead.objects.for_business(business).filter(
            email__endswith=SMITH_LLC_DEMO_EMAIL_DOMAIN,
        ).values_list('email', flat=True),
    )
    missing_indices = [
        index for index in range(1, total + 1)
        if _demo_email(index) not in existing_demo_emails
    ]
    if not missing_indices:
        return {
            'created_leads': 0,
            'created_tasks': 0,
            'created_activities': 0,
            'existing_demo_leads': len(existing_demo_emails),
        }

    rng = random.Random(20260714)
    demo_fake = Faker()
    demo_fake.seed_instance(20260714)
    now = timezone.now()
    stages = _stage_sequence(total, rng)
    sources = [choice[0] for choice in Lead.Source.choices]
    source_weights = (24, 18, 9, 8, 15, 10, 12, 4)
    lost_reasons = (
        'Budget did not allow for the service this quarter.',
        'Customer selected another provider.',
        'The timing was not right for the customer.',
        'The customer could not be reached after several attempts.',
        'The requested service was outside the current scope.',
    )

    leads = []
    lead_timelines = []
    with transaction.atomic():
        for index in missing_indices:
            stage = stages[index - 1]
            created_at, closed_at, last_activity_at = _lead_timeline(stage, now, rng)
            quoted_price = None
            if stage not in (Lead.Stage.NEW_INQUIRY, Lead.Stage.CONTACTED) and rng.random() < .9:
                quoted_price = Decimal(rng.randint(18, 360) * 500).quantize(Decimal('0.01'))
            lead = Lead(
                business=business,
                customer_name=demo_fake.name(),
                phone='03' + ''.join(str(rng.randrange(10)) for _ in range(9)),
                email=_demo_email(index),
                source=rng.choices(sources, weights=source_weights, k=1)[0],
                product=rng.choice(products),
                stage=stage,
                quoted_price=quoted_price,
                assigned_user=rng.choice(assignees),
                lost_reason=rng.choice(lost_reasons) if stage == Lead.Stage.LOST else '',
                last_activity_at=last_activity_at,
                closed_at=closed_at,
            )
            leads.append(lead)
            lead_timelines.append((lead, created_at, last_activity_at))

        Lead.objects.bulk_create(leads, batch_size=500)
        for lead, created_at, last_activity_at in lead_timelines:
            lead.created_at = created_at
            lead.updated_at = last_activity_at
            lead.last_activity_at = last_activity_at
        Lead.objects.bulk_update(
            leads,
            ('created_at', 'updated_at', 'last_activity_at'),
            batch_size=500,
        )

        tasks = []
        task_timelines = []
        activities = []
        activity_timelines = []
        for lead, created_at, last_activity_at in lead_timelines:
            task_payload = _task_for_lead(lead, created_at, last_activity_at, now, rng)
            if task_payload:
                task, task_created_at = task_payload
                tasks.append(task)
                task_timelines.append((task, task_created_at))
            activity = _activity_for_lead(lead, last_activity_at, rng)
            activities.append(activity)
            activity_timelines.append((activity, last_activity_at))

        FollowUpTask.objects.bulk_create(tasks, batch_size=500)
        for task, task_created_at in task_timelines:
            task.created_at = task_created_at
        FollowUpTask.objects.bulk_update(tasks, ('created_at',), batch_size=500)

        Activity.objects.bulk_create(activities, batch_size=500)
        for activity, activity_created_at in activity_timelines:
            activity.created_at = activity_created_at
        Activity.objects.bulk_update(activities, ('created_at',), batch_size=500)

    task_statuses = {
        status: sum(task.status == status for task in tasks)
        for status in FollowUpTask.Status.values
    }
    return {
        'created_leads': len(leads),
        'created_tasks': len(tasks),
        'created_activities': len(activities),
        'task_statuses': task_statuses,
        'existing_demo_leads': len(existing_demo_emails),
    }


def main():
    parser = argparse.ArgumentParser(description='Seed Cuein development data.')
    parser.add_argument(
        '--smith-llc-demo',
        action='store_true',
        help='Add 2,000 dashboard-ready demo leads to the existing Smith LLC business.',
    )
    parser.add_argument(
        '--count',
        type=int,
        default=SMITH_LLC_DEMO_LEAD_COUNT,
        help='Number of Smith LLC demo leads to create (default: 2000).',
    )
    args = parser.parse_args()
    if args.smith_llc_demo:
        summary = seed_smith_llc_demo_data(args.count)
        if summary['created_leads']:
            statuses = summary['task_statuses']
            print(
                f"Added {summary['created_leads']} Smith LLC leads, "
                f"{summary['created_tasks']} follow-up tasks, and "
                f"{summary['created_activities']} activities. "
                f"Tasks: {statuses[FollowUpTask.Status.PENDING]} pending, "
                f"{statuses[FollowUpTask.Status.OVERDUE]} overdue, "
                f"{statuses[FollowUpTask.Status.DONE]} completed.",
            )
        else:
            print(
                f"Smith LLC already has {summary['existing_demo_leads']} demo leads; no records were added.",
            )
    else:
        run()


if __name__ == '__main__':
    main()