from rest_framework.routers import DefaultRouter

from .views import LeadViewSet, ProductViewSet

router = DefaultRouter()
router.register('products', ProductViewSet, basename='product')
router.register('leads', LeadViewSet, basename='lead')

urlpatterns = router.urls
