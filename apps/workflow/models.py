from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max, Sum
from django.utils import timezone

from .numbering import next_number


class Counter(models.Model):
    key = models.CharField(max_length=40, unique=True)
    value = models.PositiveIntegerField(default=0)


class RequirementStage(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted to committee"
    RETURNED = "RETURNED", "Returned to MSR"
    APPROVED = "APPROVED", "Approved - awaiting Methods"
    METHODS_DONE = "METHODS_DONE", "Methods done - awaiting IMM"
    IMM_DONE = "IMM_DONE", "IMM done - awaiting Scheduling"
    ALLOCATED = "ALLOCATED", "Allocated - awaiting tender"
    TENDERING = "TENDERING", "Tender in progress"
    IN_EXECUTION = "IN_EXECUTION", "Order placed - in execution"
    CLOSED = "CLOSED", "Closed"
    INTERNAL_ONLY = "INTERNAL_ONLY", "Met within HAL - no outsourcing"


class Requirement(models.Model):
    number = models.CharField(max_length=24, unique=True, editable=False)
    title = models.CharField(max_length=200)
    equipment_name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit = models.CharField(max_length=16, default="Nos")
    purpose = models.TextField(blank=True)
    required_by = models.DateField(null=True, blank=True)
    ifs_ref = models.CharField(max_length=60, blank=True)
    efile_ref = models.CharField(max_length=60, blank=True)
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=16, choices=RequirementStage.choices, default=RequirementStage.DRAFT)
    methods_advice = models.TextField(blank=True)
    material_requirements = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number} {self.title}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number("REQ")
        super().save(*args, **kwargs)


class Allocation(models.Model):
    requirement = models.OneToOneField(Requirement, on_delete=models.CASCADE, related_name="allocation")
    in_house_qty = models.PositiveIntegerField(default=0)
    other_division_qty = models.PositiveIntegerField(default=0)
    other_division_name = models.CharField(max_length=120, blank=True)
    outsource_qty = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(auto_now=True)

    def clean(self):
        total = self.in_house_qty + self.other_division_qty + self.outsource_qty
        if total != self.requirement.quantity:
            raise ValidationError(
                f"The split adds up to {total} but the requirement is for {self.requirement.quantity}."
            )


class StageRecord(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected / returned"
        COMPLETED = "COMPLETED", "Completed"
        NOTE = "NOTE", "Note"

    requirement = models.ForeignKey(Requirement, null=True, blank=True, on_delete=models.CASCADE, related_name="trail")
    tender = models.ForeignKey("Tender", null=True, blank=True, on_delete=models.CASCADE, related_name="trail")
    stage = models.CharField(max_length=40)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    remarks = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_name = models.CharField(max_length=150)
    actor_role = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class TenderStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Awaiting committee approval"
    APPROVED = "APPROVED", "Approved - awaiting MSD estimate"
    ESTIMATED = "ESTIMATED", "Estimated - awaiting internal review"
    READY_TO_PUBLISH = "READY_TO_PUBLISH", "Ready to publish"
    PUBLISHED = "PUBLISHED", "Published"
    EVALUATION = "EVALUATION", "Under evaluation"
    PNC_REVIEW = "PNC_REVIEW", "With PNC Committee"
    AWARDED = "AWARDED", "Awarded"
    RETENDERED = "RETENDERED", "Superseded by a new round"


class Tender(models.Model):
    requirement = models.ForeignKey(Requirement, on_delete=models.PROTECT, related_name="tenders")
    round = models.PositiveIntegerField(default=1)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="retenders")
    number = models.CharField(max_length=24, unique=True, editable=False)
    scope = models.TextField()
    quantity = models.PositiveIntegerField()
    estimated_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=TenderStatus.choices, default=TenderStatus.DRAFT)
    efile_ref = models.CharField(max_length=60, blank=True)
    gem_ref = models.CharField(max_length=60, blank=True)
    eportal_ref = models.CharField(max_length=60, blank=True)
    closing_date = models.DateField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number} (round {self.round})"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number("TND")
        super().save(*args, **kwargs)


class Quotation(models.Model):
    class Source(models.TextChoices):
        GEM = "GEM", "GeM"
        EPORTAL = "EPORTAL", "HAL e-Portal"
        OTHER = "OTHER", "Other"

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="quotations")
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="quotations")
    price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    delivery_days = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.GEM)
    remarks = models.TextField(blank=True)
    technically_qualified = models.BooleanField(null=True, blank=True)
    technical_remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tender", "vendor")]
        ordering = ["price"]

    @property
    def within_estimate(self):
        est = self.tender.estimated_price
        return est is not None and self.price <= est

    @property
    def eligible(self):
        return bool(self.technically_qualified) and self.within_estimate


class POStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class PurchaseOrder(models.Model):
    number = models.CharField(max_length=24, unique=True, editable=False)
    po_ref = models.CharField(max_length=60, blank=True)
    tender = models.ForeignKey(Tender, on_delete=models.PROTECT, related_name="orders")
    quotation = models.OneToOneField(Quotation, on_delete=models.PROTECT, related_name="order")
    requirement = models.ForeignKey(Requirement, on_delete=models.PROTECT, related_name="orders")
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="orders")
    quantity = models.PositiveIntegerField()
    value = models.DecimalField(max_digits=14, decimal_places=2)
    delivery_date = models.DateField()
    terms = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=POStatus.choices, default=POStatus.ACTIVE)
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    placed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-placed_at"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number("PO")
        super().save(*args, **kwargs)

    def _sum(self, qs, field):
        return qs.aggregate(t=Sum(field))["t"] or 0

    @property
    def qty_pdi_accepted(self):
        return self._sum(self.inspections.filter(kind=Inspection.Kind.PDI, status=Inspection.Status.COMPLETED), "qty_accepted")

    @property
    def qty_dispatched_net(self):
        total = 0
        for c in self.consignments.all():
            delivered = c.qty_received if c.qty_received is not None else c.qty
            total += delivered - (c.qty_rejected or 0)
        return total

    @property
    def qty_dispatchable(self):
        return max(self.qty_pdi_accepted - self.qty_dispatched_net, 0)

    @property
    def qty_received(self):
        return sum(c.qty_received or 0 for c in self.consignments.all())

    @property
    def qty_accepted(self):
        return self._sum(self.consignments, "qty_accepted")

    @property
    def qty_finished(self):
        return self._sum(self.consignments.filter(status=Consignment.Status.FINISHED), "qty_accepted")

    @property
    def qty_outstanding(self):
        return max(self.quantity - self.qty_accepted, 0)

    @property
    def last_update_at(self):
        return self.updates.aggregate(m=Max("created_at"))["m"]

    @property
    def is_overdue(self):
        return self.status == POStatus.ACTIVE and self.delivery_date < timezone.localdate() and self.qty_finished < self.quantity

    @property
    def is_silent(self):
        if self.status != POStatus.ACTIVE:
            return False
        last = self.last_update_at or self.placed_at
        return (timezone.now() - last).days >= settings.VENDOR_STALE_DAYS


class MaterialIssue(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="material_issues")
    material = models.CharField(max_length=200)
    specification = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField(max_length=16, default="kg")
    vehicle_no = models.CharField(max_length=24, blank=True)
    dispatch_doc_no = models.CharField(max_length=60, blank=True)
    remarks = models.TextField(blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_qty = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ack_remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at"]


class ProgressUpdate(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="updates")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    qty_completed = models.PositiveIntegerField(default=0)
    percent_complete = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(100)])
    message = models.TextField(blank=True)
    expected_ready_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Inspection(models.Model):
    class Kind(models.TextChoices):
        PDI = "PDI", "Pre-dispatch inspection at vendor"
        HAL = "HAL", "Inspection at HAL"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="inspections")
    kind = models.CharField(max_length=4, choices=Kind.choices)
    consignment = models.ForeignKey("Consignment", null=True, blank=True, on_delete=models.CASCADE, related_name="inspections")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    qty_offered = models.PositiveIntegerField(default=0)
    qty_accepted = models.PositiveIntegerField(default=0)
    qty_rejected = models.PositiveIntegerField(default=0)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    request_note = models.TextField(blank=True)
    scheduled_date = models.DateField(null=True, blank=True)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    inspected_at = models.DateTimeField(null=True, blank=True)
    report = models.TextField(blank=True)
    deficiencies = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    @property
    def outcome(self):
        if self.status != self.Status.COMPLETED:
            return None
        if self.qty_accepted == 0:
            return "REJECTED"
        return "ACCEPTED" if self.qty_rejected == 0 else "PARTIAL"


class Consignment(models.Model):
    class Status(models.TextChoices):
        DISPATCHED = "DISPATCHED", "Dispatched by vendor"
        RECEIVED = "RECEIVED", "Received at gate - awaiting inspection"
        ACCEPTED = "ACCEPTED", "Accepted at HAL"
        REJECTED = "REJECTED", "Rejected at HAL"
        IN_FINISHING = "IN_FINISHING", "In finishing"
        FINISHED = "FINISHED", "Finished"

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="consignments")
    number = models.CharField(max_length=40, editable=False)
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    vehicle_no = models.CharField(max_length=24)
    challan_no = models.CharField(max_length=60)
    dispatched_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.DISPATCHED)
    qty_received = models.PositiveIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    gate_remarks = models.TextField(blank=True)
    qty_accepted = models.PositiveIntegerField(null=True, blank=True)
    qty_rejected = models.PositiveIntegerField(null=True, blank=True)
    finishing_started_at = models.DateTimeField(null=True, blank=True)
    finishing_completed_at = models.DateTimeField(null=True, blank=True)
    finishing_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    finishing_remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-dispatched_at"]
        unique_together = [("po", "number")]

    def save(self, *args, **kwargs):
        if not self.number:
            n = Consignment.objects.filter(po=self.po).count() + 1
            self.number = f"{self.po.number}/C{n}"
        super().save(*args, **kwargs)

    @property
    def shortage(self):
        if self.qty_received is None:
            return None
        return max(self.qty - self.qty_received, 0)


class Document(models.Model):
    class Type(models.TextChoices):
        DRAWING = "DRAWING", "Technical drawing"
        SPEC = "SPEC", "Specification"
        INSTRUCTION = "INSTRUCTION", "Manufacturing instruction"
        MODEL = "MODEL", "Model or diagram"
        INSPECTION = "INSPECTION", "Inspection report"
        DELIVERY = "DELIVERY", "Delivery document"
        PROGRESS = "PROGRESS", "Progress photo or report"
        OTHER = "OTHER", "Other"

    title = models.CharField(max_length=200)
    doc_type = models.CharField(max_length=12, choices=Type.choices, default=Type.OTHER)
    file = models.FileField(upload_to="documents/%Y/%m/")
    po = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    requirement = models.ForeignKey(Requirement, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    visible_to_vendor = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_name = models.CharField(max_length=150)
    actor_role = models.CharField(max_length=24)
    action = models.CharField(max_length=60)
    object_type = models.CharField(max_length=40)
    object_id = models.CharField(max_length=40)
    summary = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit records cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records cannot be deleted.")
