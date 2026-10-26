from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Business,
    EmailCampaign,
    EmailCampaignRecipient,
    Membership,
    PasswordResetRequest,
    PendingRegistration,
    User,
)


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'timezone', 'is_active', 'created_at')
    list_filter = ('industry', 'is_active')
    search_fields = ('name',)


@admin.register(User)
class CueinUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Cuein', {'fields': ('business', 'role', 'phone', 'profile_picture')}),
        ('Email verification', {'fields': ('email_verified_at', 'email_verification_sent_at')}),
    )
    readonly_fields = ('email_verified_at', 'email_verification_sent_at')
    list_display = ('username', 'email', 'business', 'role', 'email_verified_at', 'is_staff', 'is_active')
    list_filter = ('role', 'business', 'is_staff', 'is_active')


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'business', 'role', 'is_active', 'joined_at')
    list_filter = ('role', 'is_active', 'business')
    search_fields = ('user__username', 'user__email', 'business__name')
    autocomplete_fields = ('user', 'business')


@admin.register(PendingRegistration)
class PendingRegistrationAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'email', 'username', 'created_at', 'verification_sent_at')
    search_fields = ('business_name', 'email', 'username')
    readonly_fields = ('password', 'created_at', 'verification_sent_at')


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'sent_at', 'attempts', 'created_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('code_hash', 'created_at', 'sent_at', 'attempts')
    autocomplete_fields = ('user',)


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ('subject', 'business', 'status', 'recipient_count', 'sent_count', 'failed_count', 'suppressed_count', 'created_at')
    list_filter = ('status', 'business')
    search_fields = ('subject', 'body', 'business__name')
    readonly_fields = ('recipient_emails', 'recipient_count', 'sent_count', 'failed_count', 'suppressed_count', 'created_at', 'started_at', 'sent_at', 'completed_at')
    autocomplete_fields = ('business', 'created_by', 'filter_assigned_user')


@admin.register(EmailCampaignRecipient)
class EmailCampaignRecipientAdmin(admin.ModelAdmin):
    list_display = ('email', 'campaign', 'status', 'sent_at')
    list_filter = ('status',)
    search_fields = ('email', 'customer_name', 'campaign__subject')
    readonly_fields = ('created_at', 'sent_at')
    autocomplete_fields = ('campaign',)
