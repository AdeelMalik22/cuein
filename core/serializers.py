from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .authentication import revoke_refresh_tokens_for_user
from .models import Business, Membership, PendingRegistration, User
from .tenancy import active_business, default_active_membership_for, request_membership
from .validators import validate_iana_timezone


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = ('id', 'name', 'industry', 'timezone', 'is_active', 'created_at')
        read_only_fields = ('id', 'is_active', 'created_at')


class WorkspaceMembershipSerializer(serializers.ModelSerializer):
    business = BusinessSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ('id', 'business', 'role', 'is_active', 'joined_at')
        read_only_fields = fields


class CurrentUserSerializer(serializers.ModelSerializer):
    business = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    memberships = serializers.SerializerMethodField()

    def _active_membership(self, user):
        membership = self.context.get('membership')
        if membership is not None:
            return membership
        request = self.context.get('request')
        if request is not None:
            return request_membership(request)
        return default_active_membership_for(user)

    def get_business(self, user):
        membership = self._active_membership(user)
        return BusinessSerializer(membership.business).data if membership else None

    def get_role(self, user):
        membership = self._active_membership(user)
        return membership.role if membership else None

    def get_memberships(self, user):
        memberships = Membership.objects.filter(
            user=user,
            is_active=True,
            business__is_active=True,
        ).select_related('business').order_by('joined_at', 'id')
        return WorkspaceMembershipSerializer(memberships, many=True).data

    class Meta:
        model = User
        fields = (
            'id', 'username', 'first_name', 'last_name', 'email', 'phone',
            'profile_picture', 'role', 'business', 'memberships',
        )


class TeamUserSerializer(serializers.ModelSerializer):
    """Manage a person's membership without exposing their global account."""

    GLOBAL_ACCOUNT_FIELDS = frozenset(
        ('username', 'first_name', 'last_name', 'email', 'phone', 'password'),
    )

    password = serializers.CharField(write_only=True, required=False, style={'input_type': 'password'})
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active', 'password')
        read_only_fields = ('id',)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_username(self, value):
        username = value.strip()
        existing_users = User.objects.exclude(pk=getattr(self.instance, 'pk', None))
        if existing_users.filter(username__iexact=username).exists() or PendingRegistration.objects.filter(
            username__iexact=username,
        ).exists():
            raise serializers.ValidationError('This username is already in use.')
        return username

    def validate_email(self, value):
        email = value.strip().lower()
        if not email:
            return email
        existing_users = User.objects.exclude(pk=getattr(self.instance, 'pk', None))
        if existing_users.filter(email__iexact=email).exists() or PendingRegistration.objects.filter(
            email__iexact=email,
        ).exists():
            raise serializers.ValidationError('An account already uses this email.')
        return email

    def validate(self, attrs):
        if not self.instance and not attrs.get('password'):
            raise serializers.ValidationError({'password': 'This field is required when creating a user.'})
        if self.instance:
            global_account_fields = self.GLOBAL_ACCOUNT_FIELDS.intersection(attrs)
            if global_account_fields:
                raise serializers.ValidationError({
                    field: 'This is a global account field. The account owner must change it themselves.'
                    for field in global_account_fields
                })
            business = active_business(self.context['request'])
            membership = Membership.objects.get(user=self.instance, business=business)
            new_role = attrs.get('role', membership.role)
            new_is_active = attrs.get('is_active', membership.is_active)
            if membership.role == User.Role.OWNER and membership.is_active and (
                new_role != User.Role.OWNER or not new_is_active
            ):
                owner_count = Membership.objects.filter(
                    business=business,
                    role=User.Role.OWNER,
                    is_active=True,
                    user__is_active=True,
                ).count()
                if owner_count == 1:
                    raise serializers.ValidationError({'role': 'A business must keep at least one active owner.'})
        return attrs

    def create(self, validated_data):
        business = validated_data.pop('business')
        password = validated_data.pop('password')
        role = validated_data.pop('role', User.Role.SALESPERSON)
        membership_is_active = validated_data.pop('is_active', True)
        # Populate the legacy fields for a brand-new account too.  They are
        # not used for authorization, but keep older integrations functional
        # until the bridge can be removed in a later migration.
        with transaction.atomic():
            user = User.objects.create_user(
                password=password,
                business=business,
                role=role,
                is_active=True,
                **validated_data,
            )
            Membership.objects.create(
                user=user,
                business=business,
                role=role,
                is_active=membership_is_active,
            )
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        role = validated_data.pop('role', None)
        membership_is_active = validated_data.pop('is_active', None)
        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        if password:
            instance.set_password(password)
        if membership_is_active is True:
            # Preserve the old reactivation behavior while membership status
            # becomes workspace-scoped.  Deactivating one membership never
            # disables a person's other workspaces.
            instance.is_active = True
        instance.save()
        if password:
            revoke_refresh_tokens_for_user(instance)
        membership = Membership.objects.get(
            user=instance,
            business=active_business(self.context['request']),
        )
        update_fields = []
        if role is not None:
            membership.role = role
            update_fields.append('role')
        if membership_is_active is not None:
            membership.is_active = membership_is_active
            update_fields.append('is_active')
        if update_fields:
            membership.save(update_fields=update_fields)
        return instance

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        membership = Membership.objects.get(
            user=instance,
            business=active_business(self.context['request']),
        )
        representation['role'] = membership.role
        representation['is_active'] = membership.is_active
        return representation


class SignupSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=255)
    industry = serializers.ChoiceField(choices=Business.Industry.choices, default=Business.Industry.OTHER)
    timezone = serializers.CharField(max_length=64, default='Asia/Karachi')
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)

    def validate_username(self, value):
        username = value.strip()
        if User.objects.filter(username__iexact=username).exists() or PendingRegistration.objects.filter(
            username__iexact=username,
        ).exists():
            raise serializers.ValidationError('This username is already in use.')
        return username

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_timezone(self, value):
        value = value.strip()
        try:
            validate_iana_timezone(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return value

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists() or PendingRegistration.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError('An account already uses this email.')
        return email

    def create(self, validated_data):
        password = validated_data.pop('password')
        return PendingRegistration.objects.create(
            business_name=validated_data.pop('business_name'),
            industry=validated_data.pop('industry'),
            timezone=validated_data.pop('timezone'),
            password=make_password(password),
            **validated_data,
        )


class VerifyEmailCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.RegexField(regex=r'^\d{6}$', max_length=6)

    def validate_email(self, value):
        return value.strip().lower()


class ResendVerificationCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.RegexField(regex=r'^\d{6}$', max_length=6)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.strip().lower()

    def validate_new_password(self, value):
        validate_password(value)
        return value
