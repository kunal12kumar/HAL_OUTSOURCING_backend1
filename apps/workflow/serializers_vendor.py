"""Serializers for the vendor portal.

Every field is listed explicitly. Nothing is exposed by default, so a new column
added to a model never leaks to vendors by accident. Estimates, other vendors'
quotations, committee trails and internal inspector notes are never included.
"""
from rest_framework import serializers

from .models import Consignment, Document, Inspection, MaterialIssue, ProgressUpdate, PurchaseOrder


class VendorDocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    uploaded_by_hal = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ["id", "title", "doc_type", "created_at", "download_url", "uploaded_by_hal"]

    def get_download_url(self, obj):
        return f"/api/vendor/documents/{obj.id}/download/"

    def get_uploaded_by_hal(self, obj):
        return not obj.uploaded_by.is_vendor


class VendorMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialIssue
        fields = ["id", "material", "specification", "quantity", "unit", "vehicle_no", "dispatch_doc_no",
                  "issued_at", "acknowledged_at", "acknowledged_qty", "ack_remarks"]


class VendorProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressUpdate
        fields = ["id", "qty_completed", "percent_complete", "message", "expected_ready_date", "created_at"]


class VendorInspectionSerializer(serializers.ModelSerializer):
    outcome = serializers.CharField(read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    consignment_number = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = ["id", "kind", "kind_label", "status", "status_label", "outcome", "qty_offered", "qty_accepted",
                  "qty_rejected", "requested_at", "scheduled_date", "inspected_at", "deficiencies", "consignment_number"]

    def get_consignment_number(self, obj):
        return obj.consignment.number if obj.consignment_id else None


class VendorConsignmentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    shortage = serializers.IntegerField(read_only=True)

    class Meta:
        model = Consignment
        fields = ["id", "number", "qty", "vehicle_no", "challan_no", "dispatched_at", "status", "status_label",
                  "qty_received", "received_at", "shortage", "qty_accepted", "qty_rejected",
                  "finishing_started_at", "finishing_completed_at"]


class VendorOrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    equipment_name = serializers.CharField(source="requirement.equipment_name", read_only=True)
    qty_accepted = serializers.IntegerField(read_only=True)
    qty_finished = serializers.IntegerField(read_only=True)
    qty_outstanding = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    last_update_at = serializers.DateTimeField(read_only=True)
    unacknowledged_material = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "po_ref", "equipment_name", "quantity", "value", "delivery_date", "status",
                  "status_label", "qty_accepted", "qty_finished", "qty_outstanding", "is_overdue", "last_update_at",
                  "unacknowledged_material"]

    def get_unacknowledged_material(self, obj):
        return obj.material_issues.filter(acknowledged_at__isnull=True).count()


class VendorOrderDetailSerializer(VendorOrderListSerializer):
    scope = serializers.CharField(source="tender.scope", read_only=True)
    terms = serializers.CharField(read_only=True)
    material_issues = VendorMaterialSerializer(many=True, read_only=True)
    updates = VendorProgressSerializer(many=True, read_only=True)
    inspections = VendorInspectionSerializer(many=True, read_only=True)
    consignments = VendorConsignmentSerializer(many=True, read_only=True)
    documents = serializers.SerializerMethodField()
    qty_pdi_accepted = serializers.IntegerField(read_only=True)
    qty_dispatchable = serializers.IntegerField(read_only=True)

    class Meta(VendorOrderListSerializer.Meta):
        fields = VendorOrderListSerializer.Meta.fields + [
            "scope", "terms", "material_issues", "updates", "inspections", "consignments", "documents",
            "qty_pdi_accepted", "qty_dispatchable",
        ]

    def get_documents(self, obj):
        docs = obj.documents.filter(visible_to_vendor=True).select_related("uploaded_by")
        return VendorDocumentSerializer(docs, many=True).data


class ProgressIn(serializers.Serializer):
    qty_completed = serializers.IntegerField(min_value=0)
    percent_complete = serializers.IntegerField(min_value=0, max_value=100)
    message = serializers.CharField(required=False, allow_blank=True, default="")
    expected_ready_date = serializers.DateField(required=False, allow_null=True, default=None)


class PdiRequestIn(serializers.Serializer):
    qty_offered = serializers.IntegerField(min_value=1)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class DispatchIn(serializers.Serializer):
    qty = serializers.IntegerField(min_value=1)
    vehicle_no = serializers.CharField()
    challan_no = serializers.CharField()


class AcknowledgeIn(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
