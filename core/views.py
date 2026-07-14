from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .email_verification import (
    activate_pending_registration,
    EmailVerificationDeliveryError,
    EmailVerificationError,
    send_email_verification,
)
from .models import Business, PendingRegistration, User
from .permissions import IsBusinessOwner
from .serializers import (
    BusinessSerializer,
    CurrentUserSerializer,
    ResendVerificationCodeSerializer,
    SignupSerializer,
    TeamUserSerializer,
    VerifyEmailCodeSerializer,
)


class CurrentUserView(APIView):
    """Return the authenticated user and the business that scopes their data."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)


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

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'user': CurrentUserSerializer(user).data,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
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


class CurrentBusinessView(APIView):
    """Read or update the authenticated user's own tenant only."""

    permission_classes = (IsAuthenticated,)

    def get_business(self, request):
        return get_object_or_404(Business, pk=request.user.business_id, is_active=True)

    def get(self, request):
        return Response(BusinessSerializer(self.get_business(request)).data)

    def patch(self, request):
        if request.user.role != User.Role.OWNER:
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
        return User.objects.filter(business=self.request.user.business).order_by('id')

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise ValidationError({'detail': 'You cannot delete your own account.'})
        if (
            instance.role == User.Role.OWNER
            and User.objects.filter(
                business=instance.business,
                role=User.Role.OWNER,
                is_active=True,
            ).count() == 1
        ):
            raise ValidationError({'detail': 'A business must keep at least one active owner.'})
        instance.delete()
