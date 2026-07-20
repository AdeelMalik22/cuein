from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from core.models import TenantScopedModel, User
from core.tenancy import belongs_to_business


class Product(TenantScopedModel):
    """A product or service offered by one business."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower('name'),
                'business',
                name='unique_product_name_per_business',
            ),
        ]
        indexes = [
            models.Index(fields=('business', 'is_active')),
        ]

    def __str__(self):
        return self.name


class Lead(TenantScopedModel):
    """A potential customer, always owned by one business and one user."""

    class Source(models.TextChoices):
        REFERRAL = 'referral', 'Referral'
        FACEBOOK = 'facebook', 'Facebook'
        INSTAGRAM = 'instagram', 'Instagram'
        WALK_IN = 'walk_in', 'Walk-in'
        WEBSITE = 'website', 'Website'
        PHONE_CALL = 'phone_call', 'Phone call'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        OTHER = 'other', 'Other'

    class Stage(models.TextChoices):
        NEW_INQUIRY = 'new_inquiry', 'New inquiry'
        CONTACTED = 'contacted', 'Contacted'
        SITE_VISIT = 'site_visit', 'Site visit'
        QUOTATION_SENT = 'quotation_sent', 'Quotation sent'
        NEGOTIATION = 'negotiation', 'Negotiation'
        WON = 'won', 'Won'
        LOST = 'lost', 'Lost'

    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.OTHER)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        related_name='leads',
        null=True,
        blank=True,
    )
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.NEW_INQUIRY)
    quoted_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    assigned_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='leads',
    )
    lost_reason = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=('business', 'stage')),
            models.Index(
                fields=('business', 'stage', '-last_activity_at'),
                name='lead_biz_stage_activity_idx',
            ),
            models.Index(fields=('business', 'assigned_user', 'last_activity_at')),
            models.Index(fields=('business', 'created_at')),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quoted_price__isnull=True) | Q(quoted_price__gte=0),
                name='lead_quoted_price_is_not_negative',
            ),
        ]

    def clean(self):
        errors = {}
        if self.stage == self.Stage.LOST and not self.lost_reason.strip():
            errors['lost_reason'] = 'A lost lead requires a reason.'
        if self.assigned_user_id and self.business_id and not belongs_to_business(
            self.assigned_user_id,
            self.business_id,
        ):
            errors['assigned_user'] = 'The assigned user must belong to the same business.'
        if self.product_id and self.business_id and self.product.business_id != self.business_id:
            errors['product'] = 'The product must belong to the same business.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.customer_name} ({self.get_stage_display()})'

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SiteVisit(TenantScopedModel):
    """A scheduled on-site appointment for one lead.

    A lead may need more than one visit (for example, an initial inspection and
    a later measurement), so visits are records of their own rather than
    mutable fields on ``Lead``.
    """

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='site_visits')
    scheduled_at = models.DateTimeField()
    address = models.TextField(blank=True)
    assigned_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='site_visits',
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    reminder_enabled = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=('business', 'status', 'scheduled_at'), name='visit_biz_status_time_idx'),
            models.Index(fields=('business', 'assigned_user', 'scheduled_at'), name='visit_biz_user_time_idx'),
            models.Index(fields=('business', 'lead', 'scheduled_at'), name='visit_biz_lead_time_idx'),
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
        if self.status == self.Status.SCHEDULED and (self.completed_at or self.cancelled_at):
            errors['status'] = 'A scheduled visit cannot have a completion or cancellation time.'
        elif self.status == self.Status.COMPLETED:
            if not self.completed_at:
                errors['completed_at'] = 'Completed visits require a completion time.'
            if self.cancelled_at:
                errors['cancelled_at'] = 'A completed visit cannot have a cancellation time.'
        elif self.status == self.Status.CANCELLED:
            if not self.cancelled_at:
                errors['cancelled_at'] = 'Cancelled visits require a cancellation time.'
            if self.completed_at:
                errors['completed_at'] = 'A cancelled visit cannot have a completion time.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'Site visit for {self.lead} at {self.scheduled_at:%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Activity(TenantScopedModel):
    """A small, append-only record of what happened with a lead."""

    class Kind(models.TextChoices):
        NOTE = 'note', 'Note'
        CALL = 'call', 'Call'
        SITE_VISIT = 'site_visit', 'Site visit'
        QUOTATION = 'quotation', 'Quotation'
        STAGE_CHANGE = 'stage_change', 'Stage change'
        SYSTEM = 'system', 'System'

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='activities')
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.NOTE)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='lead_activities',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('business', 'lead', 'created_at')),
        ]

    def clean(self):
        errors = {}
        if self.business_id and self.lead_id and self.lead.business_id != self.business_id:
            errors['lead'] = 'The lead must belong to the same business.'
        if self.business_id and self.created_by_id and not belongs_to_business(
            self.created_by_id,
            self.business_id,
        ):
            errors['created_by'] = 'The activity author must belong to the same business.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.get_kind_display()} for {self.lead}'

# Create your models here.
