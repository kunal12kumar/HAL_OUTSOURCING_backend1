import csv

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role, User
from apps.vendors.models import Vendor

from . import services
from .actions import actions_for, queue_states
from .models import (
    AuditLog,
    Consignment,
    Document,
    Inspection,
    POStatus,
    ProgressUpdate,
    PurchaseOrder,
    Requirement,
    RequirementStage,
    Tender,
)
from .permissions import IsAdmin, IsInternal, roles_permission
from .serializers import (
    AdviceIn,
    AllocateIn,
    AuditSerializer,
    AwardIn,
    CompleteInspectionIn,
    ConsignmentSerializer,
    CreateTenderIn,
    DecisionIn,
    DeliveryDateIn,
    DocumentSerializer,
    DocumentUploadIn,
    EstimateIn,
    EvaluateIn,
    GateReceiptIn,
    InspectionSerializer,
    IssueMaterialIn,
    OrderDetailSerializer,
    OrderListSerializer,
    PncIn,
    PublishIn,
    QuotationIn,
    RemarksIn,
    RequirementDetailSerializer,
    RequirementListSerializer,
    RequirementWriteSerializer,
    ResetPasswordIn,
    ScheduleIn,
    SubmitIn,
    TenderDetailSerializer,
    TenderListSerializer,
    UserCreateSerializer,
    UserSerializer,
    VendorSerializer,
)


class InternalViewSet(viewsets.GenericViewSet):
    permission_classes = [IsInternal]
    detail_serializer = None

    def refreshed(self, obj, serializer=None):
        cls = serializer or self.detail_serializer
        fresh = self.get_queryset().get(pk=obj.pk)
        return Response(cls(fresh, context=self.get_serializer_context()).data)

    def parse(self, serializer_cls):
        s = serializer_cls(data=self.request.data)
        s.is_valid(raise_exception=True)
        return s.validated_data


# ---------------------------------------------------------------- requirements

class RequirementViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, InternalViewSet
):
    http_method_names = ["get", "post", "patch", "head", "options"]
    detail_serializer = RequirementDetailSerializer

    def get_queryset(self):
        qs = Requirement.objects.select_related("raised_by", "allocation").prefetch_related("trail", "documents", "tenders")
        p = self.request.query_params
        if p.get("stage"):
            qs = qs.filter(stage=p["stage"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(title__icontains=p["q"]) | Q(equipment_name__icontains=p["q"]))
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return RequirementListSerializer
        if self.action in ("create", "partial_update", "update"):
            return RequirementWriteSerializer
        return RequirementDetailSerializer

    def create(self, request, *args, **kwargs):
        services.require_role(request.user, Role.MSR)
        s = RequirementWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        req = s.save(raised_by=request.user)
        services.audit(request.user, "requirement.create", req, f"Raised requirement {req.number}")
        return Response(RequirementDetailSerializer(req, context=self.get_serializer_context()).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        req = self.get_object()
        services.require_role(request.user, Role.MSR)
        services.require_state(req.stage, (RequirementStage.DRAFT, RequirementStage.RETURNED), "requirement")
        s = RequirementWriteSerializer(req, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        services.audit(request.user, "requirement.edit", req, f"Edited {req.number}")
        return self.refreshed(req)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        req = self.get_object()
        d = self.parse(SubmitIn)
        services.submit_requirement(request.user, req, d["ifs_ref"], d["efile_ref"])
        return self.refreshed(req)

    @action(detail=True, methods=["post"], url_path="committee-decision")
    def committee_decision(self, request, pk=None):
        req = self.get_object()
        d = self.parse(DecisionIn)
        services.committee_decision(request.user, req, d["approve"], d["remarks"])
        return self.refreshed(req)

    @action(detail=True, methods=["post"], url_path="methods-advice")
    def methods_advice(self, request, pk=None):
        req = self.get_object()
        services.methods_advice(request.user, req, self.parse(AdviceIn)["text"])
        return self.refreshed(req)

    @action(detail=True, methods=["post"], url_path="imm-assessment")
    def imm_assessment(self, request, pk=None):
        req = self.get_object()
        services.imm_assessment(request.user, req, self.parse(AdviceIn)["text"])
        return self.refreshed(req)

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        req = self.get_object()
        d = self.parse(AllocateIn)
        services.allocate(
            request.user, req, d["in_house_qty"], d["other_division_qty"], d["other_division_name"], d["outsource_qty"], d["remarks"]
        )
        return self.refreshed(req)

    @action(detail=True, methods=["post"], url_path="create-tender")
    def create_tender(self, request, pk=None):
        req = self.get_object()
        tender = services.create_tender(request.user, req, self.parse(CreateTenderIn)["scope"])
        return Response(
            TenderDetailSerializer(tender, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def documents(self, request, pk=None):
        req = self.get_object()
        return upload_document(request, requirement=req)


# --------------------------------------------------------------------- tenders

class TenderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, InternalViewSet):
    detail_serializer = TenderDetailSerializer

    def get_queryset(self):
        qs = Tender.objects.select_related("requirement", "parent").prefetch_related(
            "quotations__vendor", "quotations__tender", "trail", "documents", "orders"
        )
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(requirement__title__icontains=p["q"]) | Q(requirement__number__icontains=p["q"]))
        return qs

    def get_serializer_class(self):
        return TenderListSerializer if self.action == "list" else TenderDetailSerializer

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        t = self.get_object()
        services.submit_tender(request.user, t, self.parse(SubmitIn)["efile_ref"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="committee-decision")
    def committee_decision(self, request, pk=None):
        t = self.get_object()
        d = self.parse(DecisionIn)
        services.tender_approval(request.user, t, d["approve"], d["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="set-estimate")
    def set_estimate(self, request, pk=None):
        t = self.get_object()
        d = self.parse(EstimateIn)
        services.set_estimate(request.user, t, d["amount"], d["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="review-estimate")
    def review_estimate(self, request, pk=None):
        t = self.get_object()
        d = self.parse(DecisionIn)
        services.review_estimate(request.user, t, d["approve"], d["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        t = self.get_object()
        d = self.parse(PublishIn)
        services.publish_tender(request.user, t, d["gem_ref"], d["eportal_ref"], d["closing_date"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"])
    def quotations(self, request, pk=None):
        t = self.get_object()
        d = self.parse(QuotationIn)
        services.add_quotation(request.user, t, d["vendor"], d["price"], d["delivery_days"], d["source"], d["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="evaluate-quotation")
    def evaluate_quotation(self, request, pk=None):
        t = self.get_object()
        d = self.parse(EvaluateIn)
        q = get_object_or_404(t.quotations, pk=d["quotation"])
        services.evaluate_technical(request.user, q, d["qualified"], d["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="start-evaluation")
    def start_evaluation(self, request, pk=None):
        t = self.get_object()
        services.start_evaluation(request.user, t)
        return self.refreshed(t)

    @action(detail=True, methods=["post"])
    def award(self, request, pk=None):
        t = self.get_object()
        d = self.parse(AwardIn)
        q = get_object_or_404(t.quotations, pk=d["quotation"])
        po = services.award(request.user, t, q, d["delivery_date"], d["po_ref"], d["terms"])
        return Response(OrderDetailSerializer(po, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"], url_path="refer-to-pnc")
    def refer_to_pnc(self, request, pk=None):
        t = self.get_object()
        services.refer_to_pnc(request.user, t, self.parse(RemarksIn)["remarks"])
        return self.refreshed(t)

    @action(detail=True, methods=["post"], url_path="pnc-decision")
    def pnc_decision(self, request, pk=None):
        t = self.get_object()
        d = self.parse(PncIn)
        new = services.pnc_decision(request.user, t, d["revised_estimate"], d["remarks"])
        return self.refreshed(new)

    @action(detail=True, methods=["post"])
    def documents(self, request, pk=None):
        return upload_document(request, tender=self.get_object())


# --------------------------------------------------------------------- vendors

class VendorViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, InternalViewSet
):
    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = VendorSerializer
    detail_serializer = VendorSerializer

    def get_queryset(self):
        qs = Vendor.objects.all()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        return qs

    def _write_check(self):
        services.require_role(self.request.user, Role.OUTSOURCING, Role.ADMIN)

    def create(self, request, *args, **kwargs):
        self._write_check()
        s = VendorSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        vendor = s.save()
        services.audit(request.user, "vendor.create", vendor, f"Registered vendor {vendor.name}")
        return Response(VendorSerializer(vendor).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        self._write_check()
        vendor = self.get_object()
        s = VendorSerializer(vendor, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        services.audit(request.user, "vendor.edit", vendor, f"Edited vendor {vendor.name}")
        return Response(VendorSerializer(vendor).data)

    @action(detail=True, methods=["post"], url_path="issue-credentials")
    def issue_credentials(self, request, pk=None):
        vendor = self.get_object()
        username, password = services.issue_credentials(request.user, vendor)
        vendor.refresh_from_db()
        return Response(
            {
                "vendor": VendorSerializer(vendor).data,
                "username": username,
                "temporary_password": password,
                "note": "Shown once. Hand it to the vendor securely. They must change it at first login.",
            }
        )

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        vendor = self.get_object()
        services.suspend_vendor(request.user, vendor)
        vendor.refresh_from_db()
        return Response(VendorSerializer(vendor).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        vendor = self.get_object()
        services.reactivate_vendor(request.user, vendor)
        vendor.refresh_from_db()
        return Response(VendorSerializer(vendor).data)


# ---------------------------------------------------------------------- orders

class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, InternalViewSet):
    detail_serializer = OrderDetailSerializer

    def get_queryset(self):
        qs = PurchaseOrder.objects.select_related("vendor", "requirement", "tender").prefetch_related(
            "material_issues", "updates", "inspections__consignment", "inspections__inspector", "inspections__po__vendor",
            "consignments__po__vendor", "documents__uploaded_by",
        )
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("vendor"):
            qs = qs.filter(vendor_id=p["vendor"])
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(vendor__name__icontains=p["q"]) | Q(requirement__equipment_name__icontains=p["q"]))
        return qs

    def get_serializer_class(self):
        return OrderListSerializer if self.action == "list" else OrderDetailSerializer

    @action(detail=True, methods=["post"], url_path="issue-material")
    def issue_material(self, request, pk=None):
        po = self.get_object()
        d = self.parse(IssueMaterialIn)
        services.issue_material(
            request.user, po, d["material"], d["quantity"], d["unit"], d["specification"], d["vehicle_no"], d["dispatch_doc_no"], d["remarks"]
        )
        return self.refreshed(po)

    @action(detail=True, methods=["post"], url_path="revise-delivery-date")
    def revise_delivery_date(self, request, pk=None):
        po = self.get_object()
        d = self.parse(DeliveryDateIn)
        services.revise_delivery_date(request.user, po, d["delivery_date"], d["remarks"])
        return self.refreshed(po)

    @action(detail=True, methods=["post"])
    def documents(self, request, pk=None):
        po = self.get_object()
        return upload_document(request, po=po)


# ----------------------------------------------- inspections and consignments

class InspectionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, InternalViewSet):
    serializer_class = InspectionSerializer
    detail_serializer = InspectionSerializer

    def get_queryset(self):
        qs = Inspection.objects.select_related("po__vendor", "consignment", "inspector")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("po"):
            qs = qs.filter(po_id=p["po"])
        return qs

    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        insp = self.get_object()
        services.schedule_inspection(request.user, insp, self.parse(ScheduleIn)["date"])
        return self.refreshed(insp)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        insp = self.get_object()
        d = self.parse(CompleteInspectionIn)
        services.complete_inspection(
            request.user, insp, d["qty_accepted"], d["qty_rejected"], d["report"], d["deficiencies"], d["internal_notes"]
        )
        return self.refreshed(insp)


class ConsignmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, InternalViewSet):
    serializer_class = ConsignmentSerializer
    detail_serializer = ConsignmentSerializer

    def get_queryset(self):
        qs = Consignment.objects.select_related("po__vendor")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("po"):
            qs = qs.filter(po_id=p["po"])
        return qs

    @action(detail=True, methods=["post"], url_path="gate-receipt")
    def gate_receipt(self, request, pk=None):
        c = self.get_object()
        d = self.parse(GateReceiptIn)
        services.gate_receipt(request.user, c, d["qty_received"], d["remarks"])
        return self.refreshed(c)

    @action(detail=True, methods=["post"], url_path="start-finishing")
    def start_finishing(self, request, pk=None):
        c = self.get_object()
        services.start_finishing(request.user, c)
        return self.refreshed(c)

    @action(detail=True, methods=["post"], url_path="complete-finishing")
    def complete_finishing(self, request, pk=None):
        c = self.get_object()
        services.complete_finishing(request.user, c, self.parse(RemarksIn)["remarks"])
        return self.refreshed(c)


# ---------------------------------------------------------- admin and read-only

class AuditViewSet(mixins.ListModelMixin, InternalViewSet):
    permission_classes = [IsInternal, roles_permission(Role.ADMIN, Role.OUTSOURCING)]
    serializer_class = AuditSerializer

    def get_queryset(self):
        qs = AuditLog.objects.all()
        p = self.request.query_params
        if p.get("object_id"):
            qs = qs.filter(object_id=p["object_id"])
        if p.get("q"):
            qs = qs.filter(Q(summary__icontains=p["q"]) | Q(actor_name__icontains=p["q"]) | Q(action__icontains=p["q"]))
        return qs


class UserViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, InternalViewSet
):
    permission_classes = [IsInternal, IsAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = User.objects.filter(vendor__isnull=True).order_by("username")

    def get_serializer_class(self):
        return UserCreateSerializer if self.action == "create" else UserSerializer

    def create(self, request, *args, **kwargs):
        s = UserCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        services.audit(request.user, "user.create", user, f"Created {user.username} ({user.role})")
        return Response(UserSerializer(user).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        allowed = {k: v for k, v in request.data.items() if k in ("first_name", "last_name", "email", "role", "employee_id", "is_active")}
        if allowed.get("role") == Role.VENDOR:
            return Response({"detail": "Vendor logins are managed from the vendor record."}, status=400)
        if user.pk == request.user.pk and allowed.get("is_active") is False:
            return Response({"detail": "You cannot deactivate your own account."}, status=400)
        s = UserSerializer(user, data=allowed, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        services.audit(request.user, "user.edit", user, f"Edited {user.username}")
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjErr

        user = self.get_object()
        d = ResetPasswordIn(data=request.data)
        d.is_valid(raise_exception=True)
        try:
            validate_password(d.validated_data["new_password"], user)
        except DjErr as exc:
            return Response({"detail": " ".join(exc.messages)}, status=400)
        user.set_password(d.validated_data["new_password"])
        user.must_change_password = True
        user.save()
        services.audit(request.user, "user.reset_password", user, f"Password reset for {user.username}")
        return Response({"detail": "Password reset. The user must change it at next login."})


# ------------------------------------------------------- queue, dashboard, feed

class QueueView(APIView):
    permission_classes = [IsInternal]

    def get(self, request):
        role = request.user.role
        items = []
        for r in Requirement.objects.filter(stage__in=queue_states("requirement", role)).select_related("raised_by"):
            items.append({"kind": "requirement", "id": r.id, "number": r.number, "title": r.title,
                          "state": r.stage, "state_label": r.get_stage_display(), "updated_at": r.updated_at,
                          "actions": actions_for("requirement", r.stage, role)})
        for t in Tender.objects.filter(status__in=queue_states("tender", role)).select_related("requirement"):
            items.append({"kind": "tender", "id": t.id, "number": t.number, "title": t.requirement.title,
                          "state": t.status, "state_label": t.get_status_display(), "updated_at": t.created_at,
                          "actions": actions_for("tender", t.status, role)})
        for i in Inspection.objects.filter(status__in=queue_states("inspection", role)).select_related("po", "po__vendor"):
            items.append({"kind": "inspection", "id": i.id, "number": i.po.number,
                          "title": f"{i.get_kind_display()} - {i.po.vendor.name} - {i.qty_offered} items",
                          "state": i.status, "state_label": i.get_status_display(), "updated_at": i.requested_at,
                          "actions": actions_for("inspection", i.status, role)})
        for c in Consignment.objects.filter(status__in=queue_states("consignment", role)).select_related("po__vendor"):
            items.append({"kind": "consignment", "id": c.id, "number": c.number,
                          "title": f"{c.po.vendor.name} - {c.qty} items", "state": c.status,
                          "state_label": c.get_status_display(), "updated_at": c.dispatched_at,
                          "actions": actions_for("consignment", c.status, role)})
        items.sort(key=lambda x: x["updated_at"])
        return Response({"role": role, "count": len(items), "items": items})


class DashboardView(APIView):
    permission_classes = [IsInternal]

    def get(self, request):
        ctx = {"request": request}
        active = list(
            PurchaseOrder.objects.filter(status=POStatus.ACTIVE).select_related("vendor", "requirement")
        )
        overdue = [o for o in active if o.is_overdue]
        silent = [o for o in active if o.is_silent]
        req_counts = {}
        for r in Requirement.objects.values_list("stage", flat=True):
            req_counts[r] = req_counts.get(r, 0) + 1
        tender_counts = {}
        for s in Tender.objects.values_list("status", flat=True):
            tender_counts[s] = tender_counts.get(s, 0) + 1
        updates = ProgressUpdate.objects.select_related("po__vendor", "po__requirement")[:10]
        return Response(
            {
                "requirements_by_stage": req_counts,
                "tenders_by_status": tender_counts,
                "active_orders": len(active),
                "overdue_orders": OrderListSerializer(overdue, many=True, context=ctx).data,
                "silent_orders": OrderListSerializer(silent, many=True, context=ctx).data,
                "pdi_waiting": Inspection.objects.filter(kind=Inspection.Kind.PDI).exclude(status=Inspection.Status.COMPLETED).count(),
                "hal_inspections_waiting": Inspection.objects.filter(kind=Inspection.Kind.HAL).exclude(status=Inspection.Status.COMPLETED).count(),
                "consignments_in_transit": Consignment.objects.filter(status=Consignment.Status.DISPATCHED).count(),
                "silent_days_threshold": settings.VENDOR_STALE_DAYS,
                "recent_updates": [
                    {
                        "id": u.id,
                        "po_id": u.po_id,
                        "po_number": u.po.number,
                        "vendor_name": u.po.vendor.name,
                        "equipment_name": u.po.requirement.equipment_name,
                        "qty_completed": u.qty_completed,
                        "percent_complete": u.percent_complete,
                        "message": u.message,
                        "expected_ready_date": u.expected_ready_date,
                        "created_at": u.created_at,
                    }
                    for u in updates
                ],
            }
        )


# ------------------------------------------------------------ documents, export

def upload_document(request, requirement=None, tender=None, po=None):
    user = request.user
    if po is not None:
        services.require_role(user, Role.OUTSOURCING, Role.INSPECTOR)
    elif tender is not None:
        services.require_role(user, Role.OUTSOURCING, Role.MSD)
    d = DocumentUploadIn(data=request.data)
    d.is_valid(raise_exception=True)
    v = d.validated_data
    if v["visible_to_vendor"] and po is None:
        services.fail("Only documents attached to an order can be shared with a vendor.")
    doc = Document.objects.create(
        title=v["title"],
        doc_type=v["doc_type"],
        file=v["file"],
        requirement=requirement,
        tender=tender,
        po=po,
        visible_to_vendor=v["visible_to_vendor"],
        uploaded_by=user,
    )
    services.audit(user, "document.upload", po or tender or requirement, f"Uploaded {doc.title}")
    return Response(DocumentSerializer(doc).data, status=201)


class DocumentDownloadView(APIView):
    permission_classes = [IsInternal]

    def get(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.file.name.rsplit("/", 1)[-1])


def _safe(value):
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


EXPORTS = {
    "requirements": (
        Requirement.objects.select_related("raised_by"),
        ["number", "title", "equipment_name", "quantity", "unit", "stage", "ifs_ref", "efile_ref", "required_by", "created_at"],
    ),
    "tenders": (
        Tender.objects.select_related("requirement"),
        ["number", "round", "requirement.number", "quantity", "estimated_price", "status", "gem_ref", "eportal_ref", "closing_date"],
    ),
    "orders": (
        PurchaseOrder.objects.select_related("vendor", "requirement"),
        ["number", "po_ref", "vendor.name", "requirement.number", "quantity", "value", "delivery_date", "status",
         "qty_accepted", "qty_finished", "qty_outstanding"],
    ),
    "consignments": (
        Consignment.objects.select_related("po__vendor"),
        ["number", "po.vendor.name", "qty", "vehicle_no", "challan_no", "status", "qty_received", "qty_accepted", "qty_rejected"],
    ),
}


def _dig(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return ""
    return obj


class ExportView(APIView):
    permission_classes = [IsInternal]

    def get(self, request, name):
        if name not in EXPORTS:
            return Response({"detail": "Unknown register."}, status=404)
        qs, columns = EXPORTS[name]
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{name}-{timezone.localdate()}.csv"'
        w = csv.writer(response)
        w.writerow([c.replace(".", " ") for c in columns])
        for row in qs:
            w.writerow([_safe(_dig(row, c)) for c in columns])
        services.audit(request.user, "export", request.user, f"Exported {name}")
        return response
