from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .token_views import BusinessTokenObtainPairView, BusinessTokenRefreshView

from .views import (
    CurrentBusinessView,
    CurrentUserView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    ResendVerificationCodeView,
    SignupView,
    TeamUserViewSet,
    VerifyEmailCodeView,
)

router = DefaultRouter()
router.register('users', TeamUserViewSet, basename='user')

urlpatterns = [
    path('auth/signup/', SignupView.as_view(), name='signup'),
    path('auth/verify-email/', VerifyEmailCodeView.as_view(), name='email_verify'),
    path('auth/verify-email/resend/', ResendVerificationCodeView.as_view(), name='email_verify_resend'),
    path('auth/password-reset/request/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('auth/token/', BusinessTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', BusinessTokenRefreshView.as_view(), name='token_refresh'),
    path('me/', CurrentUserView.as_view(), name='current_user'),
    path('business/', CurrentBusinessView.as_view(), name='current_business'),
    path('', include(router.urls)),
]
