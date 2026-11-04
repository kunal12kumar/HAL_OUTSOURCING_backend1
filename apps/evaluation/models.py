import uuid

from django.conf import settings
from django.db import models

from apps.vendors.models import Vendor


class DocStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    PROCESSING = "PROCESSING", "Processing"
    READY = "READY", "Ready"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Ready, needs review"
    FAILED = "FAILED", "Failed"


class VendorDocument(models.Model):
    """An original file supplied by a vendor, plus what was extracted from it. Vendors are not registered
    for this module: the vendor record is just a name the uploader types or picks."""

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="eval_documents")
    file = models.FileField(upload_to="evaluation/vendor/%Y/%m/")
    filename = models.CharField(max_length=255)
    doc_type = models.CharField(max_length=24, default="OTHER")
    doc_type_confirmed = models.BooleanField(default=False)
    sha256 = models.CharField(max_length=64, db_index=True)
    size = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=14, choices=DocStatus.choices, default=DocStatus.UPLOADED)
    status_detail = models.TextField(blank=True)
    structured = models.JSONField(default=dict, blank=True)  # facts, warnings, page count
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vendor__name", "-created_at"]


class DocumentChunk(models.Model):
    document = models.ForeignKey(VendorDocument, on_delete=models.CASCADE, related_name="chunks")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="+")
    index = models.PositiveIntegerField()
    page_ref = models.CharField(max_length=80)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    doc_type = models.CharField(max_length=24)
    text = models.TextField()
    embedding = models.JSONField(default=dict)

    class Meta:
        ordering = ["document_id", "index"]


class AnalysisStatus(models.TextChoices):
    DRAFT = "DRAFT", "Requirements extracted"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    INCOMPLETE = "INCOMPLETE", "Completed with incomplete evaluations"
    FAILED = "FAILED", "Failed"


class TenderAnalysis(models.Model):
    evaluation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=200)
    tender_file = models.FileField(upload_to="evaluation/tender/%Y/%m/")
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=AnalysisStatus.choices, default=AnalysisStatus.DRAFT)
    requirements = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)    # weights etc. used for this run
    progress = models.JSONField(default=dict, blank=True)  # node -> {status, started_at, finished_at}
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
