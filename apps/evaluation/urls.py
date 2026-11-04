from django.urls import path

from . import views as v

urlpatterns = [
    path("vendor-documents/", v.VendorDocumentsView.as_view()),
    path("vendor-documents/<int:pk>/", v.VendorDocumentDetailView.as_view()),
    path("vendor-documents/<int:pk>/reprocess/", v.VendorDocumentReprocessView.as_view()),
    path("vendor-documents/<int:pk>/download/", v.VendorDocumentDownloadView.as_view()),
    path("analyses/", v.AnalysesView.as_view()),
    path("analyses/<uuid:pk>/", v.AnalysisDetailView.as_view()),
    path("analyses/<uuid:pk>/start/", v.AnalysisStartView.as_view()),
]
