"""Shared, transactional lead workflow operations.

Keeping these mutations here prevents the web UI and REST API from quietly
drifting into different business behaviour.
"""

from django.db import transaction
from django.utils import timezone

from followups.rules import DELAYED_FOLLOWUP, rule_for_stage
from followups.tasks import schedule_follow_up

from .models import Activity, Lead


NEEDS_TIME_ACTIVITY_CONTENT = (
    'Customer needs more time. A follow-up has been set for seven days from now.'
)


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
