from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.models import Role
from apps.vendors.models import Vendor

from .actions import actions_for
from .models import (
    Allocation,
    AuditLog,
    Consignment,
    Document,
    Inspection,
    MaterialIssue,
    ProgressUpdate,
    PurchaseOrder,
    Quotation,
    Requirement,
    StageRecord,
    Tender,
)

User = get_user_model()


def _role(serializer):
    request = serializer.context.get("request")
    return request.user.role if request else None


# ----------------------------------------------------------------- users, vendors

class UserSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "role", "role_label",
                  "employee_id", "vendor", "must_change_password", "is_active"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "role", "employee_id", "password"]

    def validate_role(self, value):
        if value == Role.VENDOR:
            raise serializers.ValidationError("Vendor logins are issued from the vendor record, not here.")
        return value

    def validate(self, attrs):
        validate_password(attrs["password"], User(username=attrs["username"]))
        return attrs

    def create(self, validated):
        password = validated.pop("password")
        user = User(**validated)
        user.set_password(password)
        user.must_change_password = True
        user.save()
        return user


class VendorSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    order_count = serializers.SerializerMethodField()
    login_username = serializers.SerializerMethodField()

    class Meta:
        model = Vendor
        fields = ["id", "code", "name", "contact_person", "email", "phone", "gstin", "address",
                  "status", "status_label", "credentials_issued_at", "order_count", "login_username"]
        read_only_fields = ["code", "status", "credentials_issued_at"]

    def get_order_count(self, obj):
        return obj.orders.count()

    def get_login_username(self, obj):
        u = obj.users.order_by("id").first()
        return u.username if u else None


# ------------------------------------------------------------------ requirements

class AllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Allocation
        fields = ["in_house_qty", "other_division_qty", "other_division_name", "outsource_qty", "remarks", "set_at"]


class StageRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = StageRecord
        fields = ["id", "stage", "decision", "remarks", "actor_name", "actor_role", "created_at"]


class DocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ["id", "title", "doc_type", "visible_to_vendor", "uploaded_by_name", "created_at", "download_url"]

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.get_full_name() or obj.uploaded_by.username

    def get_download_url(self, obj):
        return f"/api/internal/documents/{obj.id}/download/"


class RequirementListSerializer(serializers.ModelSerializer):
    stage_label = serializers.CharField(source="get_stage_display", read_only=True)
    raised_by_name = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Requirement
        fields = ["id", "number", "title", "equipment_name", "quantity", "unit", "required_by",
                  "stage", "stage_label", "raised_by_name", "created_at", "updated_at", "actions"]

    def get_raised_by_name(self, obj):
        return obj.raised_by.get_full_name() or obj.raised_by.username

    def get_actions(self, obj):
        return actions_for("requirement", obj.stage, _role(self))


class RequirementDetailSerializer(RequirementListSerializer):
    allocation = AllocationSerializer(read_only=True)
    trail = StageRecordSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    tenders = serializers.SerializerMethodField()

    class Meta(RequirementListSerializer.Meta):
        fields = RequirementListSerializer.Meta.fields + [
            "description", "purpose", "ifs_ref", "efile_ref", "methods_advice",
            "material_requirements", "allocation", "trail", "documents", "tenders",
        ]

    def get_tenders(self, obj):
        return [{"id": t.id, "number": t.number, "round": t.round, "status": t.status,
                 "status_label": t.get_status_display()} for t in obj.tenders.all()]


class RequirementWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Requirement
        fields = ["title", "equipment_name", "description", "quantity", "unit", "purpose", "required_by"]


# ------------------------------------------------------------------------ tenders

class QuotationSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)
    within_estimate = serializers.BooleanField(read_only=True)
    eligible = serializers.BooleanField(read_only=True)

    class Meta:
        model = Quotation
        fields = ["id", "vendor", "vendor_name", "vendor_code", "price", "delivery_days", "source", "remarks",
                  "technically_qualified", "technical_remarks", "within_estimate", "eligible", "created_at"]


class TenderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    requirement_number = serializers.CharField(source="requirement.number", read_only=True)
    requirement_title = serializers.CharField(source="requirement.title", read_only=True)
    quotation_count = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Tender
        fields = ["id", "number", "round", "requirement", "requirement_number", "requirement_title", "quantity",
                  "estimated_price", "status", "status_label", "closing_date", "quotation_count", "created_at", "actions"]

    def get_quotation_count(self, obj):
        return obj.quotations.count()

    def get_actions(self, obj):
        return actions_for("tender", obj.status, _role(self))


