from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TenantScopedModel
from core.tenancy import belongs_to_business
from leads.models import Lead


class FollowUpTask(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        OVERDUE = 'overdue', 'Overdue'
        DONE = 'done', 'Done'
        CANCELLED = 'cancelled', 'Cancelled'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='follow_up_tasks')
    assigned_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='follow_up_tasks')
    due_at = models.DateTimeField()
    description = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    rule_key = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=('business', 'assigned_user', 'due_at', 'status')),
            models.Index(fields=('business', 'lead', 'status')),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('business', 'lead', 'rule_key'),
                condition=Q(status__in=('pending', 'overdue')) & ~Q(rule_key=''),
                name='unique_open_automated_followup_per_rule',
            ),
        ]

    def clean(self):
        errors = {}
        if self.business_id and self.lead_id and self.lead.business_id != self.business_id:
            errors['lead'] = 'The lead must belong to the same business.'
        if self.business_id and self.assigned_user_id and not belongs_to_business(
            self.assigned_user_id,
            self.business_id,
        ):
            errors['assigned_user'] = 'The assigned user must belong to the same business.'
        if self.status == self.Status.DONE and not self.completed_at:
            errors['completed_at'] = 'Completed tasks require a completion timestamp.'
        if self.status != self.Status.DONE and self.completed_at:
            errors['completed_at'] = 'Only completed tasks may have a completion timestamp.'
        if errors:
            raise ValidationError(errors)

    def mark_done(self):
        self.status = self.Status.DONE
        self.completed_at = timezone.now()


class Notification(TenantScopedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    task = models.ForeignKey(FollowUpTask, on_delete=models.CASCADE, related_name='notifications')
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=('business', 'recipient', 'read_at', 'created_at'))]
        constraints = [
            models.UniqueConstraint(fields=('task', 'recipient'), name='unique_notification_per_task_recipient'),
        ]

    def clean(self):
        errors = {}
        if self.business_id and self.task_id and self.task.business_id != self.business_id:
            errors['task'] = 'The task must belong to the same business.'
        if self.business_id and self.recipient_id and not belongs_to_business(
            self.recipient_id,
            self.business_id,
        ):
            errors['recipient'] = 'The recipient must belong to the same business.'
        if errors:
            raise ValidationError(errors)
