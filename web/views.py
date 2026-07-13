from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from core.models import User
from followups.models import FollowUpTask
from followups.rules import DELAYED_FOLLOWUP, rule_for_stage
from followups.tasks import schedule_follow_up
from leads.models import Activity, Lead, Product

from .forms import (
    ActivityForm,
    BusinessForm,
    LeadDetailsForm,
    LeadFollowUpForm,
    LeadQuickAddForm,
    LeadStageForm,
    ProductForm,
    SignupForm,
    TaskCompletionForm,
    TaskRescheduleForm,
    TeamUserForm,
)


def safe_post_redirect(request, fallback):
    """Keep small action forms on an internal page, never a posted external URL."""
    candidate = request.POST.get('next', '')
    if candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return fallback


class LandingView(TemplateView):
    template_name = 'web/landing.html'


class SignupView(FormView):
    template_name = 'web/signup.html'
    form_class = SignupForm

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect('web:onboarding')


class TenantWebMixin(LoginRequiredMixin):
    """Shared tenant and role scoping for the server-rendered application."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.business_id:
            return render(request, 'web/no_business.html', status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_business(self):
        return self.request.user.business

    def is_manager(self):
        return self.request.user.role in (User.Role.OWNER, User.Role.MANAGER)

    def visible_leads(self):
        queryset = Lead.objects.for_business(self.get_business()).select_related('assigned_user', 'product')
        if self.request.user.role == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        return queryset

    def visible_tasks(self):
        queryset = FollowUpTask.objects.for_business(self.get_business()).select_related('lead', 'assigned_user')
        if self.request.user.role == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        return queryset

    def get_visible_lead(self, pk):
        return get_object_or_404(self.visible_leads(), pk=pk)

    def get_visible_task(self, pk):
        return get_object_or_404(self.visible_tasks(), pk=pk)

    def common_context(self):
        return {
            'business': self.get_business(),
            'is_manager': self.is_manager(),
            'is_owner': self.request.user.role == User.Role.OWNER,
            'is_salesperson': self.request.user.role == User.Role.SALESPERSON,
        }


class OwnerRequiredMixin(TenantWebMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.business_id:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role != User.Role.OWNER:
            return HttpResponseForbidden('Only a business owner can manage this area.')
        return super().dispatch(request, *args, **kwargs)


class ManagerRequiredMixin(TenantWebMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.business_id:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in (User.Role.OWNER, User.Role.MANAGER):
            return HttpResponseForbidden('This area is for owners and managers.')
        return super().dispatch(request, *args, **kwargs)


class OnboardingView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/onboarding.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        business = self.get_business()
        context.update(self.common_context())
        context.update({
            'lead_count': Lead.objects.for_business(business).count(),
            'team_count': User.objects.filter(business=business, is_active=True).count(),
            'product_count': Product.objects.for_business(business).filter(is_active=True).count(),
        })
        return context


class DashboardView(TenantWebMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        # A salesperson's command centre is their personal pipeline. Sending
        # them there keeps the default route focused rather than a cut-down
        # version of the manager dashboard.
        if (
            request.user.is_authenticated
            and request.user.business_id
            and request.user.role == User.Role.SALESPERSON
        ):
            return redirect('web:lead-list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        business = self.get_business()
        now = timezone.now()
        today = timezone.localdate(now, timezone=ZoneInfo(business.timezone))
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
            for row in active_leads.values('stage').annotate(total=Count('id'))
        }
        active_count = active_leads.count()
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

        team_attention = []
        for teammate in User.objects.filter(
            business=business,
            is_active=True,
            role__in=(User.Role.OWNER, User.Role.MANAGER, User.Role.SALESPERSON),
        ).order_by('first_name', 'username'):
            member_leads = active_leads.filter(assigned_user=teammate)
            member_stalled = member_leads.filter(last_activity_at__lt=stale_cutoff).count()
            member_due = open_tasks.filter(assigned_user=teammate, due_at__date=today).count()
            team_attention.append({
                'member': teammate,
                'active_count': member_leads.count(),
                'stalled_count': member_stalled,
                'due_count': member_due,
                'needs_attention': bool(member_stalled or member_due),
            })

        needs_action_count = active_leads.exclude(
            follow_up_tasks__status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE),
        ).distinct().count()
        context.update(self.common_context())
        context.update({
            'today': today,
            'due_today_count': open_tasks.filter(due_at__date=today).count(),
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
            'team_attention': team_attention,
        })
        return context


class LeadListView(TenantWebMixin, ListView):
    template_name = 'web/lead_list.html'
    context_object_name = 'leads'
    paginate_by = None

    def get_queryset(self):
        queryset = self.visible_leads()
        stage = self.request.GET.get('stage')
        search = self.request.GET.get('q')
        if stage in Lead.Stage.values:
            queryset = queryset.filter(stage=stage)
        if search:
            queryset = queryset.filter(
                Q(customer_name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search),
            )
        return queryset.order_by('-last_activity_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board_leads = list(context['leads'])
        leads_by_stage = {value: [] for value, _label in Lead.Stage.choices}
        for lead in board_leads:
            leads_by_stage[lead.stage].append(lead)
        context.update(self.common_context())
        context.update({
            'stages': Lead.Stage.choices,
            'pipeline_columns': [
                {'value': value, 'label': label, 'leads': leads_by_stage[value]}
                for value, label in Lead.Stage.choices
            ],
            'selected_stage': self.request.GET.get('stage', ''),
            'search': self.request.GET.get('q', ''),
        })
        return context


class LeadCreateView(TenantWebMixin, FormView):
    template_name = 'web/lead_quick_add.html'
    form_class = LeadQuickAddForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({'business': self.get_business(), 'user': self.request.user})
        return kwargs

    def form_valid(self, form):
        lead = form.save(commit=False)
        lead.business = self.get_business()
        lead.assigned_user = form.cleaned_data.get('assigned_user') or self.request.user
        lead.save()
        Activity.objects.create(
            business=lead.business,
            lead=lead,
            kind=Activity.Kind.SYSTEM,
            content='Lead captured.',
            created_by=self.request.user,
        )
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
            'details_form': LeadDetailsForm(instance=lead, business=self.get_business(), user=self.request.user),
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
        form = LeadDetailsForm(request.POST, instance=lead, business=self.get_business(), user=request.user)
        if form.is_valid():
            updated_lead = form.save(commit=False)
            if request.user.role == User.Role.SALESPERSON:
                updated_lead.assigned_user = lead.assigned_user
            updated_lead.save()
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
            if request.user.role == User.Role.SALESPERSON and locked_lead.assigned_user_id != request.user.id:
                return HttpResponseForbidden('You can only update your own leads.')

            previous_stage = locked_lead.stage
            locked_lead.stage = next_stage
            locked_lead.lost_reason = form.cleaned_data.get('lost_reason', '').strip() if next_stage == Lead.Stage.LOST else ''
            locked_lead.closed_at = timezone.now() if next_stage in (Lead.Stage.WON, Lead.Stage.LOST) else None
            locked_lead.last_activity_at = timezone.now()
            locked_lead.save()

            if previous_stage != next_stage:
                Activity.objects.create(
                    business=locked_lead.business,
                    lead=locked_lead,
                    kind=Activity.Kind.STAGE_CHANGE,
                    content=(
                        f'Moved from {Lead.Stage(previous_stage).label} '
                        f'to {Lead.Stage(next_stage).label}.'
                    ),
                    metadata={'from': previous_stage, 'to': next_stage},
                    created_by=request.user,
                )
            if note:
                Activity.objects.create(
                    business=locked_lead.business,
                    lead=locked_lead,
                    kind=Activity.Kind.NOTE,
                    content=note,
                    created_by=request.user,
                )
            rule = rule_for_stage(next_stage) if previous_stage != next_stage else None
            if rule:
                transaction.on_commit(
                    lambda: schedule_follow_up.delay(str(locked_lead.business_id), str(locked_lead.id), rule.key),
                )

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
        if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
            messages.error(request, 'Closed leads cannot receive another follow-up reminder.')
            return redirect('web:lead-detail', pk=lead.pk)
        with transaction.atomic():
            lead.last_activity_at = timezone.now()
            lead.save(update_fields=('last_activity_at', 'updated_at'))
            Activity.objects.create(
                business=lead.business,
                lead=lead,
                kind=Activity.Kind.NOTE,
                content='Customer needs more time. A follow-up has been set for seven days from now.',
                created_by=request.user,
            )
            transaction.on_commit(
                lambda: schedule_follow_up.delay(str(lead.business_id), str(lead.id), DELAYED_FOLLOWUP.key),
            )
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
    paginate_by = None

    def get_queryset(self):
        queryset = self.visible_tasks().filter(status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE))
        status_filter = self.request.GET.get('status')
        if status_filter in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by('due_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context.update({
            'selected_status': self.request.GET.get('status', ''),
            'today': timezone.localdate(timezone.now(), timezone=ZoneInfo(self.get_business().timezone)),
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
            if request.user.role == User.Role.SALESPERSON and task.assigned_user_id != request.user.id:
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
        context['team'] = User.objects.filter(business=self.get_business()).order_by('-is_active', 'role', 'first_name', 'username')
        context['form'] = TeamUserForm()
        return context

    def post(self, request, *args, **kwargs):
        form = TeamUserForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.business = self.get_business()
            user.save()
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
        context['form'] = ProductForm()
        return context

    def post(self, request, *args, **kwargs):
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.business = self.get_business()
            product.save()
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
        return User.objects.filter(business=self.get_business())

    def form_valid(self, form):
        form.save()
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
        member = get_object_or_404(User, pk=pk, business=self.get_business())
        if member == request.user:
            messages.error(request, 'You cannot deactivate your own account.')
        elif member.role == User.Role.OWNER and User.objects.filter(
            business=self.get_business(), role=User.Role.OWNER, is_active=True,
        ).count() == 1:
            messages.error(request, 'A business must keep at least one active owner.')
        else:
            member.is_active = False
            member.save(update_fields=('is_active',))
            messages.success(request, 'Team member deactivated. Their lead history is still intact.')
        return redirect('web:team-list')


class ProductEditView(OwnerRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = 'web/edit_form.html'

    def get_queryset(self):
        return Product.objects.for_business(self.get_business())

    def form_valid(self, form):
        form.save()
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
        messages.success(request, 'Product deactivated. Existing lead history is preserved.')
        return redirect('web:product-list')


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
            leads.values('assigned_user__username', 'assigned_user__first_name', 'assigned_user__last_name').annotate(
                total=Count('id'),
                won=Count('id', filter=Q(stage=Lead.Stage.WON)),
            ).order_by('-total'),
        )
        for row in salesperson_rows:
            row['label'] = ' '.join(
                value for value in (row['assigned_user__first_name'], row['assigned_user__last_name']) if value
            ) or row['assigned_user__username']
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
            row['width'] = row['conversion']

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
            'product_rows': product_rows,
            'lost_reasons': lost_reasons,
            'average_days': round(closed_duration.total_seconds() / 86400, 1) if closed_duration else None,
            'won_count': leads.filter(stage=Lead.Stage.WON).count(),
            'total_count': leads.count(),
        })
        return context
