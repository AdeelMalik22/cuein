"""JWT token endpoint kept separate from configured authentication classes."""

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .authentication import select_token_membership, token_pair_for_membership


class BusinessTokenObtainPairSerializer(TokenObtainPairSerializer):
    business_id = serializers.UUIDField(required=False, write_only=True)

    def validate(self, attrs):
        data = super().validate(attrs)
        membership = select_token_membership(self.user, attrs.get('business_id'))
        data.update(token_pair_for_membership(self.user, membership))
        return data


class BusinessTokenObtainPairView(TokenObtainPairView):
    serializer_class = BusinessTokenObtainPairSerializer
