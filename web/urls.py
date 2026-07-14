from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    BusinessSettingsView, DashboardView, LandingView, LeadActivityCreateView, LeadCreateView,
    LeadDetailView, LeadFollowUpCreateView, LeadListView, LeadNeedsTimeView, LeadStageUpdateView,
    LeadUpdateView, OnboardingView, ProductDeleteView, ProductEditView, ProductListView, ReportsView,
    SignupView, TaskCompleteView, TaskListView, TaskRescheduleView, TeamDeleteView, TeamEditView,
    TeamListView,
)

app_name = 'web'

urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='web/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('onboarding/', OnboardingView.as_view(), name='onboarding'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('leads/', LeadListView.as_view(), name='lead-list'),
    path('leads/new/', LeadCreateView.as_view(), name='lead-create'),
    path('leads/<uuid:pk>/', LeadDetailView.as_view(), name='lead-detail'),
    path('leads/<uuid:pk>/edit/', LeadUpdateView.as_view(), name='lead-update'),
    path('leads/<uuid:pk>/stage/', LeadStageUpdateView.as_view(), name='lead-stage'),
    path('leads/<uuid:pk>/needs-time/', LeadNeedsTimeView.as_view(), name='lead-needs-time'),
    path('leads/<uuid:pk>/activities/', LeadActivityCreateView.as_view(), name='lead-activity-create'),
    path('leads/<uuid:pk>/follow-ups/', LeadFollowUpCreateView.as_view(), name='lead-follow-up-create'),
    path('follow-ups/', TaskListView.as_view(), name='task-list'),
    path('follow-ups/<uuid:pk>/complete/', TaskCompleteView.as_view(), name='task-complete'),
    path('follow-ups/<uuid:pk>/reschedule/', TaskRescheduleView.as_view(), name='task-reschedule'),
    path('team/', TeamListView.as_view(), name='team-list'),
    path('team/<uuid:pk>/edit/', TeamEditView.as_view(), name='team-edit'),
    path('team/<uuid:pk>/delete/', TeamDeleteView.as_view(), name='team-delete'),
    path('products/', ProductListView.as_view(), name='product-list'),
    path('products/<uuid:pk>/edit/', ProductEditView.as_view(), name='product-edit'),
    path('products/<uuid:pk>/delete/', ProductDeleteView.as_view(), name='product-delete'),
    path('settings/', BusinessSettingsView.as_view(), name='business-settings'),
    path('reports/', ReportsView.as_view(), name='reports'),
]
