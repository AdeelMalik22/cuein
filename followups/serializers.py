from django.utils import timezone
from rest_framework import serializers

from core.models import User
from core.tenancy import active_business, is_active_member_of_business
from leads.models import Lead

from .models import FollowUpTask, Notification


class FollowUpTaskSerializer(serializers.ModelSerializer):
    assigned_user_name = serializers.CharField(source='assigned_user.username', read_only=True)
    lead_name = serializers.CharField(source='lead.customer_name', read_only=True)
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())
    assigned_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)

    class Meta:
        model = FollowUpTask
        fields = (
            'id', 'lead', 'lead_name', 'assigned_user', 'assigned_user_name', 'due_at',
            'description', 'status', 'rule_key', 'created_at', 'completed_at',
        )
        read_only_fields = ('id', 'status', 'rule_key', 'created_at', 'completed_at')

    def validate(self, attrs):
        business = active_business(self.context['request'])
        lead = attrs.get('lead', getattr(self.instance, 'lead', None))
        assignee = attrs.get('assigned_user', getattr(self.instance, 'assigned_user', None))
        if lead and lead.business_id != business.id:
            raise serializers.ValidationError({'lead': 'Select a lead from your business.'})
        if assignee and not is_active_member_of_business(assignee, business.id):
            raise serializers.ValidationError({'assigned_user': 'The assigned user must be active.'})
        return attrs


class CompleteTaskSerializer(serializers.Serializer):
    next_due_at = serializers.DateTimeField()
    next_description = serializers.CharField()

    def validate_next_due_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('The next action must be scheduled in the future.')
        return value


class RescheduleTaskSerializer(serializers.Serializer):
    due_at = serializers.DateTimeField()

    def validate_due_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('The new due time must be in the future.')
        return value


class NotificationSerializer(serializers.ModelSerializer):
    task = FollowUpTaskSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = ('id', 'task', 'read_at', 'created_at')
        read_only_fields = fields
