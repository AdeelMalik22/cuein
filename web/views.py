from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash, views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from core.email_verification import (
    activate_pending_registration,
    EmailVerificationCooldownError,
    EmailVerificationDeliveryError,
    EmailVerificationError,
    send_email_verification,
)
from core.authentication import revoke_refresh_tokens_for_user
from core.password_reset import (
    PasswordResetCooldownError,
    PasswordResetDeliveryError,
    PasswordResetError,
    reset_password,
    send_password_reset_code,
)
from core.models import Membership, PendingRegistration, User
from core.captcha import captcha_enabled
from core.security import consume_browser_auth_rate_limit
from core.tenancy import (
    ACTIVE_BUSINESS_SESSION_KEY,
    active_memberships_for,
    attach_active_membership,
    ensure_legacy_memberships_for_business,
    membership_for_active_business,
    resolve_web_membership,
    users_for_business,
)
from core.timezones import business_day_bounds
from followups.models import FollowUpTask
from leads.cache import invalidate_business_lead_cache
from leads.models import Activity, Lead, Product
from leads.services import record_lead_capture, record_needs_time, transition_lead

from .forms import (
    ActivityForm,
    BusinessForm,
    CurrentPasswordChangeForm,
    EmailVerificationCodeForm,
    EmailVerificationResendForm,
    LeadDetailsForm,
    LeadFollowUpForm,
    LeadQuickAddForm,
    LeadStageForm,
    ProfileForm,
    ProductForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ProtectedAuthenticationForm,
    SignupForm,
    TaskCompletionForm,
    TaskRescheduleForm,
    TeamUserForm,
)


PASSWORD_RESET_EMAIL_SESSION_KEY = 'password_reset_email'


def safe_post_redirect(request, fallback):
    """Keep small action forms on an internal page, never a posted external URL."""
    candidate = request.POST.get('next', '')
    if candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return fallback


class BrowserAuthRateLimitMixin:
    """Apply cache-backed IP limits to public server-rendered auth forms."""

    browser_throttle_scope = ''

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'POST' and self.browser_throttle_scope:
            result = consume_browser_auth_rate_limit(request, self.browser_throttle_scope)
            if not result.allowed:
                messages.error(
                    request,
                    f'Too many attempts. Please try again in {result.retry_after} seconds.',
                )
                return redirect(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)


class LandingView(TemplateView):
    template_name = 'web/landing.html'


class SignupView(BrowserAuthRateLimitMixin, FormView):
    template_name = 'web/signup.html'
    form_class = SignupForm
    browser_throttle_scope = 'web_signup'

    def form_valid(self, form):
        try:
            with transaction.atomic():
                registration = form.save()
                send_email_verification(registration)
        except EmailVerificationDeliveryError:
            form.add_error(None, 'We could not send the verification code. Please try again in a moment.')
            return self.form_invalid(form)

        self.request.session['pending_email_verification_registration_id'] = str(registration.pk)
        return redirect('web:email-verification-sent')


