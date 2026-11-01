from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    """Unauthenticated liveness and database check for load balancers and container health checks."""
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health/", health),
    path("admin/", admin.site.urls),
    path("api/internal/", include("apps.workflow.urls_internal")),
    path("api/vendor/", include("apps.workflow.urls_vendor")),
]
