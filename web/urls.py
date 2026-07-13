from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    BusinessSettingsView, DashboardView, LandingView, LeadListView, ProductListView,
    ProductDeleteView, ProductEditView, ReportsView, SignupView, TaskListView, TeamDeleteView,
    TeamEditView, TeamListView,
)

app_name = 'web'

urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='web/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('leads/', LeadListView.as_view(), name='lead-list'),
    path('follow-ups/', TaskListView.as_view(), name='task-list'),
    path('team/', TeamListView.as_view(), name='team-list'),
    path('team/<uuid:pk>/edit/', TeamEditView.as_view(), name='team-edit'),
    path('team/<uuid:pk>/delete/', TeamDeleteView.as_view(), name='team-delete'),
    path('products/', ProductListView.as_view(), name='product-list'),
    path('products/<uuid:pk>/edit/', ProductEditView.as_view(), name='product-edit'),
    path('products/<uuid:pk>/delete/', ProductDeleteView.as_view(), name='product-delete'),
    path('settings/', BusinessSettingsView.as_view(), name='business-settings'),
    path('reports/', ReportsView.as_view(), name='reports'),
]
