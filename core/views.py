from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import select_token_membership, token_pair_for_membership
from .email_verification import (
    activate_pending_registration,
    EmailVerificationDeliveryError,
    EmailVerificationError,
    send_email_verification,
)
from .password_reset import (
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


class SignupView(APIView):
    """Send verification for a registration before creating its tenant or owner."""

    permission_classes = (AllowAny,)

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


class VerifyEmailCodeView(APIView):
    """Complete a pending signup after the owner enters the emailed six-digit code."""

    permission_classes = (AllowAny,)

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


class ResendVerificationCodeView(APIView):
    """Send a fresh code for a pending API or web registration."""

    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = ResendVerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = PendingRegistration.objects.filter(email__iexact=serializer.validated_data['email']).first()
        if not registration:
            return Response({'detail': 'If a pending registration uses that email, a code is on its way.'})

        try:
            send_email_verification(registration)
        except EmailVerificationDeliveryError:
            return Response(
                {'detail': 'We could not send the verification code. Please try again in a moment.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'detail': 'If a pending registration uses that email, a code is on its way.'})


class PasswordResetRequestView(APIView):
    """Email a reset code without revealing whether the account exists."""

    permission_classes = (AllowAny,)

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
            except (PasswordResetDeliveryError, PasswordResetError):
                # The public response must remain the same, otherwise this
                # endpoint would disclose which email addresses have accounts.
                pass
        return Response({'detail': 'If an active account uses that email, a six-digit reset code is on its way.'})


class PasswordResetConfirmView(APIView):
    """Set a new password after a valid emailed reset code is supplied."""

    permission_classes = (AllowAny,)

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
        membership = get_object_or_404(
            Membership,
            user=instance,
            business=active_business(self.request),
        )
        if (
            membership.role == User.Role.OWNER
            and membership.is_active
            and Membership.objects.filter(
                business=membership.business,
                role=User.Role.OWNER,
                is_active=True,
                user__is_active=True,
            ).count() == 1
        ):
            raise ValidationError({'detail': 'A business must keep at least one active owner.'})
        # Removing a person from one workspace must never delete their global
        # login or their memberships in other businesses.
        membership.delete()
