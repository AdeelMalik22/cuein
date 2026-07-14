import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


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
    timezone = models.CharField(max_length=64, default='Asia/Karachi')
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
    """An authenticated team member belonging to exactly one business in v1."""

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
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_verification_sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.get_username()


class PendingRegistration(models.Model):
    """Signup details held only until the owner confirms their email address."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_name = models.CharField(max_length=255)
    industry = models.CharField(max_length=32, choices=Business.Industry.choices, default=Business.Industry.OTHER)
    timezone = models.CharField(max_length=64, default='Asia/Karachi')
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

    def __str__(self):
        return f'{self.business_name} ({self.email})'
