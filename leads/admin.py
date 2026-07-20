from django.contrib import admin

from .models import Lead, Product, SiteVisit


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'is_active', 'created_at')
    list_filter = ('business', 'is_active')
    search_fields = ('name',)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'business', 'stage', 'assigned_user', 'last_activity_at')
    list_filter = ('business', 'stage', 'source')
    search_fields = ('customer_name', 'phone', 'email')


@admin.register(SiteVisit)
class SiteVisitAdmin(admin.ModelAdmin):
    list_display = (
        'lead', 'business', 'assigned_user', 'scheduled_at', 'status',
        'reminder_enabled', 'completed_at', 'cancelled_at',
    )
    list_filter = ('business', 'status', 'reminder_enabled', 'scheduled_at')
    search_fields = ('lead__customer_name', 'lead__phone', 'address', 'assigned_user__username')
    autocomplete_fields = ('lead', 'assigned_user')
    readonly_fields = ('created_at', 'updated_at', 'completed_at', 'cancelled_at')
    ordering = ('-scheduled_at',)

# Register your models here.
