"""Shared, transactional lead workflow operations.

Keeping these mutations here prevents the web UI and REST API from quietly
drifting into different business behaviour.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.tenancy import is_active_member_of_business
from followups.rules import DELAYED_FOLLOWUP, rule_for_stage
from followups.tasks import schedule_follow_up

from .models import Activity, Lead, SiteVisit


NEEDS_TIME_ACTIVITY_CONTENT = (
    'Customer needs more time. A follow-up has been set for seven days from now.'
)
SITE_VISIT_REMINDER_PREFIX = 'site_visit_reminder:'
SITE_VISIT_REMINDER_OFFSET = timedelta(hours=1)


class SiteVisitAlreadyClosedError(ValueError):
    """Raised when a visit lifecycle action races with a closed visit."""


def record_lead_capture(*, lead, actor):
    """Add the standard timeline event for a newly captured lead."""
    return Activity.objects.create(
        business=lead.business,
        lead=lead,
        kind=Activity.Kind.SYSTEM,
        content='Lead captured.',
        created_by=actor,
    )


def record_manual_activity(*, lead, actor, kind, content):
    """Add a user-entered timeline item and refresh the lead's activity time."""
    activity = Activity.objects.create(
        business=lead.business,
        lead=lead,
        kind=kind,
        content=content,
        created_by=actor,
    )
    lead.last_activity_at = timezone.now()
    lead.save(update_fields=('last_activity_at', 'updated_at'))
    return activity


def _schedule_rule_after_commit(lead, rule_key):
    transaction.on_commit(
        lambda: schedule_follow_up.delay(str(lead.business_id), str(lead.id), rule_key),
    )


def transition_lead(*, lead, stage, actor, lost_reason='', note=''):
    """Apply a lead transition to a locked lead and record its timeline.

    Callers must hold ``lead`` with ``select_for_update()`` inside an atomic
    transaction. A request that leaves every meaningful value unchanged does
    not reset activity timestamps or schedule another automated follow-up.
    """
    previous_stage = lead.stage
    next_lost_reason = lost_reason.strip() if stage == Lead.Stage.LOST else ''
    stage_changed = previous_stage != stage
    lost_reason_changed = stage == Lead.Stage.LOST and lead.lost_reason != next_lost_reason
    note = note.strip()

    if stage_changed or lost_reason_changed:
        now = timezone.now()
        lead.stage = stage
        lead.lost_reason = next_lost_reason
        lead.closed_at = now if stage in (Lead.Stage.WON, Lead.Stage.LOST) else None
        lead.last_activity_at = now
        lead.save()

        if stage_changed:
            Activity.objects.create(
                business=lead.business,
                lead=lead,
                kind=Activity.Kind.STAGE_CHANGE,
                content=(
                    f'Moved from {Lead.Stage(previous_stage).label} '
                    f'to {Lead.Stage(stage).label}.'
                ),
                metadata={'from': previous_stage, 'to': stage},
                created_by=actor,
            )
        else:
            Activity.objects.create(
                business=lead.business,
                lead=lead,
                kind=Activity.Kind.SYSTEM,
                content='Lost reason updated.',
                created_by=actor,
            )

    if note:
        Activity.objects.create(
            business=lead.business,
            lead=lead,
            kind=Activity.Kind.NOTE,
            content=note,
            created_by=actor,
        )
        if not stage_changed and not lost_reason_changed:
            lead.last_activity_at = timezone.now()
            lead.save(update_fields=('last_activity_at', 'updated_at'))

    if stage_changed:
        rule = rule_for_stage(stage)
        if rule:
            _schedule_rule_after_commit(lead, rule.key)

    return lead, stage_changed


def record_needs_time(*, lead, actor):
    """Record a customer's delay and queue the one automated reminder."""
    if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
        raise ValueError('Terminal leads cannot receive a follow-up reminder.')

    lead.last_activity_at = timezone.now()
    lead.save(update_fields=('last_activity_at', 'updated_at'))
    Activity.objects.create(
        business=lead.business,
        lead=lead,
        kind=Activity.Kind.NOTE,
        content=NEEDS_TIME_ACTIVITY_CONTENT,
        created_by=actor,
    )
    _schedule_rule_after_commit(lead, DELAYED_FOLLOWUP.key)
    return lead


