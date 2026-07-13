from rest_framework.routers import DefaultRouter

from .views import FollowUpTaskViewSet, NotificationViewSet

router = DefaultRouter()
router.register('follow-up-tasks', FollowUpTaskViewSet, basename='follow-up-task')
router.register('notifications', NotificationViewSet, basename='notification')

urlpatterns = router.urls
