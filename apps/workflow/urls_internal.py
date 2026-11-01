from django.urls import path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import auth_views as a
from . import views_internal as v

router = SimpleRouter()
router.register("requirements", v.RequirementViewSet, basename="requirement")
router.register("tenders", v.TenderViewSet, basename="tender")
router.register("vendors", v.VendorViewSet, basename="vendor")
router.register("orders", v.OrderViewSet, basename="order")
router.register("inspections", v.InspectionViewSet, basename="inspection")
router.register("consignments", v.ConsignmentViewSet, basename="consignment")
router.register("audit", v.AuditViewSet, basename="audit")
router.register("users", v.UserViewSet, basename="user")

urlpatterns = [
    path("auth/login/", a.InternalLoginView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/me/", a.InternalMeView.as_view()),
    path("auth/change-password/", a.InternalChangePasswordView.as_view()),
    path("dashboard/", v.DashboardView.as_view()),
    path("queue/", v.QueueView.as_view()),
    path("export/<str:name>/", v.ExportView.as_view()),
    path("documents/<int:pk>/download/", v.DocumentDownloadView.as_view()),
] + router.urls
