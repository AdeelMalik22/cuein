from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Business, Membership, User
from core.tenancy import ensure_legacy_memberships_for_business, is_active_member_of_business
from leads.models import Activity, Lead

from .models import FollowUpTask, Notification
from .rules import RULES, STALE_LEAD_ESCALATION


OPEN_TASK_STATUSES = (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE)


class TaskAlreadyClosedError(ValueError):
    """Raised when a lifecycle action races with an already closed task."""


def _resolve_open_notifications(task, *, now):
    Notification.objects.filter(task=task, read_at__isnull=True).update(read_at=now)


def _record_task_activity(task, *, actor, content, now):
    Activity.objects.create(
        business=task.business,
        lead=task.lead,
        kind=Activity.Kind.SYSTEM,
        content=content,
        created_by=actor,
    )
    task.lead.last_activity_at = now
    task.lead.save(update_fields=('last_activity_at', 'updated_at'))


def complete_task(*, task, next_due_at, next_description, actor):
    """Finish a locked open task, resolve its alert, and create its successor."""
    if task.status not in OPEN_TASK_STATUSES:
        raise TaskAlreadyClosedError('Only open tasks can be completed.')

    now = timezone.now()
    task.status = FollowUpTask.Status.DONE
    task.completed_at = now
    task.save(update_fields=('status', 'completed_at'))
    _resolve_open_notifications(task, now=now)
    next_task = FollowUpTask.objects.create(
        business=task.business,
        lead=task.lead,
        assigned_user=task.assigned_user,
        due_at=next_due_at,
        description=next_description,
    )
    _record_task_activity(
        task,
        actor=actor,
        content='Follow-up completed. Next action scheduled.',
        now=now,
    )
    return next_task


def reschedule_task(*, task, due_at, actor):
    """Move a locked open task back to pending and resolve any overdue alert."""
    if task.status not in OPEN_TASK_STATUSES:
        raise TaskAlreadyClosedError('Only open tasks can be rescheduled.')

    now = timezone.now()
    task.due_at = due_at
    task.status = FollowUpTask.Status.PENDING
    task.save(update_fields=('due_at', 'status'))
    _resolve_open_notifications(task, now=now)
    _record_task_activity(task, actor=actor, content='Follow-up rescheduled.', now=now)
    return task


def cancel_task(*, task, actor):
    """Cancel a locked open task and resolve any outstanding overdue alert."""
    if task.status not in OPEN_TASK_STATUSES:
        raise TaskAlreadyClosedError('Only open tasks can be cancelled.')

    now = timezone.now()
    task.status = FollowUpTask.Status.CANCELLED
    task.completed_at = None
    task.save(update_fields=('status', 'completed_at'))
    _resolve_open_notifications(task, now=now)
    _record_task_activity(task, actor=actor, content='Follow-up cancelled.', now=now)
    return task


def schedule_rule(*, business_id, lead_id, rule_key, recipient_id=None):
    """Create one open automated task, safely across retries and workers."""
    rule = RULES[rule_key]
    with transaction.atomic():
        business = Business.objects.get(pk=business_id, is_active=True)
        lead = Lead.objects.for_business(business).select_related('assigned_user').get(pk=lead_id)
        recipient = lead.assigned_user
        if recipient_id is not None:
            recipient = User.objects.get(pk=recipient_id, is_active=True)
            if not is_active_member_of_business(recipient, business.id):
                raise User.DoesNotExist
        defaults = {
            'assigned_user': recipient,
            'due_at': timezone.now() + rule.delay,
            'description': rule.description,
        }
        try:
            task, created = FollowUpTask.objects.get_or_create(
                business=business,
                lead=lead,
                rule_key=rule.key,
                status=FollowUpTask.Status.PENDING,
                defaults=defaults,
            )
        except IntegrityError:
            task = FollowUpTask.objects.for_business(business).get(
                lead=lead,
                rule_key=rule.key,
                status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
            )
            created = False
    return task, created


def schedule_stale_escalations():
    cutoff = timezone.now() - STALE_LEAD_ESCALATION.delay
    created_count = 0
    for business in Business.objects.filter(is_active=True):
        # Preserve the temporary legacy-data bridge while making the actual
        # recipient choice entirely membership-scoped.
        ensure_legacy_memberships_for_business(business)
        recipient_membership = Membership.objects.filter(
            business=business,
            is_active=True,
            user__is_active=True,
            role__in=(User.Role.OWNER, User.Role.MANAGER),
        ).select_related('user').order_by('role', 'id').first()
        if not recipient_membership:
            continue
        recipient = recipient_membership.user
        leads = Lead.objects.for_business(business).exclude(
            stage__in=(Lead.Stage.WON, Lead.Stage.LOST)
        ).filter(last_activity_at__lt=cutoff)
        for lead in leads.iterator():
            _, created = schedule_rule(
                business_id=business.id,
                lead_id=lead.id,
                rule_key=STALE_LEAD_ESCALATION.key,
                recipient_id=recipient.id,
            )
            created_count += created
    return created_count


def flag_overdue_tasks():
    tasks = FollowUpTask.objects.filter(
        status=FollowUpTask.Status.PENDING, due_at__lt=timezone.now()
    ).select_related('assigned_user')
    count = 0
    for task in tasks.iterator():
        with transaction.atomic():
            task = FollowUpTask.objects.select_for_update().get(pk=task.pk)
            if task.status != FollowUpTask.Status.PENDING or task.due_at >= timezone.now():
                continue
            task.status = FollowUpTask.Status.OVERDUE
            task.save(update_fields=('status',))
            Notification.objects.get_or_create(
                business=task.business, task=task, recipient=task.assigned_user
            )
            count += 1
    return count
