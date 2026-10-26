import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from .validators import validate_iana_timezone


class Business(models.Model):
    """A customer company: the tenant boundary for its data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Industry(models.TextChoices):
        ACCOUNTING = 'accounting', 'Accounting'
        AGRICULTURE = 'agriculture', 'Agriculture'
        ARCHITECTURE = 'architecture', 'Architecture'
        AUTOMOTIVE = 'automotive', 'Automotive'
        BEAUTY_WELLNESS = 'beauty_wellness', 'Beauty & wellness'
        CLEANING = 'cleaning', 'Cleaning services'
        CONSULTING = 'consulting', 'Consulting'
        EDUCATION = 'education', 'Education & training'
        ELECTRICAL = 'electrical', 'Electrical services'
        EVENTS = 'events', 'Events & catering'
        FACILITY_MANAGEMENT = 'facility_management', 'Facility management'
        FURNITURE = 'furniture', 'Furniture'
        HEALTHCARE = 'healthcare', 'Healthcare'
        HOME_IMPROVEMENT = 'home_improvement', 'Home improvement'
        HOSPITALITY = 'hospitality', 'Hospitality'
        INFORMATION_TECHNOLOGY = 'information_technology', 'Information technology'
        INSURANCE = 'insurance', 'Insurance'
        INTERIOR_DESIGN = 'interior_design', 'Interior design'
        LANDSCAPING = 'landscaping', 'Landscaping'
        LEGAL = 'legal', 'Legal services'
        LOGISTICS = 'logistics', 'Logistics & delivery'
        MANUFACTURING = 'manufacturing', 'Manufacturing'
        MARKETING = 'marketing', 'Marketing & advertising'
        MAINTENANCE = 'maintenance', 'Maintenance & repair'
        PLUMBING = 'plumbing', 'Plumbing'
        REAL_ESTATE = 'real_estate', 'Real estate'
        RETAIL = 'retail', 'Retail'
        SOLAR = 'solar', 'Solar'
        CCTV = 'cctv', 'CCTV / Security'
        AC_INSTALLATION = 'ac_installation', 'AC installation'
        CONSTRUCTION = 'construction', 'Construction'
        SECURITY = 'security', 'Security services'
        TELECOMMUNICATIONS = 'telecommunications', 'Telecommunications'
        TRAVEL = 'travel', 'Travel & tourism'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=255)
    industry = models.CharField(
        max_length=32,
        choices=Industry.choices,
        default=Industry.OTHER,
    )
    timezone = models.CharField(
        max_length=64,
        default='Asia/Karachi',
        validators=[validate_iana_timezone],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'businesses'

    def __str__(self):
        return self.name


class TenantScopedQuerySet(models.QuerySet):
    """Query helper for models that belong to a single business."""

    def for_business(self, business):
        return self.filter(business=business)


class TenantScopedModel(models.Model):
    """Abstract base for data that must never cross a business boundary."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s_set',
    )
    objects = TenantScopedQuerySet.as_manager()

    class Meta:
        abstract = True


class User(AbstractUser):
    """A person's global login identity.

    ``business`` and ``role`` are retained temporarily as a legacy bridge for
    existing installations.  New authorization decisions must use
    :class:`Membership`, where both the business and role are scoped to the
    workspace being used.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        MANAGER = 'manager', 'Manager'
        SALESPERSON = 'salesperson', 'Salesperson'

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name='users',
        null=True,
        blank=True,
    )
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.SALESPERSON,
    )
    phone = models.CharField(max_length=32, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_verification_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower('username'),
                name='unique_user_username_case_insensitive',
            ),
            models.UniqueConstraint(
                Lower('email'),
                condition=~Q(email=''),
                name='unique_user_email_case_insensitive',
            ),
        ]

    def __str__(self):
        return self.get_username()


class Membership(models.Model):
    """A person's access and role within one business workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=16, choices=User.Role.choices, default=User.Role.SALESPERSON)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('user', 'business'), name='unique_user_membership_per_business'),
        ]
        indexes = [
            models.Index(fields=('user', 'is_active')),
            models.Index(fields=('business', 'is_active')),
        ]
        ordering = ('joined_at', 'id')

    def __str__(self):
        return f'{self.user} in {self.business} ({self.get_role_display()})'


