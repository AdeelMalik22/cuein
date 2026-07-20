from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    BusinessCreateView, BusinessSettingsView, DashboardView, EmailVerificationResendView, EmailVerificationSentView,
    EmailVerificationView, LandingView, LeadActivityCreateView, LeadCreateView,
    LeadDetailView, LeadFollowUpCreateView, LeadListView, LeadNeedsTimeView, LeadStageListView, LeadStageUpdateView,
    LeadSiteVisitCreateView, LeadUpdateView, NotificationListView, NotificationReadView, OnboardingView, ProductDeleteView, ProductEditView, ProductListView, ProfileView, ProtectedLoginView, ReportsView,
    PasswordResetConfirmView, PasswordResetRequestView, ProfilePasswordChangeView, SecuritySettingsView,
    SignupView, SiteVisitCalendarView, SiteVisitCancelView, SiteVisitCompleteView, SiteVisitRescheduleView, TaskCompleteView, TaskListView, TaskRescheduleView, TeamDeleteView, TeamEditView,
    TeamListView, WorkspaceSwitchView,
)

app_name = 'web'

urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('signup/', SignupView.as_view(), name='signup'),
    path('verify-email/sent/', EmailVerificationSentView.as_view(), name='email-verification-sent'),
    path('verify-email/resend/', EmailVerificationResendView.as_view(), name='email-verification-resend'),
    path('verify-email/', EmailVerificationView.as_view(), name='email-verify'),
    path('forgot-password/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('reset-password/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('login/', ProtectedLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('workspaces/switch/', WorkspaceSwitchView.as_view(), name='workspace-switch'),
    path('workspaces/new/', BusinessCreateView.as_view(), name='business-create'),
    path('onboarding/', OnboardingView.as_view(), name='onboarding'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('leads/', LeadListView.as_view(), name='lead-list'),
    path('leads/new/', LeadCreateView.as_view(), name='lead-create'),
    path('leads/stage/<str:stage>/', LeadStageListView.as_view(), name='lead-stage-list'),
    path('leads/<uuid:pk>/', LeadDetailView.as_view(), name='lead-detail'),
    path('leads/<uuid:pk>/edit/', LeadUpdateView.as_view(), name='lead-update'),
    path('leads/<uuid:pk>/stage/', LeadStageUpdateView.as_view(), name='lead-stage'),
    path('leads/<uuid:pk>/needs-time/', LeadNeedsTimeView.as_view(), name='lead-needs-time'),
    path('leads/<uuid:pk>/activities/', LeadActivityCreateView.as_view(), name='lead-activity-create'),
    path('leads/<uuid:pk>/follow-ups/', LeadFollowUpCreateView.as_view(), name='lead-follow-up-create'),
    path('leads/<uuid:pk>/site-visits/', LeadSiteVisitCreateView.as_view(), name='lead-site-visit-create'),
    path('site-visits/', SiteVisitCalendarView.as_view(), name='site-visit-calendar'),
    path('site-visits/<uuid:pk>/reschedule/', SiteVisitRescheduleView.as_view(), name='site-visit-reschedule'),
    path('site-visits/<uuid:pk>/complete/', SiteVisitCompleteView.as_view(), name='site-visit-complete'),
    path('site-visits/<uuid:pk>/cancel/', SiteVisitCancelView.as_view(), name='site-visit-cancel'),
    path('follow-ups/', TaskListView.as_view(), name='task-list'),
    path('follow-ups/<uuid:pk>/complete/', TaskCompleteView.as_view(), name='task-complete'),
    path('follow-ups/<uuid:pk>/reschedule/', TaskRescheduleView.as_view(), name='task-reschedule'),
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/<uuid:pk>/read/', NotificationReadView.as_view(), name='notification-read'),
    path('account/settings/', ProfileView.as_view(), name='account-settings-profile'),
    path('account/settings/security/', SecuritySettingsView.as_view(), name='security-settings'),
    path('account/settings/security/password/', ProfilePasswordChangeView.as_view(), name='security-password-change'),
    # Keep the former profile URLs working for bookmarks while account menu
    # navigation uses the clearer settings structure above.
    path('profile/', ProfileView.as_view(), name='profile'),
    path('profile/password/', ProfilePasswordChangeView.as_view(), name='profile-password-change'),
    path('team/', TeamListView.as_view(), name='team-list'),
    path('team/<uuid:pk>/edit/', TeamEditView.as_view(), name='team-edit'),
    path('team/<uuid:pk>/delete/', TeamDeleteView.as_view(), name='team-delete'),
    path('products/', ProductListView.as_view(), name='product-list'),
    path('products/<uuid:pk>/edit/', ProductEditView.as_view(), name='product-edit'),
    path('products/<uuid:pk>/delete/', ProductDeleteView.as_view(), name='product-delete'),
    path('settings/', BusinessSettingsView.as_view(), name='business-settings'),
    path('reports/', ReportsView.as_view(), name='reports'),
]