class EmailVerificationSentView(TemplateView):
    template_name = 'web/email_verification_sent.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        registration_id = self.request.session.get('pending_email_verification_registration_id')
        registration = PendingRegistration.objects.filter(pk=registration_id).only('email').first()
        context['pending_email'] = registration.email if registration else ''
        context['verification_timeout_hours'] = max(1, settings.EMAIL_VERIFICATION_TIMEOUT // 3600)
        context['verification_form'] = EmailVerificationCodeForm(initial={'email': context['pending_email']})
        context['resend_form'] = EmailVerificationResendForm(initial={'email': context['pending_email']})
        return context


class EmailVerificationResendView(BrowserAuthRateLimitMixin, View):
    browser_throttle_scope = 'web_email_resend'

    def post(self, request):
        form = EmailVerificationResendForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Enter a valid email address to request a new verification code.')
            return redirect('web:email-verification-sent')

        registration = PendingRegistration.objects.filter(
            email__iexact=form.cleaned_data['email'],
        ).first()
        if not registration:
            messages.success(request, 'If a pending registration uses that email, a verification code is on its way.')
            return redirect('web:email-verification-sent')

        request.session['pending_email_verification_registration_id'] = str(registration.pk)
        try:
            send_email_verification(registration)
        except EmailVerificationCooldownError:
            messages.success(request, 'If a pending registration uses that email, a verification code is on its way.')
        except EmailVerificationDeliveryError:
            messages.error(request, 'We could not resend the verification code. Please try again in a moment.')
        else:
            messages.success(request, 'A fresh six-digit verification code has been sent.')
        return redirect('web:email-verification-sent')


class EmailVerificationView(BrowserAuthRateLimitMixin, View):
    browser_throttle_scope = 'web_email_verify'

    def get(self, request):
        return redirect('web:email-verification-sent')

    def post(self, request):
        form = EmailVerificationCodeForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Enter the email address and six-digit verification code.')
            return redirect('web:email-verification-sent')

        registration = PendingRegistration.objects.filter(email__iexact=form.cleaned_data['email']).first()
        if not registration:
            messages.error(request, 'That email address or verification code is not valid.')
            return redirect('web:email-verification-sent')

        try:
            user = activate_pending_registration(registration, form.cleaned_data['code'])
        except EmailVerificationError:
            messages.error(request, 'That email address or verification code is not valid, has expired, or has already been used.')
            return redirect('web:email-verification-sent')

        request.session.pop('pending_email_verification_registration_id', None)
        login(request, user)
        messages.success(request, 'Email verified. Your workspace is ready.')
        return redirect('web:onboarding')


class PasswordResetRequestView(BrowserAuthRateLimitMixin, FormView):
    """Start a password reset without revealing whether an account exists."""

    template_name = 'web/password_reset_request.html'
    form_class = PasswordResetRequestForm
    browser_throttle_scope = 'web_password_reset_request'

    def get_initial(self):
        initial = super().get_initial()
        if self.request.user.is_authenticated and self.request.user.email:
            initial['email'] = self.request.user.email
        return initial

    def form_valid(self, form):
        email = form.cleaned_data['email']
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            try:
                send_password_reset_code(user)
            except (PasswordResetCooldownError, PasswordResetDeliveryError, PasswordResetError):
                # Keep the browser response the same for known and unknown
                # addresses so this form cannot be used to enumerate users.
                pass
        self.request.session[PASSWORD_RESET_EMAIL_SESSION_KEY] = email
        messages.success(self.request, 'If an active account uses that email, a six-digit reset code is on its way.')
        return redirect('web:password-reset-confirm')


class PasswordResetConfirmView(BrowserAuthRateLimitMixin, FormView):
    """Accept a reset code and a new password, then invalidate that code."""

    template_name = 'web/password_reset_confirm.html'
    form_class = PasswordResetConfirmForm
    browser_throttle_scope = 'web_password_reset_confirm'

    def get_initial(self):
        initial = super().get_initial()
        pending_email = self.request.session.get(PASSWORD_RESET_EMAIL_SESSION_KEY)
        if pending_email:
            initial['email'] = pending_email
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['pending_email'] = self.request.session.get(PASSWORD_RESET_EMAIL_SESSION_KEY, '')
        context['password_reset_timeout_minutes'] = max(1, settings.PASSWORD_RESET_TIMEOUT // 60)
        return context

    def form_valid(self, form):
        # Resolve the lazy user before changing the password. Otherwise the
        # authentication middleware compares the old session hash with the
        # newly saved password and turns this request into AnonymousUser.
        signed_in_user_id = self.request.user.pk if self.request.user.is_authenticated else None
        try:
            user = reset_password(
                form.cleaned_data['email'],
                form.cleaned_data['code'],
                form.cleaned_data['new_password'],
            )
        except PasswordResetError:
            form.add_error(None, 'That email address or reset code is not valid, has expired, or has already been used.')
            return self.form_invalid(form)

        self.request.session.pop(PASSWORD_RESET_EMAIL_SESSION_KEY, None)
        if signed_in_user_id == user.pk:
            update_session_auth_hash(self.request, user)
            messages.success(self.request, 'Your password has been reset.')
            return redirect('web:security-settings')
        messages.success(self.request, 'Your password has been reset. You can now sign in.')
        return redirect('web:login')


class ProtectedLoginView(BrowserAuthRateLimitMixin, auth_views.LoginView):
    template_name = 'web/login.html'
    authentication_form = ProtectedAuthenticationForm
    browser_throttle_scope = 'web_login'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['turnstile_site_key'] = settings.TURNSTILE_SITE_KEY if captcha_enabled() else ''
        return context


class TenantWebMixin(LoginRequiredMixin):
    """Shared tenant and role scoping for the server-rendered application."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        membership = resolve_web_membership(request)
        attach_active_membership(request, membership)
        if membership is None:
            return render(request, 'web/no_business.html', status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_active_membership(self):
        membership = getattr(self.request, 'active_membership', None)
        if membership is None:
            membership = resolve_web_membership(self.request)
            attach_active_membership(self.request, membership)
        return membership

    def get_business(self):
        return self.get_active_membership().business

    def get_role(self):
        return self.get_active_membership().role

    def is_manager(self):
        return self.get_role() in (User.Role.OWNER, User.Role.MANAGER)

    def visible_leads(self):
        queryset = Lead.objects.for_business(self.get_business()).select_related('assigned_user', 'product')
        if self.get_role() == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        return queryset

    def visible_tasks(self):
        queryset = FollowUpTask.objects.for_business(self.get_business()).select_related('lead', 'assigned_user')
        if self.get_role() == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        return queryset

    def get_visible_lead(self, pk):
        return get_object_or_404(self.visible_leads(), pk=pk)

    def get_visible_task(self, pk):
        return get_object_or_404(self.visible_tasks(), pk=pk)

    def common_context(self):
        membership = self.get_active_membership()
        return {
            'business': self.get_business(),
            'active_membership': membership,
            'workspace_memberships': active_memberships_for(self.request.user).order_by('business__name', 'id'),
            'is_manager': self.is_manager(),
            'is_owner': self.get_role() == User.Role.OWNER,
            'is_salesperson': self.get_role() == User.Role.SALESPERSON,
        }


class OwnerRequiredMixin(TenantWebMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        membership = resolve_web_membership(request)
        attach_active_membership(request, membership)
        if membership is None:
            return super().dispatch(request, *args, **kwargs)
        if membership.role != User.Role.OWNER:
            return HttpResponseForbidden('Only a business owner can manage this area.')
        return super().dispatch(request, *args, **kwargs)


class ManagerRequiredMixin(TenantWebMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        membership = resolve_web_membership(request)
        attach_active_membership(request, membership)
        if membership is None:
            return super().dispatch(request, *args, **kwargs)
        if membership.role not in (User.Role.OWNER, User.Role.MANAGER):
            return HttpResponseForbidden('This area is for owners and managers.')
        return super().dispatch(request, *args, **kwargs)


class WorkspaceSwitchView(LoginRequiredMixin, View):
    """Switch the session to one membership the signed-in user actually owns."""

    def post(self, request):
        membership = membership_for_active_business(request.user, request.POST.get('business_id'))
        if membership is None:
            return HttpResponseForbidden('You do not have access to that workspace.')
        request.session[ACTIVE_BUSINESS_SESSION_KEY] = str(membership.business_id)
        attach_active_membership(request, membership)
        messages.success(request, f'You are now working in {membership.business.name}.')
        # Always start at the new workspace overview.  It prevents a URL from
        # the old tenant being carried into a business where it is irrelevant.
        return redirect('web:dashboard')


class BusinessCreateView(OwnerRequiredMixin, FormView):
    """Let an owner create and immediately enter another business workspace."""

    template_name = 'web/business_create.html'
    form_class = BusinessForm

    def form_valid(self, form):
        with transaction.atomic():
            business = form.save()
            Membership.objects.create(
                user=self.request.user,
                business=business,
                role=User.Role.OWNER,
                is_active=True,
            )
            # The legacy User.business field intentionally remains unchanged.
            # Session context is the authoritative active workspace.
            self.request.session[ACTIVE_BUSINESS_SESSION_KEY] = str(business.id)
        messages.success(self.request, f'{business.name} is ready for your team.')
        return redirect('web:dashboard')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        return context


class OnboardingView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/onboarding.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        business = self.get_business()
        context.update(self.common_context())
        context.update({
            'lead_count': Lead.objects.for_business(business).count(),
            'team_count': Membership.objects.filter(
                business=business,
                is_active=True,
                user__is_active=True,
            ).count(),
            'product_count': Product.objects.for_business(business).filter(is_active=True).count(),
        })
        return context


class DashboardView(TenantWebMixin, TemplateView):
    template_name = 'web/dashboard.html'
    TEAM_ATTENTION_LIMIT = 5
    LEAD_TREND_DAYS = 14
    LEAD_TREND_CHART_WIDTH = 560
    LEAD_TREND_CHART_LEFT = 32
    LEAD_TREND_CHART_RIGHT = 14
    LEAD_TREND_CHART_TOP = 18
    LEAD_TREND_CHART_BOTTOM = 136

    @staticmethod
    def greeting_for_hour(hour):
        if hour < 12:
            return 'Good morning'
        if hour < 17:
            return 'Good afternoon'
        return 'Good evening'

    def dispatch(self, request, *args, **kwargs):
        # A salesperson's command centre is their personal pipeline. Sending
        # them there keeps the default route focused rather than a cut-down
        # version of the manager dashboard.
        membership = resolve_web_membership(request) if request.user.is_authenticated else None
        if membership is not None:
            attach_active_membership(request, membership)
        if membership is not None and membership.role == User.Role.SALESPERSON:
            return redirect('web:lead-list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        business = self.get_business()
        now = timezone.now()
        business_timezone = ZoneInfo(business.timezone)
        dashboard_now = timezone.localtime(now, timezone=business_timezone)
        today = dashboard_now.date()
        today_start, tomorrow_start = business_day_bounds(business.timezone, now=now)
        dashboard_greeting = self.greeting_for_hour(dashboard_now.hour)
        leads = self.visible_leads()
        active_leads = leads.exclude(stage__in=(Lead.Stage.WON, Lead.Stage.LOST))
        tasks = self.visible_tasks()
        open_tasks = tasks.filter(status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE))
        overdue_tasks = open_tasks.filter(Q(status=FollowUpTask.Status.OVERDUE) | Q(due_at__lt=now))
        stale_cutoff = now - timedelta(days=10)
        quote_cutoff = now - timedelta(days=2)
        stalled_leads = active_leads.filter(last_activity_at__lt=stale_cutoff).order_by('last_activity_at')
        quiet_quotes = active_leads.filter(
            stage=Lead.Stage.QUOTATION_SENT,
            last_activity_at__lt=quote_cutoff,
        ).order_by('last_activity_at')

        stage_totals = {
            row['stage']: row['total']
            for row in leads.values('stage').annotate(total=Count('id'))
        }
        active_count = sum(
            stage_totals.get(value, 0)
            for value, _label in Lead.Stage.choices
            if value not in (Lead.Stage.WON, Lead.Stage.LOST)
        )
        stage_rows = [
            {
                'value': value,
                'label': label,
                'total': stage_totals.get(value, 0),
                'width': round(stage_totals.get(value, 0) * 100 / active_count) if active_count else 0,
            }
            for value, label in Lead.Stage.choices
            if value not in (Lead.Stage.WON, Lead.Stage.LOST)
        ]

        # Keep the dashboard charts compact and directly actionable: the
        # complete stage mix shows where opportunities sit, while source
        # conversion shows which channels produce customers rather than just
        # inquiries. Both are built from the same tenant-scoped queryset used
        # everywhere else on this page.
        short_stage_labels = {
            Lead.Stage.NEW_INQUIRY: 'New',
            Lead.Stage.CONTACTED: 'Contacted',
            Lead.Stage.SITE_VISIT: 'Visit',
            Lead.Stage.QUOTATION_SENT: 'Quote',
            Lead.Stage.NEGOTIATION: 'Negotiating',
            Lead.Stage.WON: 'Won',
            Lead.Stage.LOST: 'Lost',
        }
        largest_stage_total = max(stage_totals.values(), default=0)
        analytics_stage_rows = []
        for value, label in Lead.Stage.choices:
            total = stage_totals.get(value, 0)
            analytics_stage_rows.append({
                'value': value,
                'label': label,
                'short_label': short_stage_labels[value],
                'total': total,
                'height': max(round(total * 100 / largest_stage_total), 10) if total else 0,
            })

        source_labels = dict(Lead.Source.choices)
        analytics_source_rows = list(
            leads.values('source').annotate(
                total=Count('id'),
                won=Count('id', filter=Q(stage=Lead.Stage.WON)),
            ).order_by('-total', 'source')[:5],
        )
        for row in analytics_source_rows:
            row['label'] = source_labels.get(row['source'], row['source'])
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
            row['width'] = row['conversion']

        # The dashboard needs a short, useful sense of momentum without
        # leaking another workspace's data. Build the series from the same
        # visible, tenant-scoped lead queryset as the other dashboard cards.
        lead_trend_start_date = today - timedelta(days=self.LEAD_TREND_DAYS - 1)
        lead_trend_start = timezone.make_aware(
            datetime.combine(lead_trend_start_date, time.min),
            timezone=business_timezone,
        )
        lead_trend_totals = {
            row['day']: row['total']
            for row in leads.filter(created_at__gte=lead_trend_start).annotate(
                day=TruncDate('created_at', tzinfo=business_timezone),
            ).values('day').annotate(total=Count('id'))
        }
        lead_trend_counts = [
            lead_trend_totals.get(lead_trend_start_date + timedelta(days=offset), 0)
            for offset in range(self.LEAD_TREND_DAYS)
        ]
        lead_trend_scale = max(max(lead_trend_counts, default=0), 4)
        chart_plot_width = self.LEAD_TREND_CHART_WIDTH - self.LEAD_TREND_CHART_LEFT - self.LEAD_TREND_CHART_RIGHT
        chart_plot_height = self.LEAD_TREND_CHART_BOTTOM - self.LEAD_TREND_CHART_TOP
        lead_trend_days = []
        for offset, total in enumerate(lead_trend_counts):
            day = lead_trend_start_date + timedelta(days=offset)
            x = self.LEAD_TREND_CHART_LEFT + (chart_plot_width * offset / (self.LEAD_TREND_DAYS - 1))
            y = self.LEAD_TREND_CHART_BOTTOM - (chart_plot_height * total / lead_trend_scale)
            lead_trend_days.append({
                'count': total,
                'label': day.strftime('%b %d'),
                'short_label': day.strftime('%b %d').replace(' 0', ' '),
                'x': f'{x:.1f}',
                'y': f'{y:.1f}',
                'show_label': offset in (0, 4, 9, self.LEAD_TREND_DAYS - 1),
            })
        lead_trend_points = ' '.join(f"{day['x']},{day['y']}" for day in lead_trend_days)
        lead_trend_area_points = (
            f"{lead_trend_days[0]['x']},{self.LEAD_TREND_CHART_BOTTOM} "
            f"{lead_trend_points} "
            f"{lead_trend_days[-1]['x']},{self.LEAD_TREND_CHART_BOTTOM}"
        )
        lead_trend_grid_lines = [
            {'y': self.LEAD_TREND_CHART_TOP, 'label': lead_trend_scale},
            {
                'y': (self.LEAD_TREND_CHART_TOP + self.LEAD_TREND_CHART_BOTTOM) / 2,
                'label': lead_trend_scale // 2,
            },
            {'y': self.LEAD_TREND_CHART_BOTTOM, 'label': 0},
        ]
        lead_trend_total = sum(lead_trend_counts)
        lead_trend_peak = max(lead_trend_days, key=lambda day: day['count'])

        closed_count = stage_totals.get(Lead.Stage.WON, 0) + stage_totals.get(Lead.Stage.LOST, 0)
        win_rate = round(stage_totals.get(Lead.Stage.WON, 0) * 100 / closed_count) if closed_count else 0

        team_attention = []
        for teammate in users_for_business(business).order_by('first_name', 'username'):
            member_leads = active_leads.filter(assigned_user=teammate)
            member_stalled = member_leads.filter(last_activity_at__lt=stale_cutoff).count()
            member_due = open_tasks.filter(
                assigned_user=teammate,
                due_at__gte=today_start,
                due_at__lt=tomorrow_start,
            ).count()
            team_attention.append({
                'member': teammate,
                'active_count': member_leads.count(),
                'stalled_count': member_stalled,
                'due_count': member_due,
                'needs_attention': bool(member_stalled or member_due),
            })

        # Priority order: most stalled first, then most due, then everyone
        # else alphabetically (already the default order coming in).
        team_attention.sort(key=lambda row: (-row['stalled_count'], -row['due_count']))
        team_attention_total = len(team_attention)
        team_attention_overflow = max(team_attention_total - self.TEAM_ATTENTION_LIMIT, 0)
        team_attention = team_attention[:self.TEAM_ATTENTION_LIMIT]

        needs_action_count = active_leads.exclude(
            follow_up_tasks__status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
        ).distinct().count()
        context.update(self.common_context())
        context.update({
            'today': today,
            'dashboard_now': dashboard_now,
            'dashboard_greeting': dashboard_greeting,
            'due_today_count': open_tasks.filter(
                due_at__gte=today_start,
                due_at__lt=tomorrow_start,
            ).count(),
            'overdue_count': overdue_tasks.count(),
            'active_leads_count': active_count,
            'pipeline_value': active_leads.aggregate(total=Sum('quoted_price'))['total'] or 0,
            'needs_action_count': needs_action_count,
            'attention_count': overdue_tasks.count() + stalled_leads.count() + quiet_quotes.count(),
            'tasks': open_tasks.order_by('due_at')[:6],
            'overdue_tasks': overdue_tasks.order_by('due_at')[:4],
            'stalled_leads': stalled_leads[:4],
            'quiet_quotes': quiet_quotes[:4],
            'recent_leads': leads.order_by('-updated_at')[:6],
            'stage_rows': stage_rows,
            'analytics_stage_rows': analytics_stage_rows,
            'analytics_source_rows': analytics_source_rows,
            'analytics_closed_count': closed_count,
            'analytics_win_rate': win_rate,
            'lead_trend_days': lead_trend_days,
            'lead_trend_points': lead_trend_points,
            'lead_trend_area_points': lead_trend_area_points,
            'lead_trend_grid_lines': lead_trend_grid_lines,
            'lead_trend_total': lead_trend_total,
            'lead_trend_peak': lead_trend_peak,
            'team_attention': team_attention,
            'team_attention_overflow': team_attention_overflow,
        })
        return context

class LeadListView(TenantWebMixin, TemplateView):
    template_name = 'web/lead_list.html'
    BOARD_PAGE_SIZE = 10

    def visible_board_leads(self):
        # The board needs only card fields. Keeping this separate from
        # visible_leads() avoids loading assignees and contact data for every
        # record before the seven columns are rendered.
        queryset = Lead.objects.for_business(self.get_business())
        if self.get_role() == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        return queryset

    def get_board_queryset(self):
        queryset = self.visible_board_leads().select_related('product')

        stage = self.request.GET.get('stage', '')
        search = self.request.GET.get('q', '').strip()
        assigned_user = self.request.GET.get('assigned_user', '')
        if stage in Lead.Stage.values:
            queryset = queryset.filter(stage=stage)
        if assigned_user:
            try:
                queryset = queryset.filter(assigned_user_id=UUID(assigned_user))
            except (TypeError, ValueError, AttributeError):
                return queryset.none()
        if search:
            queryset = queryset.filter(
                Q(customer_name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search),
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = self.get_board_queryset()
        totals = {
            row['stage']: row['total']
            for row in queryset.values('stage').annotate(total=Count('id'))
        }
        card_fields = (
            'id', 'customer_name', 'stage', 'quoted_price', 'last_activity_at', 'product_id', 'product__id',
            'product__name',
        )
        pipeline_columns = []
        for value, label in Lead.Stage.choices:
            total = totals.get(value, 0)
            leads = list(
                queryset.filter(stage=value).order_by('-last_activity_at', '-id').only(*card_fields)[:self.BOARD_PAGE_SIZE],
            )
            for lead in leads:
                lead.initials = ''.join(part[0] for part in lead.customer_name.split()[:2]).upper() or 'L'
            pipeline_columns.append({
                'value': value,
                'label': label,
                'leads': leads,
                'shown': len(leads),
                'total': total,
                'has_more': total > len(leads),
            })

        search = self.request.GET.get('q', '').strip()
        selected_stage = self.request.GET.get('stage', '')
        if selected_stage not in Lead.Stage.values:
            selected_stage = ''
        selected_assigned_user = self.request.GET.get('assigned_user', '')
        summary = self.visible_board_leads().aggregate(
            total=Count('id'),
            new=Count('id', filter=Q(stage=Lead.Stage.NEW_INQUIRY)),
            won=Count('id', filter=Q(stage=Lead.Stage.WON)),
            lost=Count('id', filter=Q(stage=Lead.Stage.LOST)),
            value=Sum('quoted_price'),
            created_this_month=Count('id', filter=Q(created_at__gte=timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))),
        )
        closed = summary['won'] + summary['lost']
        board_filter_params = {}
        if search:
            board_filter_params['q'] = search
        if selected_assigned_user:
            board_filter_params['assigned_user'] = selected_assigned_user
        context.update(self.common_context())
        context.update({
            'stages': Lead.Stage.choices,
            'pipeline_columns': pipeline_columns,
            'has_leads': any(totals.values()),
            'selected_stage': selected_stage,
            'selected_assigned_user': selected_assigned_user,
            'search': search,
            'assignees': users_for_business(self.get_business()).order_by(
                'first_name', 'last_name', 'username',
            ) if self.is_manager() else [self.request.user],
            'board_filter_query': urlencode(board_filter_params),
            'lead_summary': {
                'total': summary['total'],
                'new': summary['new'],
                'won': summary['won'],
                'conversion_rate': round(summary['won'] * 100 / closed, 1) if closed else 0,
                'value': summary['value'] or 0,
                'created_this_month': summary['created_this_month'],
            },
        })
        return context


class LeadStageListView(TenantWebMixin, ListView):
    """A focused, server-paginated working list for one pipeline stage."""

    template_name = 'web/lead_stage_list.html'
    context_object_name = 'leads'
    paginate_by = 25
    ORDERING_CHOICES = (
        ('-last_activity_at', 'Latest activity'),
        ('last_activity_at', 'Oldest activity'),
        ('customer_name', 'Customer A–Z'),
        ('-customer_name', 'Customer Z–A'),
        ('-quoted_price', 'Highest quote'),
        ('quoted_price', 'Lowest quote'),
    )

    def get_stage(self):
        stage = self.kwargs['stage']
        if stage not in Lead.Stage.values:
            raise Http404('Unknown lead stage.')
        return stage

    @staticmethod
    def valid_uuid(value):
        try:
            return UUID(value)
        except (TypeError, ValueError, AttributeError):
            return None

    def get_queryset(self):
        queryset = self.visible_leads().filter(stage=self.get_stage())
        search = self.request.GET.get('q', '').strip()
        if search:
            queryset = queryset.filter(
                Q(customer_name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search),
            )

        source = self.request.GET.get('source', '')
        if source in Lead.Source.values:
            queryset = queryset.filter(source=source)

        assigned_user_id = self.valid_uuid(self.request.GET.get('assigned_user'))
        if self.request.GET.get('assigned_user'):
            if not assigned_user_id:
                return queryset.none()
            if self.is_manager() or assigned_user_id == self.request.user.id:
                queryset = queryset.filter(assigned_user_id=assigned_user_id)
            else:
                return queryset.none()

        product_id = self.valid_uuid(self.request.GET.get('product'))
        if self.request.GET.get('product'):
            if not product_id:
                return queryset.none()
            queryset = queryset.filter(product_id=product_id)

        ordering = self.request.GET.get('ordering', '-last_activity_at')
        allowed_ordering = {value for value, _label in self.ORDERING_CHOICES}
        if ordering not in allowed_ordering:
            ordering = '-last_activity_at'
        return queryset.order_by(ordering, '-id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = self.get_stage()
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        context.update(self.common_context())
        context.update({
            'stage': stage,
            'stage_label': Lead.Stage(stage).label,
            'search': self.request.GET.get('q', '').strip(),
            'selected_source': self.request.GET.get('source', ''),
            'selected_assigned_user': self.request.GET.get('assigned_user', ''),
            'selected_product': self.request.GET.get('product', ''),
            'selected_ordering': self.request.GET.get('ordering', '-last_activity_at'),
            'source_choices': Lead.Source.choices,
            'ordering_choices': self.ORDERING_CHOICES,
            'assignees': users_for_business(self.get_business()).order_by(
                'first_name', 'last_name', 'username',
            ) if self.is_manager() else [self.request.user],
            'products': Product.objects.for_business(self.get_business()).filter(is_active=True).order_by('name'),
            'query_string': query_params.urlencode(),
        })
        return context


class LeadCreateView(TenantWebMixin, FormView):
    template_name = 'web/lead_quick_add.html'
    form_class = LeadQuickAddForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({
            'business': self.get_business(),
            'user': self.request.user,
            'role': self.get_role(),
        })
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            lead = form.save(commit=False)
            lead.business = self.get_business()
            lead.assigned_user = form.cleaned_data.get('assigned_user') or self.request.user
            lead.save()
            record_lead_capture(lead=lead, actor=self.request.user)
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))
        messages.success(self.request, f'{lead.customer_name} is now in your pipeline.')
        return redirect('web:lead-detail', pk=lead.pk)


class LeadDetailView(TenantWebMixin, TemplateView):
    template_name = 'web/lead_detail.html'

    def get_lead(self):
        return self.get_visible_lead(self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lead = self.get_lead()
        task_queryset = FollowUpTask.objects.for_business(self.get_business()).filter(
            lead=lead,
            status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
        ).select_related('assigned_user').order_by('due_at')
        context.update(self.common_context())
        context.update({
            'lead': lead,
            'details_form': LeadDetailsForm(
                instance=lead,
                business=self.get_business(),
                user=self.request.user,
                role=self.get_role(),
            ),
            'stage_form': LeadStageForm(initial={'stage': lead.stage}),
            'activity_form': ActivityForm(initial={'kind': Activity.Kind.CALL}),
            'follow_up_form': LeadFollowUpForm(),
            'open_tasks': task_queryset,
            'timeline': Activity.objects.for_business(self.get_business()).filter(lead=lead).select_related('created_by'),
            'can_change_assignee': self.is_manager(),
        })
        return context


class LeadUpdateView(TenantWebMixin, View):
    def post(self, request, pk):
        lead = self.get_visible_lead(pk)
        form = LeadDetailsForm(
            request.POST,
            instance=lead,
            business=self.get_business(),
            user=request.user,
            role=self.get_role(),
        )
        if form.is_valid():
            updated_lead = form.save(commit=False)
            if self.get_role() == User.Role.SALESPERSON:
                updated_lead.assigned_user = lead.assigned_user
            updated_lead.save()
            transaction.on_commit(lambda: invalidate_business_lead_cache(updated_lead.business_id))
            messages.success(request, 'Lead details updated.')
        else:
            messages.error(request, 'Check the lead details and try again.')
        return redirect('web:lead-detail', pk=lead.pk)


class LeadStageUpdateView(TenantWebMixin, View):
    def post(self, request, pk):
        lead = self.get_visible_lead(pk)
        form = LeadStageForm(request.POST)
        wants_json = 'application/json' in request.headers.get('Accept', '')
        if not form.is_valid():
            if wants_json:
                return JsonResponse({'errors': form.errors}, status=400)
            messages.error(request, 'A lost lead needs a reason — add one below.')
            return redirect('web:lead-detail', pk=lead.pk)

        next_stage = form.cleaned_data['stage']
        note = form.cleaned_data.get('note', '').strip()
        with transaction.atomic():
            locked_lead = Lead.objects.for_business(self.get_business()).select_for_update().get(pk=lead.pk)
            if self.get_role() == User.Role.SALESPERSON and locked_lead.assigned_user_id != request.user.id:
                return HttpResponseForbidden('You can only update your own leads.')

            transition_lead(
                lead=locked_lead,
                stage=next_stage,
                lost_reason=form.cleaned_data.get('lost_reason', ''),
                note=note,
                actor=request.user,
            )
            transaction.on_commit(lambda: invalidate_business_lead_cache(locked_lead.business_id))

        if wants_json:
            return JsonResponse({
                'stage': next_stage,
                'stage_label': Lead.Stage(next_stage).label,
                'detail_url': request.build_absolute_uri(redirect('web:lead-detail', pk=lead.pk).url),
            })
        messages.success(request, f'Lead moved to {Lead.Stage(next_stage).label}.')
        return redirect('web:lead-detail', pk=lead.pk)


class LeadNeedsTimeView(TenantWebMixin, View):
    def post(self, request, pk):
        lead = self.get_visible_lead(pk)
        with transaction.atomic():
            lead = Lead.objects.for_business(self.get_business()).select_for_update().get(pk=lead.pk)
            if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
                messages.error(request, 'Closed leads cannot receive another follow-up reminder.')
                return redirect('web:lead-detail', pk=lead.pk)
            record_needs_time(lead=lead, actor=request.user)
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))
        messages.success(request, 'Follow-up set for seven days from now.')
        return redirect('web:lead-detail', pk=lead.pk)


class LeadActivityCreateView(TenantWebMixin, View):
    def post(self, request, pk):
        lead = self.get_visible_lead(pk)
        form = ActivityForm(request.POST)
        if form.is_valid():
            activity = form.save(commit=False)
            activity.business = lead.business
            activity.lead = lead
            activity.created_by = request.user
            activity.save()
            lead.last_activity_at = timezone.now()
            lead.save(update_fields=('last_activity_at', 'updated_at'))
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))
            messages.success(request, 'Activity added to the timeline.')
        else:
            messages.error(request, 'Add a short note about what happened before saving.')
        return redirect('web:lead-detail', pk=lead.pk)


class LeadFollowUpCreateView(TenantWebMixin, View):
    def post(self, request, pk):
        lead = self.get_visible_lead(pk)
        form = LeadFollowUpForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.business = lead.business
            task.lead = lead
            task.assigned_user = lead.assigned_user
            task.save()
            messages.success(request, 'Next action scheduled.')
        else:
            messages.error(request, 'Choose a future time and describe the next action.')
        return redirect('web:lead-detail', pk=lead.pk)


class TaskListView(TenantWebMixin, ListView):
    template_name = 'web/task_list.html'
    context_object_name = 'tasks'
    paginate_by = 10

    def get_queryset(self):
        queryset = self.visible_tasks().filter(status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE))
        status_filter = self.request.GET.get('status')
        if status_filter in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by('due_at', 'id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        task_counts = self.visible_tasks().filter(
            status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
        ).aggregate(
            total=Count('id'),
            pending=Count('id', filter=Q(status=FollowUpTask.Status.PENDING)),
            overdue=Count('id', filter=Q(status=FollowUpTask.Status.OVERDUE)),
        )
        context.update(self.common_context())
        context.update({
            'selected_status': self.request.GET.get('status', ''),
            'today': timezone.localdate(timezone.now(), timezone=ZoneInfo(self.get_business().timezone)),
            'query_string': query_params.urlencode(),
            'task_counts': task_counts,
        })
        return context


class TaskCompleteView(TenantWebMixin, View):
    def post(self, request, pk):
        task = self.get_visible_task(pk)
        form = TaskCompletionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Choose a future time and describe the next action before marking this done.')
            return redirect(safe_post_redirect(request, 'web:task-list'))
        with transaction.atomic():
            task = FollowUpTask.objects.for_business(self.get_business()).select_for_update().get(pk=task.pk)
            if self.get_role() == User.Role.SALESPERSON and task.assigned_user_id != request.user.id:
                return HttpResponseForbidden('You can only complete your own tasks.')
            if task.status not in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
                messages.error(request, 'That follow-up is already closed.')
                return redirect(safe_post_redirect(request, 'web:task-list'))
            task.mark_done()
            task.save(update_fields=('status', 'completed_at'))
            FollowUpTask.objects.create(
                business=task.business,
                lead=task.lead,
                assigned_user=task.assigned_user,
                due_at=form.cleaned_data['next_due_at'],
                description=form.cleaned_data['next_description'],
            )
        messages.success(request, 'Marked done and the next action is scheduled.')
        return redirect(safe_post_redirect(request, 'web:task-list'))


class TaskRescheduleView(TenantWebMixin, View):
    def post(self, request, pk):
        task = self.get_visible_task(pk)
        form = TaskRescheduleForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Choose a future time to reschedule this follow-up.')
            return redirect(safe_post_redirect(request, 'web:task-list'))
        if task.status not in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            messages.error(request, 'That follow-up is already closed.')
            return redirect(safe_post_redirect(request, 'web:task-list'))
        task.due_at = form.cleaned_data['due_at']
        task.status = FollowUpTask.Status.PENDING
        task.save(update_fields=('due_at', 'status'))
        messages.success(request, 'Follow-up rescheduled.')
        return redirect(safe_post_redirect(request, 'web:task-list'))


class TeamListView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/team_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        ensure_legacy_memberships_for_business(self.get_business())
        context['team'] = Membership.objects.filter(
            business=self.get_business(),
        ).select_related('user').order_by(
            '-is_active', 'role', 'user__first_name', 'user__username',
        )
        context['form'] = TeamUserForm(business=self.get_business())
        return context

    def post(self, request, *args, **kwargs):
        form = TeamUserForm(request.POST, business=self.get_business())
        if form.is_valid():
            user = form.save()
            messages.success(request, f'{user.get_full_name() or user.username} was added to your team.')
            return redirect('web:team-list')
        context = self.get_context_data()
        context['form'] = form
        return self.render_to_response(context)


class ProductListView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/product_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['products'] = Product.objects.for_business(self.get_business()).order_by('-is_active', 'name')
        context['form'] = ProductForm(business=self.get_business())
        return context

    def post(self, request, *args, **kwargs):
        form = ProductForm(request.POST, business=self.get_business())
        if form.is_valid():
            product = form.save(commit=False)
            product.business = self.get_business()
            product.save()
            transaction.on_commit(lambda: invalidate_business_lead_cache(product.business_id))
            messages.success(request, f'{product.name} was added.')
            return redirect('web:product-list')
        context = self.get_context_data()
        context['form'] = form
        return self.render_to_response(context)


class TeamEditView(OwnerRequiredMixin, UpdateView):
    model = User
    form_class = TeamUserForm
    template_name = 'web/edit_form.html'

    def get_queryset(self):
        return users_for_business(self.get_business(), active_only=False)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['business'] = self.get_business()
        return kwargs

    def form_valid(self, form):
        member = form.save()
        transaction.on_commit(lambda: invalidate_business_lead_cache(self.get_business().id))
        messages.success(self.request, 'Team member updated.')
        return redirect('web:team-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['heading'] = 'Edit team member'
        context['cancel_url'] = 'web:team-list'
        return context


class TeamDeleteView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        membership = get_object_or_404(
            Membership.objects.select_related('user'),
            user_id=pk,
            business=self.get_business(),
        )
        if membership.user == request.user:
            messages.error(request, 'You cannot deactivate your own account.')
        elif membership.role == User.Role.OWNER and membership.is_active and Membership.objects.filter(
            business=self.get_business(),
            role=User.Role.OWNER,
            is_active=True,
            user__is_active=True,
        ).count() == 1:
            messages.error(request, 'A business must keep at least one active owner.')
        else:
            membership.is_active = False
            membership.save(update_fields=('is_active',))
            messages.success(request, 'Team member deactivated. Their lead history is still intact.')
        return redirect('web:team-list')


class ProductEditView(OwnerRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'web/edit_form.html'

    def get_queryset(self):
        return Product.objects.for_business(self.get_business())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['business'] = self.get_business()
        return kwargs

    def form_valid(self, form):
        product = form.save()
        transaction.on_commit(lambda: invalidate_business_lead_cache(product.business_id))
        messages.success(self.request, 'Product updated.')
        return redirect('web:product-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['heading'] = 'Edit product'
        context['cancel_url'] = 'web:product-list'
        return context


class ProductDeleteView(OwnerRequiredMixin, View):
    def post(self, request, pk):
        product = get_object_or_404(Product.objects.for_business(self.get_business()), pk=pk)
        product.is_active = False
        product.save(update_fields=('is_active',))
        transaction.on_commit(lambda: invalidate_business_lead_cache(product.business_id))
        messages.success(request, 'Product deactivated. Existing lead history is preserved.')
        return redirect('web:product-list')


class ProfileView(TenantWebMixin, UpdateView):
    """Let an authenticated user update only their own account details."""

    model = User
    form_class = ProfileForm
    template_name = 'web/profile.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Your profile was updated.')
        return redirect('web:account-settings-profile')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        return context


class SecuritySettingsView(TenantWebMixin, TemplateView):
    """Account security settings that are available today."""

    template_name = 'web/security_settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['password_form'] = CurrentPasswordChangeForm(user=self.request.user)
        context.update(self.common_context())
        return context


class ProfilePasswordChangeView(TenantWebMixin, View):
    """Change the signed-in user's password after checking their current one."""

    def post(self, request):
        password_form = CurrentPasswordChangeForm(request.POST, user=request.user)
        if not password_form.is_valid():
            context = {'password_form': password_form}
            context.update(self.common_context())
            return render(request, 'web/security_settings.html', context)

        request.user.set_password(password_form.cleaned_data['new_password'])
        request.user.save(update_fields=('password',))
        revoke_refresh_tokens_for_user(request.user)
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Your password was updated.')
        return redirect('web:security-settings')


class BusinessSettingsView(OwnerRequiredMixin, FormView):
    template_name = 'web/business_settings.html'
    form_class = BusinessForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.get_business()
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Business settings were saved.')
        return redirect('web:business-settings')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        return context


class ReportsView(ManagerRequiredMixin, TemplateView):
    template_name = 'web/reports.html'
    SALESPERSON_PAGE_SIZE = 10

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        leads = self.visible_leads()
        source_labels = dict(Lead.Source.choices)
        source_rows = list(
            leads.values('source').annotate(
                total=Count('id'),
                won=Count('id', filter=Q(stage=Lead.Stage.WON)),
            ).order_by('-total'),
        )
        for row in source_rows:
            row['label'] = source_labels.get(row['source'], row['source'])
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
            row['width'] = row['conversion']

        salesperson_rows = list(
            leads.exclude(assigned_user__isnull=True).values(
                'assigned_user__username', 'assigned_user__first_name', 'assigned_user__last_name',
            ).annotate(
                total=Count('id'),
                won=Count('id', filter=Q(stage=Lead.Stage.WON)),
            ),
        )
        for row in salesperson_rows:
            row['label'] = ' '.join(
                value for value in (row['assigned_user__first_name'], row['assigned_user__last_name']) if value
            ) or row['assigned_user__username']
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
            row['width'] = row['conversion']
            row['conversion_ratio'] = row['won'] / row['total'] if row['total'] else 0

        # Rank by actual conversion percentage rather than raw lead volume.
        # This keeps the most effective salespeople at the top, even when the
        # team is large, and gives the card a clear, manageable default size.
        salesperson_rows.sort(key=lambda row: (
            -row['conversion_ratio'], -row['won'], -row['total'], row['label'].casefold(),
        ))
        salesperson_total_count = len(salesperson_rows)
        try:
            requested_salesperson_limit = int(
                self.request.GET.get('salespeople_limit', self.SALESPERSON_PAGE_SIZE),
            )
        except (TypeError, ValueError):
            requested_salesperson_limit = self.SALESPERSON_PAGE_SIZE
        salesperson_visible_count = min(
            max(self.SALESPERSON_PAGE_SIZE, requested_salesperson_limit),
            salesperson_total_count,
        )
        salesperson_more_count = min(
            self.SALESPERSON_PAGE_SIZE,
            max(salesperson_total_count - salesperson_visible_count, 0),
        )
        salesperson_next_limit = salesperson_visible_count + salesperson_more_count
        salesperson_has_more = bool(salesperson_more_count)
        salesperson_can_collapse = salesperson_visible_count > self.SALESPERSON_PAGE_SIZE
        salesperson_rows = salesperson_rows[:salesperson_visible_count]

        product_rows = list(
            leads.filter(stage=Lead.Stage.WON, closed_at__isnull=False).values('product__name').annotate(
                total=Count('id'),
                average=Avg(
                    ExpressionWrapper(F('closed_at') - F('created_at'), output_field=DurationField()),
                ),
            ).order_by('average'),
        )
        max_product_days = max(
            (row['average'].total_seconds() / 86400 for row in product_rows if row['average']), default=0,
        )
        for row in product_rows:
            days = round(row['average'].total_seconds() / 86400, 1) if row['average'] else 0
            row['label'] = row['product__name'] or 'No product selected'
            row['days'] = days
            row['width'] = round(days * 100 / max_product_days) if max_product_days else 0

        lost_reasons = list(
            leads.filter(stage=Lead.Stage.LOST).exclude(lost_reason='').values('lost_reason').annotate(
                total=Count('id'),
            ).order_by('-total')[:6],
        )
        max_lost = max((row['total'] for row in lost_reasons), default=0)
        for row in lost_reasons:
            row['width'] = round(row['total'] * 100 / max_lost) if max_lost else 0

        closed_duration = leads.filter(stage=Lead.Stage.WON, closed_at__isnull=False).aggregate(
            average=Avg(ExpressionWrapper(F('closed_at') - F('created_at'), output_field=DurationField())),
        )['average']
        context.update(self.common_context())
        context.update({
            'source_rows': source_rows,
            'salesperson_rows': salesperson_rows,
            'salesperson_total_count': salesperson_total_count,
            'salesperson_visible_count': salesperson_visible_count,
            'salesperson_more_count': salesperson_more_count,
            'salesperson_next_limit': salesperson_next_limit,
            'salesperson_has_more': salesperson_has_more,
            'salesperson_can_collapse': salesperson_can_collapse,
            'product_rows': product_rows,
            'lost_reasons': lost_reasons,
            'average_days': round(closed_duration.total_seconds() / 86400, 1) if closed_duration else None,
            'won_count': leads.filter(stage=Lead.Stage.WON).count(),
            'total_count': leads.count(),
        })
        return context
