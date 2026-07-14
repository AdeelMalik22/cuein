from django.contrib import admin
from django.utils import timezone

from .models import FollowUpTask, Notification


@admin.register(FollowUpTask)
class FollowUpTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lead",
        "assigned_user",
        "business",
        "status",
        "due_at",
        "completed_at",
        "created_at",
    )
    list_filter = (
        "status",
        "business",
        "due_at",
        "created_at",
    )
    search_fields = (
        "lead__name",
        "lead__email",
        "assigned_user__username",
        "assigned_user__email",
        "description",
        "rule_key",
    )
    autocomplete_fields = (
        "lead",
        "assigned_user",
    )
    readonly_fields = (
        "created_at",
        "completed_at",
    )
    ordering = ("-due_at",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "business",
                    "lead",
                    "assigned_user",
                    "description",
                    "status",
                    "due_at",
                    "rule_key",
                )
            },
        ),
        (
            "Completion",
            {
                "fields": (
                    "completed_at",
                    "created_at",
                )
            },
        ),
    )

    actions = ["mark_selected_done"]

    @admin.action(description="Mark selected tasks as done")
    def mark_selected_done(self, request, queryset):
        updated = 0
        for task in queryset.exclude(status=FollowUpTask.Status.DONE):
            task.status = FollowUpTask.Status.DONE
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at"])
            updated += 1

        self.message_user(request, f"{updated} task(s) marked as done.")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "task",
        "business",
        "created_at",
        "read_at",
    )
    list_filter = (
        "business",
        "read_at",
        "created_at",
    )
    search_fields = (
        "recipient__username",
        "recipient__email",
        "task__description",
        "task__lead__name",
        "task__lead__email",
    )
    autocomplete_fields = (
        "recipient",
        "task",
    )
    readonly_fields = (
        "created_at",
    )
    ordering = ("-created_at",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "business",
                    "recipient",
                    "task",
                )
            },
        ),
        (
            "Status",
            {
                "fields": (
                    "read_at",
                    "created_at",
                )
            },
        ),
    )