"""JWT token endpoint kept separate from configured authentication classes."""

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenObtainSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .authentication import select_token_membership, token_pair_for_membership
from .captcha import captcha_enabled, verify_turnstile
from .security import clear_login_failures, client_ip, login_attempt_state, record_failed_login


class BusinessTokenObtainPairSerializer(TokenObtainPairSerializer):
    business_id = serializers.UUIDField(required=False, write_only=True)
    captcha_token = serializers.CharField(required=False, write_only=True, trim_whitespace=True)

    def validate(self, attrs):
        request = self.context.get('request')
        credential = str(attrs.get(self.username_field, '')).strip()
        if request and credential:
            state = login_attempt_state(request, credential)
            if state.retry_after:
                raise Throttled(
                    wait=state.retry_after,
                    detail='Too many sign-in attempts. Please try again shortly.',
                )
            if state.captcha_required and captcha_enabled() and not verify_turnstile(
                attrs.get('captcha_token', ''),
                client_ip(request),
            ):
                raise serializers.ValidationError({
                    'captcha_token': 'Complete the security check before signing in.',
                })

        attrs.pop('captcha_token', None)
        try:
            # The stock pair serializer would mint an unscoped pair before we
            # replace it below. Authenticate through its base class instead,
            # then issue exactly one business-scoped pair.
            data = TokenObtainSerializer.validate(self, attrs)
        except AuthenticationFailed:
            if request and credential:
                record_failed_login(request, credential)
            raise

        if request and credential:
            clear_login_failures(request, credential)
        membership = select_token_membership(self.user, attrs.get('business_id'))
        data.update(token_pair_for_membership(self.user, membership))
        return data


class BusinessTokenObtainPairView(TokenObtainPairView):
    serializer_class = BusinessTokenObtainPairSerializer
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'auth_token'


class BusinessTokenRefreshView(TokenRefreshView):
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'auth_token_refresh'
