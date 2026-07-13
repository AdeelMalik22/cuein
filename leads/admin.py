from django.contrib import admin

from .models import Lead, Product


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

# Register your models here.
