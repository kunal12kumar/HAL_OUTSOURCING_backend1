from django.urls import path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import auth_views as a
from . import views_vendor as v

router = SimpleRouter()
router.register("orders", v.VendorOrderViewSet, basename="vendor-order")

urlpatterns = [
    path("auth/login/", a.VendorLoginView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/me/", a.VendorMeView.as_view()),
    path("auth/change-password/", a.VendorChangePasswordView.as_view()),
    path("dashboard/", v.VendorDashboardView.as_view()),
    path("documents/<int:pk>/download/", v.VendorDocumentDownloadView.as_view()),
] + router.urls
