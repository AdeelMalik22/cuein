from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Business, PendingRegistration, User


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


@admin.register(PendingRegistration)
class PendingRegistrationAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'email', 'username', 'created_at', 'verification_sent_at')
    search_fields = ('business_name', 'email', 'username')
    readonly_fields = ('password', 'created_at', 'verification_sent_at')
