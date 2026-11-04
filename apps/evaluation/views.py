import os

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.vendors.models import Vendor
from apps.workflow.permissions import IsInternal, roles_permission

from . import facts, ingest, runner, scoring, tender
from .models import AnalysisStatus, DocStatus, TenderAnalysis, VendorDocument
from .readers import SUPPORTED, UnreadableDocument

# Demo module: HAL Administrators and the Outsourcing Department only.
CanEvaluate = roles_permission(Role.ADMIN, Role.OUTSOURCING)


class Base(APIView):
    permission_classes = [IsInternal, CanEvaluate]


def doc_out(d, detail=False):
    out = {
        "id": d.id, "vendor_id": d.vendor_id, "vendor_name": d.vendor.name, "filename": d.filename, "doc_type": d.doc_type,
        "doc_type_label": facts.DOC_TYPE_LABELS.get(d.doc_type, d.doc_type), "status": d.status, "status_label": d.get_status_display(),
        "status_detail": d.status_detail, "size": d.size, "uploaded_at": d.created_at, "pages": d.structured.get("pages"),
        "fact_count": len(d.structured.get("facts", [])),
    }
    if detail:
        out.update(facts=d.structured.get("facts", []), warnings=d.structured.get("warnings", []),
                   chunks=[{"index": c.index, "page_ref": c.page_ref, "text": c.text[:400]} for c in d.chunks.all()[:50]])
    return out


class VendorDocumentsView(Base):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        docs = VendorDocument.objects.select_related("vendor")
        vendors = {}
        for v in Vendor.objects.filter(eval_documents__isnull=False).distinct():
            vendors[v.id] = {"id": v.id, "name": v.name, "code": v.code, "documents": []}
        for d in docs:
            vendors[d.vendor_id]["documents"].append(doc_out(d))
        return Response({"vendors": sorted(vendors.values(), key=lambda v: v["name"].lower()), "doc_types": facts.DOC_TYPE_LABELS,
                         "supported": sorted(SUPPORTED)})

    def post(self, request):
        name = (request.data.get("vendor_name") or "").strip()
        if not name:
            return Response({"vendor_name": "Enter or choose a vendor name."}, status=400)
        files = request.FILES.getlist("files")
        if not files:
            return Response({"files": "Choose at least one file."}, status=400)
        forced_type = request.data.get("doc_type") or ""
        if forced_type and forced_type not in facts.DOC_TYPE_LABELS:
            return Response({"doc_type": "Unknown document type."}, status=400)
        vendor = Vendor.objects.filter(name__iexact=name).first() or Vendor.objects.create(name=name)
        created, rejected = [], []
        for f in files:
            problem = ingest.validate_upload(f)
            if problem:
                rejected.append(problem)
                continue
            digest = ingest.sha256_of(f)
            if VendorDocument.objects.filter(vendor=vendor, sha256=digest).exists():
                rejected.append(f"{f.name}: identical file already uploaded for {vendor.name}.")
                continue
            created.append(VendorDocument.objects.create(
                vendor=vendor, file=f, filename=os.path.basename(f.name)[:255], sha256=digest, size=f.size,
                doc_type=forced_type or "OTHER", doc_type_confirmed=bool(forced_type), uploaded_by=request.user))
        ingest.process_in_background([d.id for d in created])
        code = status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST
        return Response({"vendor": {"id": vendor.id, "name": vendor.name}, "created": [doc_out(d) for d in created], "rejected": rejected}, status=code)


class VendorDocumentDetailView(Base):
    def get(self, request, pk):
        return Response(doc_out(get_object_or_404(VendorDocument.objects.select_related("vendor"), pk=pk), detail=True))

    def patch(self, request, pk):
        d = get_object_or_404(VendorDocument, pk=pk)
        t = request.data.get("doc_type")
        if t not in facts.DOC_TYPE_LABELS:
            return Response({"doc_type": "Unknown document type."}, status=400)
        d.doc_type, d.doc_type_confirmed = t, True
        d.save(update_fields=["doc_type", "doc_type_confirmed"])
        ingest.process_in_background([d.id])
        return Response(doc_out(d))

    def delete(self, request, pk):
        get_object_or_404(VendorDocument, pk=pk).delete()
        return Response(status=204)


class VendorDocumentReprocessView(Base):
    def post(self, request, pk):
        d = get_object_or_404(VendorDocument, pk=pk)
        ingest.process_in_background([d.id])
        return Response(doc_out(d))


class VendorDocumentDownloadView(Base):
    def get(self, request, pk):
        d = get_object_or_404(VendorDocument, pk=pk)
        return FileResponse(d.file.open("rb"), as_attachment=True, filename=d.filename)


def analysis_out(a, full=False):
    out = {
        "id": a.id, "evaluation_id": str(a.evaluation_id), "title": a.title, "filename": a.filename, "status": a.status,
        "status_label": a.get_status_display(), "created_at": a.created_at, "finished_at": a.finished_at, "error": a.error,
        "progress": a.progress, "requirement_count": len(a.requirements.get("requirements", [])),
    }
    if full:
        out.update(requirements=a.requirements, config=a.config, result=a.result, default_config=scoring.DEFAULT_CONFIG)
    return out


class AnalysesView(Base):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        return Response({"results": [analysis_out(a) for a in TenderAnalysis.objects.all()[:50]]})

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"file": "Choose the tender document."}, status=400)
        problem = ingest.validate_upload(f)
        if problem:
            return Response({"file": problem}, status=400)
        a = TenderAnalysis.objects.create(tender_file=f, filename=os.path.basename(f.name)[:255],
                                          title=(request.data.get("title") or os.path.splitext(f.name)[0])[:200], created_by=request.user)
        try:
            a.requirements = tender.extract_requirements(a.tender_file.path, os.path.splitext(a.filename)[1])
        except UnreadableDocument as exc:
            a.delete()
            return Response({"file": str(exc)}, status=400)
        a.save(update_fields=["requirements"])
        return Response(analysis_out(a, full=True), status=201)


class AnalysisDetailView(Base):
    def get(self, request, pk):
        return Response(analysis_out(get_object_or_404(TenderAnalysis, evaluation_id=pk), full=True))


class AnalysisStartView(Base):
    def post(self, request, pk):
        a = get_object_or_404(TenderAnalysis, evaluation_id=pk)
        if a.status == AnalysisStatus.RUNNING:
            return Response({"detail": "This analysis is already running."}, status=409)
        if not a.requirements.get("requirements"):
            return Response({"detail": "No requirements were extracted from this tender, so there is nothing to evaluate."}, status=400)
        cfg = {}
        for k in ("technical_weight", "commercial_weight"):
            if request.data.get(k) not in (None, ""):
                cfg[k] = request.data[k]
        try:
            scoring.build_config(cfg)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        overrides = request.data.get("requirements")  # reviewer-confirmed mandatory flags: {id: true|false|null}
        if isinstance(overrides, dict):
            for r in a.requirements["requirements"]:
                if r["id"] in overrides and overrides[r["id"]] in (True, False, None):
                    r["mandatory"] = overrides[r["id"]]
            a.requirements["mandatory_identified"] = any(r["mandatory"] for r in a.requirements["requirements"])
        a.config, a.progress, a.result, a.error = cfg, {}, {}, ""
        a.status = AnalysisStatus.RUNNING
        a.save()
        runner.start_in_background(a.pk)
        return Response(analysis_out(a), status=202)
