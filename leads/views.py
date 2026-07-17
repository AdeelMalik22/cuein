from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination, PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.authentication import BusinessScopedJWTAuthentication, WorkspaceSessionAuthentication
from core.models import User
from core.permissions import IsBusinessManagerOrOwner
from core.tenancy import active_business, active_role

from .cache import (
    cache_lead_response,
    get_cached_lead_response,
    invalidate_business_lead_cache,
    lead_api_cache_key,
)
from .models import Activity, Lead, Product
from .serializers import (
    ActivitySerializer,
    LeadAssignmentSerializer,
    LeadKanbanCardSerializer,
    LeadSerializer,
    LeadTransitionSerializer,
    ProductSerializer,
)
from .services import record_lead_capture, record_needs_time, transition_lead


class LeadPageNumberPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class LeadKanbanPagination(LimitOffsetPagination):
    default_limit = 10
    limit_query_param = 'limit'
    max_limit = 100


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ('name', 'description')
    ordering_fields = ('name', 'created_at', 'is_active')
    ordering = ('name',)

    def get_queryset(self):
        return Product.objects.for_business(active_business(self.request))

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return (IsBusinessManagerOrOwner(),)
        return (IsAuthenticated(),)

    def perform_create(self, serializer):
        product = serializer.save(business=active_business(self.request))
        transaction.on_commit(lambda: invalidate_business_lead_cache(product.business_id))

    def perform_update(self, serializer):
        product = serializer.save()
        transaction.on_commit(lambda: invalidate_business_lead_cache(product.business_id))

    def perform_destroy(self, instance):
        business_id = instance.business_id
        instance.delete()
        transaction.on_commit(lambda: invalidate_business_lead_cache(business_id))


class ActivityViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ActivitySerializer
    permission_classes = (IsAuthenticated,)
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ('created_at', 'kind')
    ordering = ('-created_at',)

    def get_queryset(self):
        queryset = Activity.objects.for_business(active_business(self.request)).select_related(
            'lead', 'created_by',
        )
        if active_role(self.request) == User.Role.SALESPERSON:
            queryset = queryset.filter(lead__assigned_user=self.request.user)

        lead_id = self.request.query_params.get('lead')
        if lead_id:
            try:
                queryset = queryset.filter(lead_id=UUID(lead_id))
            except (TypeError, ValueError, AttributeError):
                return queryset.none()
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            activity = serializer.save()
            transaction.on_commit(lambda: invalidate_business_lead_cache(activity.business_id))


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    pagination_class = LeadPageNumberPagination
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ('customer_name', 'phone', 'email')
    ordering_fields = ('created_at', 'updated_at', 'last_activity_at', 'quoted_price')
    ordering = ('-last_activity_at', '-id')

    def get_queryset(self):
        queryset = Lead.objects.for_business(active_business(self.request))
        if self.action == 'kanban':
            queryset = queryset.select_related('product').only(
                'id', 'customer_name', 'stage', 'quoted_price', 'last_activity_at', 'product_id', 'product__id',
                'product__name',
            )
        else:
            queryset = queryset.select_related('product', 'assigned_user')
        if active_role(self.request) == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)

        for field in ('stage', 'source', 'assigned_user', 'product'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def get_serializer_class(self):
        if self.action == 'kanban':
            return LeadKanbanCardSerializer
        return super().get_serializer_class()

    def get_permissions(self):
        if self.action in ('destroy', 'assign'):
            return (IsBusinessManagerOrOwner(),)
        return (IsAuthenticated(),)

    def perform_create(self, serializer):
        assigned_user = serializer.validated_data.get('assigned_user', self.request.user)
        if active_role(self.request) == User.Role.SALESPERSON:
            assigned_user = self.request.user
        with transaction.atomic():
            lead = serializer.save(business=active_business(self.request), assigned_user=assigned_user)
            record_lead_capture(lead=lead, actor=self.request.user)
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))

    def perform_update(self, serializer):
        if active_role(self.request) == User.Role.SALESPERSON and 'assigned_user' in self.request.data:
            raise ValidationError({'assigned_user': 'Salespeople cannot reassign leads.'})
        lead = serializer.save()
        transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))

    def perform_destroy(self, instance):
        business_id = instance.business_id
        instance.delete()
        transaction.on_commit(lambda: invalidate_business_lead_cache(business_id))

    def list(self, request, *args, **kwargs):
        cache_key = lead_api_cache_key(
            business_id=active_business(request).id,
            user=request.user,
            role=active_role(request),
            action=self.action,
            query_params=request.query_params,
        )
        cached_payload = get_cached_lead_response(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache_lead_response(cache_key, response.data)
        return response

    @action(
        detail=False,
        methods=('get',),
        url_path='kanban',
        authentication_classes=(BusinessScopedJWTAuthentication, WorkspaceSessionAuthentication),
    )
    def kanban(self, request):
        if request.query_params.get('stage') not in Lead.Stage.values:
            raise ValidationError({'stage': 'Choose a valid pipeline stage.'})
        self.pagination_class = LeadKanbanPagination
        return self.list(request)

    @action(detail=True, methods=('post',))
    def assign(self, request, pk=None):
        lead = self.get_object()
        serializer = LeadAssignmentSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        lead.assigned_user = serializer.validated_data['assigned_user']
        lead.save(update_fields=('assigned_user', 'updated_at'))
        transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))
        return Response(LeadSerializer(lead, context={'request': request}).data)

    @action(detail=True, methods=('post',))
    def transition(self, request, pk=None):
        with transaction.atomic():
            # Lock the lead row itself. The normal list queryset joins the
            # nullable product relation, which PostgreSQL correctly refuses
            # to lock with FOR UPDATE.
            locked_queryset = Lead.objects.for_business(active_business(request))
            if active_role(request) == User.Role.SALESPERSON:
                locked_queryset = locked_queryset.filter(assigned_user=request.user)
            lead = get_object_or_404(locked_queryset.select_for_update(), pk=pk)
            serializer = LeadTransitionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            lead, _stage_changed = transition_lead(
                lead=lead,
                stage=serializer.validated_data['stage'],
                lost_reason=serializer.validated_data.get('lost_reason', ''),
                actor=request.user,
            )
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))

        return Response(LeadSerializer(lead, context={'request': request}).data)

    @action(detail=True, methods=('post',), url_path='needs-time')
    def needs_time(self, request, pk=None):
        with transaction.atomic():
            locked_queryset = Lead.objects.for_business(active_business(request))
            if active_role(request) == User.Role.SALESPERSON:
                locked_queryset = locked_queryset.filter(assigned_user=request.user)
            lead = get_object_or_404(locked_queryset.select_for_update(), pk=pk)
            if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
                raise ValidationError({'detail': 'Terminal leads cannot receive a follow-up reminder.'})
            record_needs_time(lead=lead, actor=request.user)
            transaction.on_commit(lambda: invalidate_business_lead_cache(lead.business_id))
        return Response(status=status.HTTP_202_ACCEPTED)

# Create your views here.
