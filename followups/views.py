from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from zoneinfo import ZoneInfo
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import User
from core.permissions import IsBusinessManagerOrOwner
from core.tenancy import active_business, active_role

from .models import FollowUpTask, Notification
from .serializers import (
    CompleteTaskSerializer,
    FollowUpTaskSerializer,
    NotificationSerializer,
    RescheduleTaskSerializer,
)


class FollowUpTaskViewSet(viewsets.ModelViewSet):
    serializer_class = FollowUpTaskSerializer
    permission_classes = (IsAuthenticated,)
    filter_backends = (filters.OrderingFilter,)
    ordering_fields = ('due_at', 'created_at')
    ordering = ('due_at',)

    def get_queryset(self):
        business = active_business(self.request)
        queryset = FollowUpTask.objects.for_business(business).select_related('lead', 'assigned_user')
        if active_role(self.request) == User.Role.SALESPERSON:
            queryset = queryset.filter(assigned_user=self.request.user)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        if self.request.query_params.get('due') == 'today':
            local_today = timezone.localdate(timezone=ZoneInfo(business.timezone))
            queryset = queryset.filter(due_at__date=local_today)
        return queryset

    def perform_create(self, serializer):
        assignee = serializer.validated_data.get('assigned_user', self.request.user)
        if active_role(self.request) == User.Role.SALESPERSON:
            assignee = self.request.user
        serializer.save(business=active_business(self.request), assigned_user=assignee)

    def perform_update(self, serializer):
        if active_role(self.request) == User.Role.SALESPERSON and 'assigned_user' in self.request.data:
            raise ValidationError({'assigned_user': 'Salespeople cannot reassign tasks.'})
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        if active_role(request) == User.Role.SALESPERSON:
            return Response({'detail': 'Only an owner or manager can cancel a task.'}, status=status.HTTP_403_FORBIDDEN)
        task = self.get_object()
        task.status = FollowUpTask.Status.CANCELLED
        task.save(update_fields=('status',))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=('post',))
    def complete(self, request, pk=None):
        task = self.get_object()
        serializer = CompleteTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if task.status not in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            raise ValidationError({'detail': 'Only open tasks can be completed.'})
        with transaction.atomic():
            task = FollowUpTask.objects.select_for_update().get(pk=task.pk)
            if task.status not in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
                raise ValidationError({'detail': 'Only open tasks can be completed.'})
            task.mark_done()
            task.save(update_fields=('status', 'completed_at'))
            next_task = FollowUpTask.objects.create(
                business=task.business,
                lead=task.lead,
                assigned_user=task.assigned_user,
                due_at=serializer.validated_data['next_due_at'],
                description=serializer.validated_data['next_description'],
            )
        return Response(FollowUpTaskSerializer(next_task, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=('post',))
    def reschedule(self, request, pk=None):
        task = self.get_object()
        serializer = RescheduleTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if task.status not in (FollowUpTask.Status.PENDING, FollowUpTask.Status.OVERDUE):
            raise ValidationError({'detail': 'Only open tasks can be rescheduled.'})
        task.due_at = serializer.validated_data['due_at']
        task.status = FollowUpTask.Status.PENDING
        task.save(update_fields=('due_at', 'status'))
        return Response(FollowUpTaskSerializer(task, context={'request': request}).data)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        return Notification.objects.for_business(active_business(self.request)).filter(
            recipient=self.request.user
        ).select_related('task', 'task__lead', 'task__assigned_user')

    @action(detail=True, methods=('post',))
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=('read_at',))
        return Response(NotificationSerializer(notification, context={'request': request}).data)