def _site_visit_reminder_rule_key(visit):
    """Return a deterministic, per-visit key that fits FollowUpTask.rule_key."""
    return f'{SITE_VISIT_REMINDER_PREFIX}{visit.id}'


def _site_visit_metadata(visit):
    metadata = {
        'site_visit_id': str(visit.id),
        'scheduled_at': visit.scheduled_at.isoformat(),
        'status': visit.status,
    }
    if visit.address:
        metadata['address'] = visit.address
    return metadata


def _record_site_visit_activity(*, visit, actor, content, now):
    """Write visit history and keep stale-lead calculations accurate."""
    Activity.objects.create(
        business=visit.business,
        lead=visit.lead,
        kind=Activity.Kind.SITE_VISIT,
        content=content,
        metadata=_site_visit_metadata(visit),
        created_by=actor,
    )
    visit.lead.last_activity_at = now
    visit.lead.save(update_fields=('last_activity_at', 'updated_at'))


def _resolve_open_visit_reminder_notifications(task, *, now):
    # Imported lazily so the lead workflow module remains usable while Django
    # is loading the mutually related leads and followups applications.
    from followups.models import Notification

    Notification.objects.filter(task=task, read_at__isnull=True).update(read_at=now)


def _sync_site_visit_reminder(*, visit, now):
    """Create, move, or cancel this visit's one optional reminder task.

    FollowUpTask already has a partial unique constraint for open automated
    tasks. The deterministic rule key lets a reschedule safely update the same
    reminder and lets the Celery overdue sweep treat it like any other task.
    """
    from followups.models import FollowUpTask

    rule_key = _site_visit_reminder_rule_key(visit)
    open_tasks = FollowUpTask.objects.for_business(visit.business).select_for_update().filter(
        lead=visit.lead,
        rule_key=rule_key,
        status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
    )
    existing_task = open_tasks.first()
    should_remind = visit.status == SiteVisit.Status.SCHEDULED and visit.reminder_enabled

    if not should_remind:
        for task in open_tasks:
            task.status = FollowUpTask.Status.CANCELLED
            task.completed_at = None
            task.save(update_fields=('status', 'completed_at'))
            _resolve_open_visit_reminder_notifications(task, now=now)
        return None

    reminder_due_at = visit.scheduled_at - SITE_VISIT_REMINDER_OFFSET
    if reminder_due_at <= now:
        raise ValueError(
            'Choose a visit time at least one hour ahead or turn off the reminder.'
        )

    description = f'Site visit reminder for {visit.lead.customer_name}.'
    if existing_task is None:
        try:
            # The nested savepoint means a rare concurrent create does not
            # poison the surrounding visit transaction.
            with transaction.atomic():
                task, _created = FollowUpTask.objects.get_or_create(
                    business=visit.business,
                    lead=visit.lead,
                    rule_key=rule_key,
                    status=FollowUpTask.Status.PENDING,
                    defaults={
                        'assigned_user': visit.assigned_user,
                        'due_at': reminder_due_at,
                        'description': description,
                    },
                )
        except IntegrityError:
            task = FollowUpTask.objects.for_business(visit.business).select_for_update().get(
                lead=visit.lead,
                rule_key=rule_key,
                status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
            )
    else:
        task = existing_task

    update_fields = []
    if task.assigned_user_id != visit.assigned_user_id:
        task.assigned_user = visit.assigned_user
        update_fields.append('assigned_user')
    if task.due_at != reminder_due_at:
        task.due_at = reminder_due_at
        update_fields.append('due_at')
    if task.description != description:
        task.description = description
        update_fields.append('description')
    if task.status != FollowUpTask.Status.PENDING:
        task.status = FollowUpTask.Status.PENDING
        update_fields.append('status')
        _resolve_open_visit_reminder_notifications(task, now=now)
    if update_fields:
        task.save(update_fields=update_fields)
    return task


