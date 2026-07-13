from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import Business, User


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = ('id', 'name', 'industry', 'timezone', 'is_active', 'created_at')
        read_only_fields = ('id', 'is_active', 'created_at')


class CurrentUserSerializer(serializers.ModelSerializer):
    business = BusinessSerializer(read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'phone', 'role', 'business')


class TeamUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active', 'password')
        read_only_fields = ('id',)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if not self.instance and not attrs.get('password'):
            raise serializers.ValidationError({'password': 'This field is required when creating a user.'})
        if self.instance and self.instance.role == User.Role.OWNER:
            new_role = attrs.get('role', self.instance.role)
            owner_count = User.objects.filter(
                business=self.instance.business,
                role=User.Role.OWNER,
                is_active=True,
            ).count()
            if new_role != User.Role.OWNER and owner_count == 1:
                raise serializers.ValidationError({'role': 'A business must keep at least one active owner.'})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class SignupSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=255)
    industry = serializers.ChoiceField(choices=Business.Industry.choices, default=Business.Industry.OTHER)
    timezone = serializers.CharField(max_length=64, default='Asia/Karachi')
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('This username is already in use.')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        business = Business.objects.create(
            name=validated_data.pop('business_name'),
            industry=validated_data.pop('industry'),
            timezone=validated_data.pop('timezone'),
        )
        return User.objects.create_user(
            business=business,
            role=User.Role.OWNER,
            **validated_data,
        )
