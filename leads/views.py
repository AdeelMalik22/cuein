from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import User
from core.permissions import IsBusinessManagerOrOwner

from .models import Lead, Product
from .serializers import (
    LeadAssignmentSerializer,
    LeadSerializer,
    LeadTransitionSerializer,
    ProductSerializer,
)
from followups.rules import rule_for_stage
from followups.rules import DELAYED_FOLLOWUP
from followups.tasks import schedule_follow_up


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ('name', 'description')
    ordering_fields = ('name', 'created_at', 'is_active')
    ordering = ('name',)

    def get_queryset(self):
        return Product.objects.for_business(self.request.user.business)

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return (IsBusinessManagerOrOwner(),)
        return (IsAuthenticated(),)

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    filter_backends = (filters.SearchFilter, filters.OrderingFilter)
    search_fields = ('customer_name', 'phone', 'email')
    ordering_fields = ('created_at', 'updated_at', 'last_activity_at', 'quoted_price')
    ordering = ('-updated_at',)

    def get_queryset(self):
        queryset = Lead.objects.for_business(self.request.user.business).select_related('product', 'assigned_user')
        if self.request.user.role == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)

        for field in ('stage', 'source', 'assigned_user', 'product'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def get_permissions(self):
        if self.action in ('destroy', 'assign'):
            return (IsBusinessManagerOrOwner(),)
        return (IsAuthenticated(),)

    def perform_create(self, serializer):
        assigned_user = serializer.validated_data.get('assigned_user', self.request.user)
        if self.request.user.role == User.Role.SALESPERSON:
            assigned_user = self.request.user
        serializer.save(business=self.request.user.business, assigned_user=assigned_user)

    def perform_update(self, serializer):
        if self.request.user.role == User.Role.SALESPERSON and 'assigned_user' in self.request.data:
            raise ValidationError({'assigned_user': 'Salespeople cannot reassign leads.'})
        serializer.save()

    @action(detail=True, methods=('post',))
    def assign(self, request, pk=None):
        lead = self.get_object()
        serializer = LeadAssignmentSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        lead.assigned_user = serializer.validated_data['assigned_user']
        lead.save(update_fields=('assigned_user', 'updated_at'))
        return Response(LeadSerializer(lead, context={'request': request}).data)

    @action(detail=True, methods=('post',))
    def transition(self, request, pk=None):
        with transaction.atomic():
            lead = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            serializer = LeadTransitionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            lead.stage = serializer.validated_data['stage']
            lead.lost_reason = serializer.validated_data.get('lost_reason', '')
            lead.closed_at = timezone.now() if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST) else None
            lead.last_activity_at = timezone.now()
            lead.full_clean()
            lead.save()
            rule = rule_for_stage(lead.stage)
            if rule:
                transaction.on_commit(
                    lambda: schedule_follow_up.delay(str(lead.business_id), str(lead.id), rule.key)
                )

        return Response(LeadSerializer(lead, context={'request': request}).data)

    @action(detail=True, methods=('post',), url_path='needs-time')
    def needs_time(self, request, pk=None):
        lead = self.get_object()
        if lead.stage in (Lead.Stage.WON, Lead.Stage.LOST):
            raise ValidationError({'detail': 'Terminal leads cannot receive a follow-up reminder.'})
        transaction.on_commit(
            lambda: schedule_follow_up.delay(str(lead.business_id), str(lead.id), DELAYED_FOLLOWUP.key)
        )
        return Response(status=status.HTTP_202_ACCEPTED)

# Create your views here.