class TenderDetailSerializer(TenderListSerializer):
    quotations = QuotationSerializer(many=True, read_only=True)
    trail = StageRecordSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    parent_number = serializers.SerializerMethodField()
    rounds = serializers.SerializerMethodField()
    orders = serializers.SerializerMethodField()

    class Meta(TenderListSerializer.Meta):
        fields = TenderListSerializer.Meta.fields + [
            "scope", "efile_ref", "gem_ref", "eportal_ref", "published_at",
            "quotations", "trail", "documents", "parent_number", "rounds", "orders",
        ]

    def get_parent_number(self, obj):
        return obj.parent.number if obj.parent_id else None

    def get_rounds(self, obj):
        return [{"id": t.id, "number": t.number, "round": t.round, "status": t.status,
                 "estimated_price": t.estimated_price} for t in obj.requirement.tenders.order_by("round")]

    def get_orders(self, obj):
        return [{"id": o.id, "number": o.number} for o in obj.orders.all()]


# ----------------------------------------------------------------------- execution

class MaterialIssueSerializer(serializers.ModelSerializer):
    issued_by_name = serializers.SerializerMethodField()

    class Meta:
        model = MaterialIssue
        fields = ["id", "material", "specification", "quantity", "unit", "vehicle_no", "dispatch_doc_no", "remarks",
                  "issued_by_name", "issued_at", "acknowledged_at", "acknowledged_qty", "ack_remarks"]

    def get_issued_by_name(self, obj):
        return obj.issued_by.get_full_name() or obj.issued_by.username


class ProgressUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressUpdate
        fields = ["id", "qty_completed", "percent_complete", "message", "expected_ready_date", "created_at"]


class InspectionSerializer(serializers.ModelSerializer):
    outcome = serializers.CharField(read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    po_number = serializers.CharField(source="po.number", read_only=True)
    vendor_name = serializers.CharField(source="po.vendor.name", read_only=True)
    consignment_number = serializers.SerializerMethodField()
    inspector_name = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = ["id", "po", "po_number", "vendor_name", "kind", "kind_label", "consignment", "consignment_number",
                  "status", "status_label", "outcome", "qty_offered", "qty_accepted", "qty_rejected", "requested_at",
                  "request_note", "scheduled_date", "inspector_name", "inspected_at", "report", "deficiencies",
                  "internal_notes", "actions"]

    def get_consignment_number(self, obj):
        return obj.consignment.number if obj.consignment_id else None

    def get_inspector_name(self, obj):
        return (obj.inspector.get_full_name() or obj.inspector.username) if obj.inspector_id else None

    def get_actions(self, obj):
        return actions_for("inspection", obj.status, _role(self))


class ConsignmentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    po_number = serializers.CharField(source="po.number", read_only=True)
    vendor_name = serializers.CharField(source="po.vendor.name", read_only=True)
    shortage = serializers.IntegerField(read_only=True)
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Consignment
        fields = ["id", "po", "po_number", "vendor_name", "number", "qty", "vehicle_no", "challan_no", "dispatched_at",
                  "status", "status_label", "qty_received", "received_at", "gate_remarks", "shortage", "qty_accepted",
                  "qty_rejected", "finishing_started_at", "finishing_completed_at", "finishing_remarks", "actions"]

    def get_actions(self, obj):
        return actions_for("consignment", obj.status, _role(self))


class OrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    vendor_code = serializers.CharField(source="vendor.code", read_only=True)
    requirement_number = serializers.CharField(source="requirement.number", read_only=True)
    equipment_name = serializers.CharField(source="requirement.equipment_name", read_only=True)
    qty_accepted = serializers.IntegerField(read_only=True)
    qty_finished = serializers.IntegerField(read_only=True)
    qty_outstanding = serializers.IntegerField(read_only=True)
    last_update_at = serializers.DateTimeField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    is_silent = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "po_ref", "vendor", "vendor_name", "vendor_code", "requirement_number", "equipment_name",
                  "quantity", "value", "delivery_date", "status", "status_label", "qty_accepted", "qty_finished",
                  "qty_outstanding", "last_update_at", "is_overdue", "is_silent", "placed_at"]


