from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from core.models import User
from followups.models import FollowUpTask
from leads.models import Lead
from leads.models import Product

from .forms import BusinessForm, ProductForm, SignupForm, TeamUserForm


class LandingView(TemplateView):
    template_name = 'web/landing.html'


class SignupView(FormView):
    template_name = 'web/signup.html'
    form_class = SignupForm

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect('web:dashboard')


class TenantWebMixin(LoginRequiredMixin):
    """Shared request-level tenant and role scoping for server-rendered pages."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.business_id:
            return render(request, 'web/no_business.html', status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_business(self):
        return self.request.user.business

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

    def common_context(self):
        return {'business': self.get_business(), 'is_manager': self.request.user.role in (User.Role.OWNER, User.Role.MANAGER)}


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
            return HttpResponseForbidden('Only an owner or manager can view reports.')
        return super().dispatch(request, *args, **kwargs)


class DashboardView(TenantWebMixin, TemplateView):
    template_name = 'web/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        business = self.get_business()
        today = timezone.localdate(timezone=ZoneInfo(business.timezone))
        leads = self.visible_leads()
        tasks = self.visible_tasks()
        active_leads = leads.exclude(stage__in=(Lead.Stage.WON, Lead.Stage.LOST))
        open_tasks = tasks.filter(status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE))

        context.update(self.common_context())
        context.update({
            'today': today,
            'due_today_count': open_tasks.filter(due_at__date=today).count(),
            'overdue_count': tasks.filter(status=FollowUpTask.Status.OVERDUE).count(),
            'active_leads_count': active_leads.count(),
            'pipeline_value': active_leads.aggregate(total=Sum('quoted_price'))['total'] or 0,
            'tasks': open_tasks.order_by('due_at')[:6],
            'recent_leads': leads.order_by('-updated_at')[:6],
            'stage_counts': active_leads.values('stage').annotate(total=Count('id')).order_by('stage'),
            'needs_action_count': active_leads.exclude(
                follow_up_tasks__status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE)
            ).distinct().count(),
        })
        return context


class TaskListView(TenantWebMixin, ListView):
    template_name = 'web/task_list.html'
    context_object_name = 'tasks'
    paginate_by = 20

    def get_queryset(self):
        queryset = self.visible_tasks().filter(status__in=(FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE))
        status_filter = self.request.GET.get('status')
        if status_filter in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by('due_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['selected_status'] = self.request.GET.get('status', '')
        return context


class LeadListView(TenantWebMixin, ListView):
    template_name = 'web/lead_list.html'
    context_object_name = 'leads'
    paginate_by = 20

    def get_queryset(self):
        queryset = self.visible_leads()
        stage = self.request.GET.get('stage')
        search = self.request.GET.get('q')
        if stage in Lead.Stage.values:
            queryset = queryset.filter(stage=stage)
        if search:
            queryset = queryset.filter(
                Q(customer_name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search)
            )
        return queryset.order_by('-updated_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context.update({'stages': Lead.Stage.choices, 'selected_stage': self.request.GET.get('stage', ''), 'search': self.request.GET.get('q', '')})
        return context


class TeamListView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/team_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['team'] = User.objects.filter(business=self.get_business()).order_by('role', 'first_name', 'username')
        context['form'] = TeamUserForm()
        return context

    def post(self, request, *args, **kwargs):
        form = TeamUserForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.business = self.get_business()
            user.save()
            messages.success(request, f'{user.username} was added to your team.')
            return redirect('web:team-list')
        context = self.get_context_data()
        context['form'] = form
        return self.render_to_response(context)


class ProductListView(OwnerRequiredMixin, TemplateView):
    template_name = 'web/product_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context())
        context['products'] = Product.objects.for_business(self.get_business()).order_by('name')
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
        member = User.objects.get(pk=pk, business=self.get_business())
        if member == request.user:
            messages.error(request, 'You cannot remove your own account.')
        elif member.role == User.Role.OWNER and User.objects.filter(business=self.get_business(), role=User.Role.OWNER, is_active=True).count() == 1:
            messages.error(request, 'A business must keep at least one active owner.')
        else:
            member.delete()
            messages.success(request, 'Team member removed.')
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
        product = Product.objects.for_business(self.get_business()).get(pk=pk)
        product.delete()
        messages.success(request, 'Product removed.')
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
        total = leads.count() or 1
        source_rows = list(leads.values('source').annotate(total=Count('id'), won=Count('id', filter=Q(stage=Lead.Stage.WON))).order_by('-total'))
        for row in source_rows:
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
            row['width'] = round(row['total'] * 100 / total)
        salesperson_rows = list(leads.values('assigned_user__username').annotate(total=Count('id'), won=Count('id', filter=Q(stage=Lead.Stage.WON))).order_by('-total'))
        for row in salesperson_rows:
            row['conversion'] = round(row['won'] * 100 / row['total']) if row['total'] else 0
        closed_duration = leads.filter(closed_at__isnull=False).aggregate(
            average=Avg(ExpressionWrapper(F('closed_at') - F('created_at'), output_field=DurationField()))
        )['average']
        context.update(self.common_context())
        context.update({
            'source_rows': source_rows,
            'salesperson_rows': salesperson_rows,
            'average_days': round(closed_duration.total_seconds() / 86400, 1) if closed_duration else None,
            'lost_reasons': leads.filter(stage=Lead.Stage.LOST).exclude(lost_reason='').values('lost_reason').annotate(total=Count('id')).order_by('-total')[:6],
            'won_count': leads.filter(stage=Lead.Stage.WON).count(),
            'total_count': leads.count(),
        })
        return context
