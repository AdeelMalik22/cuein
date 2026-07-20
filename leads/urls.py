from rest_framework.routers import DefaultRouter

from .views import ActivityViewSet, LeadViewSet, ProductViewSet, SiteVisitViewSet

router = DefaultRouter()
router.register('activities', ActivityViewSet, basename='activity')
router.register('products', ProductViewSet, basename='product')
router.register('leads', LeadViewSet, basename='lead')
router.register('site-visits', SiteVisitViewSet, basename='site-visit')

urlpatterns = router.urls