def _validate_new_site_visit(*, lead, scheduled_at, assigned_user, reminder_enabled, now):
    if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
        raise ValueError('Closed leads cannot receive a site visit.')
    if scheduled_at <= now:
        raise ValueError('Choose a future time for the site visit.')
    if not is_active_member_of_business(assigned_user, lead.business_id):
        raise ValueError('The visit assignee must be active in this business.')
    if reminder_enabled and scheduled_at - SITE_VISIT_REMINDER_OFFSET <= now:
        raise ValueError('Choose a visit time at least one hour ahead or turn off the reminder.')


def schedule_site_visit(*, lead, scheduled_at, address, assigned_user, reminder_enabled, actor):
    """Create one visit, its optional reminder task, and a timeline event.

    Call inside ``transaction.atomic()`` with a tenant-scoped, locked lead.
    """
    now = timezone.now()
    _validate_new_site_visit(
        lead=lead,
        scheduled_at=scheduled_at,
        assigned_user=assigned_user,
        reminder_enabled=reminder_enabled,
        now=now,
    )
    visit = SiteVisit.objects.create(
        business=lead.business,
        lead=lead,
        scheduled_at=scheduled_at,
        address=address.strip(),
        assigned_user=assigned_user,
        reminder_enabled=reminder_enabled,
    )
    _sync_site_visit_reminder(visit=visit, now=now)
    _record_site_visit_activity(
        visit=visit,
        actor=actor,
        content='Site visit scheduled.',
        now=now,
    )
    return visit


def reschedule_site_visit(*, visit, scheduled_at, address, assigned_user, reminder_enabled, actor):
    """Move a locked scheduled visit and keep its optional reminder aligned."""
    if visit.status != SiteVisit.Status.SCHEDULED:
        raise SiteVisitAlreadyClosedError('Only scheduled visits can be rescheduled.')

    now = timezone.now()
    _validate_new_site_visit(
        lead=visit.lead,
        scheduled_at=scheduled_at,
        assigned_user=assigned_user,
        reminder_enabled=reminder_enabled,
        now=now,
    )
    visit.scheduled_at = scheduled_at
    visit.address = address.strip()
    visit.assigned_user = assigned_user
    visit.reminder_enabled = reminder_enabled
    visit.save(update_fields=('scheduled_at', 'address', 'assigned_user', 'reminder_enabled', 'updated_at'))
    _sync_site_visit_reminder(visit=visit, now=now)
    _record_site_visit_activity(
        visit=visit,
        actor=actor,
        content='Site visit rescheduled.',
        now=now,
    )
    return visit


def complete_site_visit(*, visit, actor):
    """Mark a locked scheduled visit completed and record it on the lead."""
    if visit.status != SiteVisit.Status.SCHEDULED:
        raise SiteVisitAlreadyClosedError('Only scheduled visits can be completed.')

    now = timezone.now()
    visit.status = SiteVisit.Status.COMPLETED
    visit.completed_at = now
    visit.cancelled_at = None
    visit.save(update_fields=('status', 'completed_at', 'cancelled_at', 'updated_at'))
    _sync_site_visit_reminder(visit=visit, now=now)
    _record_site_visit_activity(
        visit=visit,
        actor=actor,
        content='Site visit completed.',
        now=now,
    )
    return visit


def cancel_site_visit(*, visit, actor):
    """Cancel a locked scheduled visit and record it on the lead."""
    if visit.status != SiteVisit.Status.SCHEDULED:
        raise SiteVisitAlreadyClosedError('Only scheduled visits can be cancelled.')

    now = timezone.now()
    visit.status = SiteVisit.Status.CANCELLED
    visit.completed_at = None
    visit.cancelled_at = now
    visit.save(update_fields=('status', 'completed_at', 'cancelled_at', 'updated_at'))
    _sync_site_visit_reminder(visit=visit, now=now)
    _record_site_visit_activity(
        visit=visit,
        actor=actor,
        content='Site visit cancelled.',
        now=now,
    )
    return visit
