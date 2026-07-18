from rest_framework import serializers
from django.urls import reverse

from core.models import User
from core.tenancy import active_business, active_role, is_active_member_of_business

from .models import Activity, Lead, Product
from .services import record_manual_activity


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ('id', 'name', 'description', 'is_active', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate_name(self, value):
        request = self.context['request']
        products = Product.objects.for_business(active_business(request)).filter(name__iexact=value)
        if self.instance:
            products = products.exclude(pk=self.instance.pk)
        if products.exists():
            raise serializers.ValidationError('A product with this name already exists in this business.')
        return value


class LeadSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    assigned_user_name = serializers.CharField(source='assigned_user.username', read_only=True)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    assigned_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)

    class Meta:
        model = Lead
        fields = (
            'id',
            'customer_name',
            'phone',
            'email',
            'source',
            'product',
            'product_name',
            'stage',
            'quoted_price',
            'assigned_user',
            'assigned_user_name',
            'lost_reason',
            'last_activity_at',
            'closed_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'stage', 'lost_reason', 'last_activity_at', 'closed_at', 'created_at', 'updated_at')

    def validate(self, attrs):
        request = self.context['request']
        business = active_business(request)

        if 'stage' in self.initial_data or 'lost_reason' in self.initial_data:
            raise serializers.ValidationError(
                'Use the lead transition endpoint to change a stage or lost reason.'
            )

        product = attrs.get('product')
        if product and product.business_id != business.id:
            raise serializers.ValidationError({'product': 'Select a product from your business.'})

        assigned_user = attrs.get('assigned_user')
        if assigned_user and not is_active_member_of_business(assigned_user, business.id):
            raise serializers.ValidationError({'assigned_user': 'The assigned user must be active.'})
        return attrs


class LeadKanbanCardSerializer(serializers.ModelSerializer):
    """The intentionally small payload used when the board loads more cards."""

    product_name = serializers.CharField(source='product.name', read_only=True, default=None)
    detail_url = serializers.SerializerMethodField()
    transition_url = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = (
            'id',
            'customer_name',
            'product_name',
            'stage',
            'quoted_price',
            'last_activity_at',
            'detail_url',
            'transition_url',
        )

    def get_detail_url(self, lead):
        return reverse('web:lead-detail', kwargs={'pk': lead.pk})

    def get_transition_url(self, lead):
        return reverse('web:lead-stage', kwargs={'pk': lead.pk})


class LeadTransitionSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Lead.Stage.choices)
    lost_reason = serializers.CharField(required=False, allow_blank=False, trim_whitespace=True)

    def validate(self, attrs):
        stage = attrs['stage']
        lost_reason = attrs.get('lost_reason', '')
        if stage == Lead.Stage.LOST and not lost_reason:
            raise serializers.ValidationError({'lost_reason': 'A lost lead requires a reason.'})
        if stage != Lead.Stage.LOST and 'lost_reason' in attrs:
            raise serializers.ValidationError({'lost_reason': 'A lost reason is only valid for a lost lead.'})
        return attrs


class LeadAssignmentSerializer(serializers.Serializer):
    assigned_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    def validate_assigned_user(self, user):
        request = self.context['request']
        if not is_active_member_of_business(user, active_business(request).id):
            raise serializers.ValidationError('The assigned user must be active.')
        return user


class ActivitySerializer(serializers.ModelSerializer):
    """The tenant-safe, user-entered portion of a lead's timeline."""

    lead_name = serializers.CharField(source='lead.customer_name', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())

    class Meta:
        model = Activity
        fields = (
            'id', 'lead', 'lead_name', 'kind', 'content', 'metadata',
            'created_by', 'created_by_name', 'created_at',
        )
        read_only_fields = ('id', 'metadata', 'created_by', 'created_by_name', 'created_at')

    def validate(self, attrs):
        request = self.context['request']
        business = active_business(request)
        lead = attrs['lead']
        if lead.business_id != business.id:
            raise serializers.ValidationError({'lead': 'Select a lead from your business.'})
        if active_role(request) == User.Role.SALESPERSON and lead.assigned_user_id != request.user.id:
            raise serializers.ValidationError({'lead': 'You can only add activity to your own leads.'})
        if attrs.get('kind', Activity.Kind.NOTE) in (Activity.Kind.SYSTEM, Activity.Kind.STAGE_CHANGE):
            raise serializers.ValidationError({'kind': 'System timeline events cannot be created manually.'})
        return attrs

    def get_created_by_name(self, activity):
        return activity.created_by.username if activity.created_by else None

    def create(self, validated_data):
        return record_manual_activity(
            lead=validated_data['lead'],
            actor=self.context['request'].user,
            kind=validated_data.get('kind', Activity.Kind.NOTE),
            content=validated_data['content'],
        )