class OrderDetailSerializer(OrderListSerializer):
    material_issues = MaterialIssueSerializer(many=True, read_only=True)
    updates = ProgressUpdateSerializer(many=True, read_only=True)
    inspections = serializers.SerializerMethodField()
    consignments = serializers.SerializerMethodField()
    documents = DocumentSerializer(many=True, read_only=True)
    qty_pdi_accepted = serializers.IntegerField(read_only=True)
    qty_dispatchable = serializers.IntegerField(read_only=True)
    qty_received = serializers.IntegerField(read_only=True)
    tender_number = serializers.CharField(source="tender.number", read_only=True)
    requirement_id = serializers.IntegerField(read_only=True)
    actions = serializers.SerializerMethodField()

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "terms", "tender", "tender_number", "requirement_id", "qty_pdi_accepted", "qty_dispatchable", "qty_received",
            "material_issues", "updates", "inspections", "consignments", "documents", "actions",
        ]

    def get_inspections(self, obj):
        return InspectionSerializer(obj.inspections.all(), many=True, context=self.context).data

    def get_consignments(self, obj):
        return ConsignmentSerializer(obj.consignments.all(), many=True, context=self.context).data

    def get_actions(self, obj):
        return actions_for("order", obj.status, _role(self))


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "actor_name", "actor_role", "action", "object_type", "object_id", "summary", "created_at"]


# -------------------------------------------------------------------- action input

class DecisionIn(serializers.Serializer):
    approve = serializers.BooleanField()
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class SubmitIn(serializers.Serializer):
    ifs_ref = serializers.CharField(required=False, allow_blank=True, default="")
    efile_ref = serializers.CharField(required=False, allow_blank=True, default="")


class AdviceIn(serializers.Serializer):
    text = serializers.CharField()


class AllocateIn(serializers.Serializer):
    in_house_qty = serializers.IntegerField(min_value=0)
    other_division_qty = serializers.IntegerField(min_value=0)
    other_division_name = serializers.CharField(required=False, allow_blank=True, default="")
    outsource_qty = serializers.IntegerField(min_value=0)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class CreateTenderIn(serializers.Serializer):
    scope = serializers.CharField()


class EstimateIn(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class PublishIn(serializers.Serializer):
    gem_ref = serializers.CharField()
    eportal_ref = serializers.CharField()
    closing_date = serializers.DateField()


class QuotationIn(serializers.Serializer):
    vendor = serializers.PrimaryKeyRelatedField(queryset=Vendor.objects.all())
    price = serializers.DecimalField(max_digits=14, decimal_places=2)
    delivery_days = serializers.IntegerField(min_value=0, required=False, default=0)
    source = serializers.ChoiceField(choices=Quotation.Source.choices, default=Quotation.Source.GEM)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class EvaluateIn(serializers.Serializer):
    quotation = serializers.IntegerField()
    qualified = serializers.BooleanField()
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class AwardIn(serializers.Serializer):
    quotation = serializers.IntegerField()
    delivery_date = serializers.DateField(required=False, allow_null=True, default=None)
    po_ref = serializers.CharField(required=False, allow_blank=True, default="")
    terms = serializers.CharField(required=False, allow_blank=True, default="")


class PncIn(serializers.Serializer):
    revised_estimate = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True, default=None)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class RemarksIn(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class IssueMaterialIn(serializers.Serializer):
    material = serializers.CharField()
    specification = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit = serializers.CharField(default="kg")
    vehicle_no = serializers.CharField(required=False, allow_blank=True, default="")
    dispatch_doc_no = serializers.CharField(required=False, allow_blank=True, default="")
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class DeliveryDateIn(serializers.Serializer):
    delivery_date = serializers.DateField()
    remarks = serializers.CharField()


class ScheduleIn(serializers.Serializer):
    date = serializers.DateField()


class CompleteInspectionIn(serializers.Serializer):
    qty_accepted = serializers.IntegerField(min_value=0)
    qty_rejected = serializers.IntegerField(min_value=0)
    report = serializers.CharField()
    deficiencies = serializers.CharField(required=False, allow_blank=True, default="")
    internal_notes = serializers.CharField(required=False, allow_blank=True, default="")


class GateReceiptIn(serializers.Serializer):
    qty_received = serializers.IntegerField(min_value=0)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class DocumentUploadIn(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    doc_type = serializers.ChoiceField(choices=Document.Type.choices, default=Document.Type.OTHER)
    file = serializers.FileField()
    visible_to_vendor = serializers.BooleanField(required=False, default=False)

    ALLOWED_EXTENSIONS = {
        "pdf", "png", "jpg", "jpeg", "doc", "docx", "xls", "xlsx", "csv", "txt",
        "dwg", "dxf", "step", "stp", "igs", "zip",
    }

    def validate_file(self, f):
        from django.conf import settings

        if f.size > settings.FILE_UPLOAD_MAX_BYTES:
            raise serializers.ValidationError("File is larger than 15 MB.")
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in self.ALLOWED_EXTENSIONS:
            raise serializers.ValidationError("This file type is not allowed.")
        return f


class ChangePasswordIn(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField()


class ResetPasswordIn(serializers.Serializer):
    new_password = serializers.CharField()