class PendingRegistration(models.Model):
    """Signup details held only until the owner confirms their email address."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_name = models.CharField(max_length=255)
    industry = models.CharField(max_length=32, choices=Business.Industry.choices, default=Business.Industry.OTHER)
    timezone = models.CharField(
        max_length=64,
        default='Asia/Karachi',
        validators=[validate_iana_timezone],
    )
    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    password = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    verification_code_hash = models.CharField(max_length=128, blank=True)
    verification_attempts = models.PositiveSmallIntegerField(default=0)
    verification_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'pending registration'
        verbose_name_plural = 'pending registrations'
        constraints = [
            models.UniqueConstraint(
                Lower('username'),
                name='unique_pending_registration_username_case_insensitive',
            ),
            models.UniqueConstraint(
                Lower('email'),
                name='unique_pending_registration_email_case_insensitive',
            ),
        ]

    def __str__(self):
        return f'{self.business_name} ({self.email})'


class PasswordResetRequest(models.Model):
    """A single active, expiring password-reset code for one account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='password_reset_request')
    code_hash = models.CharField(max_length=128, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'password reset request'
        verbose_name_plural = 'password reset requests'

    def __str__(self):
        return f'Password reset for {self.user.email or self.user.username}'


class EmailCampaign(TenantScopedModel):
    """A tenant-scoped bulk email with a snapshot of its recipients."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        QUEUED = 'queued', 'Queued'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        PARTIAL = 'partial', 'Partially sent'
        FAILED = 'failed', 'Failed'

    subject = models.CharField(max_length=255)
    preheader = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    html_body = models.TextField(blank=True)
    reply_to = models.EmailField(blank=True)
    filter_stage = models.CharField(max_length=20, blank=True)
    filter_source = models.CharField(max_length=20, blank=True)
    filter_assigned_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_campaign_filters',
    )
    recipient_emails = models.JSONField(default=list)
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    suppressed_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_email_campaigns',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=('business', 'created_at')),
            models.Index(fields=('business', 'status')),
        ]

    def __str__(self):
        return self.subject


class EmailCampaignRecipient(models.Model):
    """Delivery state for one campaign recipient."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        SUPPRESSED = 'suppressed', 'Suppressed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        EmailCampaign,
        on_delete=models.CASCADE,
        related_name='recipients',
    )
    email = models.EmailField()
    customer_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('campaign', 'email'),
                name='unique_email_campaign_recipient',
            ),
        ]
        indexes = [
            models.Index(fields=('campaign', 'status'), name='campaign_recipient_status_idx'),
        ]

    def __str__(self):
        return f'{self.email} · {self.campaign.subject}'


class EmailCampaignAttachment(models.Model):
    """A validated file attached to every message in a campaign."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        EmailCampaign,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='campaign_attachments/%Y/%m/')
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'id')

    def __str__(self):
        return self.original_name


class MailboxConnection(TenantScopedModel):
    """One user's connected mailbox inside a workspace."""

    class Provider(models.TextChoices):
        GMAIL = 'gmail', 'Google / Gmail'
        MICROSOFT = 'microsoft', 'Microsoft 365'
        IMAP = 'imap', 'IMAP / SMTP'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending connection'
        ACTIVE = 'active', 'Active'
        ERROR = 'error', 'Sync error'
        DISCONNECTED = 'disconnected', 'Disconnected'

    connected_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='mailbox_connections')
    provider = models.CharField(max_length=16, choices=Provider.choices)
    account_email = models.EmailField()
    display_name = models.CharField(max_length=255, blank=True)
    is_shared = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    provider_account_id = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    access_token_secret = models.TextField(blank=True)
    refresh_token_secret = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    imap_host = models.CharField(max_length=255, blank=True)
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_use_tls = models.BooleanField(default=True)
    username_secret = models.TextField(blank=True)
    password_secret = models.TextField(blank=True)
    signature = models.TextField(blank=True)
    hidden_domains = models.JSONField(default=list, blank=True)
    hidden_labels = models.JSONField(default=list, blank=True)
    sync_cursor = models.TextField(blank=True)
    webhook_subscription_id = models.CharField(max_length=255, blank=True)
    webhook_verification_secret = models.CharField(max_length=255, blank=True)
    webhook_expires_at = models.DateTimeField(null=True, blank=True)
    backfill_days = models.PositiveSmallIntegerField(default=90)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('business', 'account_email'),
                name='unique_mailbox_email_per_business',
            ),
        ]
        indexes = [
            models.Index(fields=('business', 'status')),
            models.Index(fields=('connected_by', 'status')),
        ]

    def __str__(self):
        return f'{self.account_email} · {self.get_provider_display()}'


