from django.contrib.auth.models import AbstractUser
from django.db import models


class Business(models.Model):
    """A customer company: the tenant boundary for its data."""

    class Industry(models.TextChoices):
        SOLAR = 'solar', 'Solar'
        CCTV = 'cctv', 'CCTV / Security'
        AC_INSTALLATION = 'ac_installation', 'AC installation'
        REAL_ESTATE = 'real_estate', 'Real estate'
        CONSTRUCTION = 'construction', 'Construction'
        FURNITURE = 'furniture', 'Furniture'
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


class User(AbstractUser):
    """An authenticated team member belonging to exactly one business in v1."""

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

    def __str__(self):
        return self.get_username()
