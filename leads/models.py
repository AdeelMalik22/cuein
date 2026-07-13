from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TenantScopedModel, User


class Product(TenantScopedModel):
    """A product or service offered by one business."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('business', 'name'), name='unique_product_name_per_business'),
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
        if self.assigned_user_id and self.business_id and self.assigned_user.business_id != self.business_id:
            errors['assigned_user'] = 'The assigned user must belong to the same business.'
        if self.product_id and self.business_id and self.product.business_id != self.business_id:
            errors['product'] = 'The product must belong to the same business.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.customer_name} ({self.get_stage_display()})'

# Create your models here.
