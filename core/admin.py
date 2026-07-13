from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Business, User


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'timezone', 'is_active', 'created_at')
    list_filter = ('industry', 'is_active')
    search_fields = ('name',)


@admin.register(User)
class CueinUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Cuein', {'fields': ('business', 'role', 'phone')}),
    )
    list_display = ('username', 'email', 'business', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'business', 'is_staff', 'is_active')