class MailTemplate(TenantScopedModel):
    name = models.CharField(max_length=160)
    subject = models.CharField(max_length=998, blank=True)
    body = models.TextField()
    html_body = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='mail_templates')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('business', 'name'), name='unique_mail_template_name_per_business'),
        ]
        indexes = [models.Index(fields=('business', 'is_active', 'name'))]

    def __str__(self):
        return self.name


class EmailThread(TenantScopedModel):
    """A provider-independent conversation in the shared inbox."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        WAITING = 'waiting', 'Waiting'
        CLOSED = 'closed', 'Closed'

    class LastDirection(models.TextChoices):
        INBOUND = 'inbound', 'Inbound'
        OUTBOUND = 'outbound', 'Outbound'

    subject = models.CharField(max_length=998, blank=True)
    normalized_subject = models.CharField(max_length=998, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_email_threads',
    )
    lead = models.ForeignKey(
        'leads.Lead',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_threads',
    )
    last_direction = models.CharField(max_length=16, choices=LastDirection.choices, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.TextField(blank=True)
    message_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=('business', 'status', '-last_message_at')),
            models.Index(fields=('business', 'assigned_to', 'status')),
            models.Index(fields=('business', 'lead', '-last_message_at')),
        ]

    def __str__(self):
        return self.subject or '(no subject)'


class EmailMessage(TenantScopedModel):
    """One inbound or outbound message, retained for threading and audit."""

    class Direction(models.TextChoices):
        INBOUND = 'inbound', 'Inbound'
        OUTBOUND = 'outbound', 'Outbound'

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        DELIVERED = 'delivered', 'Delivered'
        REPLIED = 'replied', 'Replied'
        BOUNCED = 'bounced', 'Bounced'
        FAILED = 'failed', 'Failed'

    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name='messages')
    mailbox = models.ForeignKey(MailboxConnection, on_delete=models.SET_NULL, null=True, related_name='messages')
    provider_message_id = models.CharField(max_length=512, blank=True)
    message_id = models.CharField(max_length=998, blank=True)
    in_reply_to = models.CharField(max_length=998, blank=True)
    references = models.JSONField(default=list, blank=True)
    direction = models.CharField(max_length=16, choices=Direction.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    from_email = models.EmailField()
    to_emails = models.JSONField(default=list, blank=True)
    cc_emails = models.JSONField(default=list, blank=True)
    bcc_emails = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=998, blank=True)
    text_body = models.TextField(blank=True)
    html_body = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    raw_headers = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('mailbox', 'provider_message_id'),
                condition=~Q(provider_message_id=''),
                name='unique_provider_message_per_mailbox',
            ),
        ]
        indexes = [
            models.Index(fields=('business', 'thread', '-created_at')),
            models.Index(fields=('business', 'message_id')),
            models.Index(fields=('business', 'from_email')),
        ]

    def __str__(self):
        return self.subject or self.message_id or str(self.pk)


class EmailMessageAttachment(models.Model):
    message = models.ForeignKey(EmailMessage, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='mail_attachments/%Y/%m/')
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128, blank=True)
    content_id = models.CharField(max_length=255, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    is_inline = models.BooleanField(default=False)

    class Meta:
        ordering = ('id',)


class EmailInternalNote(TenantScopedModel):
    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name='internal_notes')
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name='email_internal_notes')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'id')
        indexes = [models.Index(fields=('business', 'thread', 'created_at'))]


class EmailUnmatched(TenantScopedModel):
    """Inbound messages that need a human decision before CRM matching."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CONVERTED = 'converted', 'Converted'
        IGNORED = 'ignored', 'Ignored'

    mailbox = models.ForeignKey(MailboxConnection, on_delete=models.CASCADE, related_name='unmatched_messages')
    message = models.OneToOneField(EmailMessage, on_delete=models.CASCADE, related_name='unmatched')
    from_email = models.EmailField()
    subject = models.CharField(max_length=998, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=('business', 'status', '-created_at'))]


class EmailTrackingEvent(TenantScopedModel):
    class Kind(models.TextChoices):
        OPEN = 'open', 'Open'
        CLICK = 'click', 'Click'

    message = models.ForeignKey(EmailMessage, on_delete=models.CASCADE, related_name='tracking_events')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    target_url = models.URLField(blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=('business', 'message', 'kind', '-occurred_at'))]
