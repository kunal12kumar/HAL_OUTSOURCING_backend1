from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Document, Inspection, POStatus, PurchaseOrder
from .permissions import IsVendorUser, VendorPasswordChanged
from .serializers import DocumentUploadIn
from .serializers_vendor import (
    AcknowledgeIn,
    DispatchIn,
    PdiRequestIn,
    ProgressIn,
    VendorOrderDetailSerializer,
    VendorOrderListSerializer,
)

VENDOR_PERMISSIONS = [IsVendorUser, VendorPasswordChanged]


def own_orders(request):
    """Every vendor query starts here. The vendor comes from the login, never from the request."""
    return PurchaseOrder.objects.filter(vendor_id=request.user.vendor_id)


class VendorOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = VENDOR_PERMISSIONS

    def get_queryset(self):
        return own_orders(self.request).select_related("requirement", "tender").prefetch_related(
            "material_issues", "updates", "inspections__consignment", "consignments", "documents__uploaded_by"
        )

    def get_serializer_class(self):
        return VendorOrderListSerializer if self.action == "list" else VendorOrderDetailSerializer

    def _fresh(self, po):
        return Response(VendorOrderDetailSerializer(self.get_queryset().get(pk=po.pk)).data)

    def _parse(self, cls):
        s = cls(data=self.request.data)
        s.is_valid(raise_exception=True)
        return s.validated_data

    @action(detail=True, methods=["post"])
    def progress(self, request, pk=None):
        po = self.get_object()
        d = self._parse(ProgressIn)
        services.post_progress(request.user, po, d["qty_completed"], d["percent_complete"], d["message"], d["expected_ready_date"])
        return self._fresh(po)

    @action(detail=True, methods=["post"], url_path="request-pdi")
    def request_pdi(self, request, pk=None):
        po = self.get_object()
        d = self._parse(PdiRequestIn)
        services.request_pdi(request.user, po, d["qty_offered"], d["note"])
        return self._fresh(po)

    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_goods(self, request, pk=None):
        po = self.get_object()
        d = self._parse(DispatchIn)
        services.dispatch_consignment(request.user, po, d["qty"], d["vehicle_no"], d["challan_no"])
        return self._fresh(po)

    @action(detail=True, methods=["post"], url_path=r"material/(?P<issue_id>\d+)/acknowledge")
    def acknowledge_material(self, request, pk=None, issue_id=None):
        po = self.get_object()
        issue = get_object_or_404(po.material_issues, pk=issue_id)
        d = self._parse(AcknowledgeIn)
        services.acknowledge_material(request.user, issue, d["quantity"], d["remarks"])
        return self._fresh(po)

    @action(detail=True, methods=["post"])
    def documents(self, request, pk=None):
        po = self.get_object()
        d = DocumentUploadIn(data=request.data)
        d.is_valid(raise_exception=True)
        v = d.validated_data
        Document.objects.create(
            title=v["title"],
            doc_type=v["doc_type"],
            file=v["file"],
            po=po,
            visible_to_vendor=True,
            uploaded_by=request.user,
        )
        services.audit(request.user, "document.upload", po, f"Vendor uploaded {v['title']}")
        return self._fresh(po)


class VendorDocumentDownloadView(APIView):
    permission_classes = VENDOR_PERMISSIONS

    def get(self, request, pk):
        doc = get_object_or_404(
            Document, pk=pk, visible_to_vendor=True, po__vendor_id=request.user.vendor_id
        )
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.file.name.rsplit("/", 1)[-1])


class VendorDashboardView(APIView):
    permission_classes = VENDOR_PERMISSIONS

    def get(self, request):
        vendor = request.user.vendor
        orders = list(own_orders(request).filter(status=POStatus.ACTIVE).select_related("requirement"))
        todo = []
        for po in orders:
            pending_ack = po.material_issues.filter(acknowledged_at__isnull=True).count()
            if pending_ack:
                todo.append({"order_id": po.id, "order_number": po.number, "kind": "material",
                             "text": f"{pending_ack} raw material issue(s) waiting for your acknowledgement"})
            last = po.last_update_at or po.placed_at
            if (timezone.now() - last).days >= settings.VENDOR_STALE_DAYS:
                todo.append({"order_id": po.id, "order_number": po.number, "kind": "update",
                             "text": f"No progress update for {(timezone.now() - last).days} days"})
            for insp in po.inspections.filter(status=Inspection.Status.SCHEDULED):
                todo.append({"order_id": po.id, "order_number": po.number, "kind": "inspection",
                             "text": f"{insp.get_kind_display()} scheduled for {insp.scheduled_date}"})
            for insp in po.inspections.filter(status=Inspection.Status.COMPLETED, qty_rejected__gt=0).order_by("-inspected_at")[:2]:
                todo.append({"order_id": po.id, "order_number": po.number, "kind": "deficiency",
                             "text": f"{insp.qty_rejected} item(s) rejected at {insp.get_kind_display().lower()}: {insp.deficiencies}"[:200]})
            if po.qty_dispatchable > 0:
                todo.append({"order_id": po.id, "order_number": po.number, "kind": "dispatch",
                             "text": f"{po.qty_dispatchable} item(s) cleared by inspection and ready to dispatch"})
        return Response(
            {
                "vendor": {"name": vendor.name, "code": vendor.code},
                "active_orders": len(orders),
                "overdue_orders": sum(1 for o in orders if o.is_overdue),
                "orders": VendorOrderListSerializer(orders, many=True).data,
                "todo": todo,
            }
        )
