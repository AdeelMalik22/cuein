from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Business,
    EmailCampaign,
    EmailCampaignAttachment,
    EmailCampaignRecipient,
    EmailInternalNote,
    EmailMessage,
    EmailMessageAttachment,
    EmailThread,
    EmailUnmatched,
    MailboxConnection,
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


@admin.register(EmailCampaignAttachment)
class EmailCampaignAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'campaign', 'content_type', 'size', 'created_at')
    search_fields = ('original_name', 'campaign__subject')
    readonly_fields = ('original_name', 'content_type', 'size', 'created_at')
    autocomplete_fields = ('campaign',)


@admin.register(MailboxConnection)
class MailboxConnectionAdmin(admin.ModelAdmin):
    list_display = ('account_email', 'business', 'provider', 'is_shared', 'status', 'last_sync_at')
    list_filter = ('provider', 'status', 'is_shared', 'business')
    search_fields = ('account_email', 'display_name', 'business__name')
    readonly_fields = ('access_token_secret', 'refresh_token_secret', 'username_secret', 'password_secret', 'created_at', 'updated_at')
    autocomplete_fields = ('business', 'connected_by')


@admin.register(EmailThread)
class EmailThreadAdmin(admin.ModelAdmin):
    list_display = ('subject', 'business', 'status', 'assigned_to', 'lead', 'last_message_at')
    list_filter = ('status', 'business')
    search_fields = ('subject', 'messages__from_email', 'lead__customer_name')
    autocomplete_fields = ('business', 'assigned_to', 'lead')


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ('subject', 'from_email', 'direction', 'status', 'received_at', 'thread')
    list_filter = ('direction', 'status', 'business')
    search_fields = ('subject', 'from_email', 'message_id')
    readonly_fields = ('business', 'thread', 'mailbox', 'message_id', 'provider_message_id', 'created_at')
    autocomplete_fields = ('thread', 'mailbox')


@admin.register(EmailMessageAttachment)
class EmailMessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'message', 'content_type', 'size')
    search_fields = ('filename', 'message__subject')
    readonly_fields = ('filename', 'content_type', 'size')
    autocomplete_fields = ('message',)


@admin.register(EmailInternalNote)
class EmailInternalNoteAdmin(admin.ModelAdmin):
    list_display = ('thread', 'author', 'created_at')
    search_fields = ('body', 'thread__subject')
    autocomplete_fields = ('business', 'thread', 'author')


@admin.register(EmailUnmatched)
class EmailUnmatchedAdmin(admin.ModelAdmin):
    list_display = ('from_email', 'subject', 'business', 'status', 'created_at')
    list_filter = ('status', 'business')
    search_fields = ('from_email', 'subject')
    autocomplete_fields = ('business', 'mailbox', 'message')
