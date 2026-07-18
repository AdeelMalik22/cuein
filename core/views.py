from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .authentication import select_token_membership, token_pair_for_membership
from .email_verification import (
    activate_pending_registration,
    EmailVerificationCooldownError,
    EmailVerificationDeliveryError,
    EmailVerificationError,
    send_email_verification,
)
from .membership_services import LastActiveOwnerError, remove_membership
from .password_reset import (
    PasswordResetCooldownError,
    PasswordResetDeliveryError,
    PasswordResetError,
    reset_password,
    send_password_reset_code,
)
from .models import Membership, PendingRegistration, User
from .permissions import IsBusinessOwner
from .serializers import (
    BusinessSerializer,
    CurrentUserSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ResendVerificationCodeSerializer,
    SignupSerializer,
    TeamUserSerializer,
    VerifyEmailCodeSerializer,
)
from .tenancy import active_business, active_role, users_for_business


class CurrentUserView(APIView):
    """Return the authenticated user and the business that scopes their data."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(CurrentUserSerializer(request.user, context={'request': request}).data)


class PublicAuthAPIView(APIView):
    """Public API endpoint with a configured, per-IP DRF rate limit."""

    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)


class SignupView(PublicAuthAPIView):
    """Send verification for a registration before creating its tenant or owner."""

    throttle_scope = 'auth_signup'

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                registration = serializer.save()
                send_email_verification(registration)
        except EmailVerificationDeliveryError:
            return Response(
                {'detail': 'We could not send the verification code. Please try again in a moment.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'detail': 'Verification code sent. Enter the six-digit code before signing in.',
                'email': registration.email,
                'verification_required': True,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailCodeView(PublicAuthAPIView):
    """Complete a pending signup after the owner enters the emailed six-digit code."""

    throttle_scope = 'auth_email_verify'

    def post(self, request):
        serializer = VerifyEmailCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = PendingRegistration.objects.filter(email__iexact=serializer.validated_data['email']).first()
        if not registration:
            return Response(
                {'detail': 'That email address or verification code is not valid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = activate_pending_registration(registration, serializer.validated_data['code'])
        except EmailVerificationError:
            return Response(
                {'detail': 'That email address or verification code is not valid, has expired, or has already been used.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership = select_token_membership(user)
        return Response(
            {
                'user': CurrentUserSerializer(user, context={'membership': membership}).data,
                **token_pair_for_membership(user, membership),
            },
            status=status.HTTP_201_CREATED,
        )


class ResendVerificationCodeView(PublicAuthAPIView):
    """Send a fresh code for a pending API or web registration."""

    throttle_scope = 'auth_email_resend'

    def post(self, request):
        serializer = ResendVerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = PendingRegistration.objects.filter(email__iexact=serializer.validated_data['email']).first()
        if not registration:
            return Response({'detail': 'If a pending registration uses that email, a code is on its way.'})

        try:
            send_email_verification(registration)
        except EmailVerificationCooldownError:
            # Match the unknown-address response so resend requests cannot
            # reveal whether an address has a pending registration.
            pass
        except EmailVerificationDeliveryError:
            return Response(
                {'detail': 'We could not send the verification code. Please try again in a moment.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'detail': 'If a pending registration uses that email, a code is on its way.'})


class PasswordResetRequestView(PublicAuthAPIView):
    """Email a reset code without revealing whether the account exists."""

    throttle_scope = 'auth_password_reset_request'

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data['email'],
            is_active=True,
        ).first()
        if user:
            try:
                send_password_reset_code(user)
            except (PasswordResetCooldownError, PasswordResetDeliveryError, PasswordResetError):
                # The public response must remain the same, otherwise this
                # endpoint would disclose which email addresses have accounts.
                pass
        return Response({'detail': 'If an active account uses that email, a six-digit reset code is on its way.'})


class PasswordResetConfirmView(PublicAuthAPIView):
    """Set a new password after a valid emailed reset code is supplied."""

    throttle_scope = 'auth_password_reset_confirm'

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reset_password(
                serializer.validated_data['email'],
                serializer.validated_data['code'],
                serializer.validated_data['new_password'],
            )
        except PasswordResetError:
            return Response(
                {'detail': 'That email address or reset code is not valid, has expired, or has already been used.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'detail': 'Your password has been reset. You can now sign in.'})


class CurrentBusinessView(APIView):
    """Read or update the authenticated user's own tenant only."""

    permission_classes = (IsAuthenticated,)

    def get_business(self, request):
        return active_business(request)

    def get(self, request):
        return Response(BusinessSerializer(self.get_business(request)).data)

    def patch(self, request):
        if active_role(request) != User.Role.OWNER:
            return Response(
                {'detail': 'Only a business owner can update business settings.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = BusinessSerializer(self.get_business(request), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class TeamUserViewSet(viewsets.ModelViewSet):
    """Owner-only user management, always limited to the requester's tenant."""

    serializer_class = TeamUserSerializer
    permission_classes = (IsBusinessOwner,)

    def get_queryset(self):
        return users_for_business(active_business(self.request)).order_by('id')

    def perform_create(self, serializer):
        serializer.save(business=active_business(self.request))

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise ValidationError({'detail': 'You cannot delete your own account.'})
        business = active_business(self.request)
        with transaction.atomic():
            membership = get_object_or_404(
                Membership,
                user=instance,
                business=business,
            )
            try:
                # Removing a person from one workspace must never delete their
                # global login or their memberships in other businesses.
                remove_membership(membership_id=membership.id, business=business)
            except LastActiveOwnerError as error:
                raise ValidationError({'detail': str(error)}) from error
