"""prepare_vendor_candidates: hybrid retrieval of the evidence each evaluator needs.

Structured data (extracted facts, document metadata) is filtered with the ORM; semantic search runs over the
embedded chunks, restricted by vendor and document type. Absence is kept distinct from silence and from refusal:
vendors with no documents of a kind are reported as such, and downstream nodes mark missing / negative separately.
"""
from . import facts as F
from .models import DocStatus, DocumentChunk, VendorDocument
from .technical import TECH_DOCS

USABLE = (DocStatus.READY, DocStatus.NEEDS_REVIEW)


def prepare(requirements, vendor_ids=None, top_k=3):
    docs = VendorDocument.objects.filter(status__in=USABLE).select_related("vendor")
    if vendor_ids:
        docs = docs.filter(vendor_id__in=vendor_ids)
    by_vendor = {}
    for d in docs:
        by_vendor.setdefault(d.vendor_id, {"vendor_id": d.vendor_id, "vendor_name": d.vendor.name, "vendor_code": d.vendor.code, "documents": []})
        by_vendor[d.vendor_id]["documents"].append({
            "document_id": d.id, "filename": d.filename, "doc_type": d.doc_type, "uploaded_at": d.created_at.isoformat(),
            "warnings": d.structured.get("warnings", []),
            "facts": [dict(f, filename=d.filename, document_id=d.id) for f in d.structured.get("facts", [])],
        })
    reqs = requirements.get("requirements", [])
    tech_reqs = [r for r in reqs if r["category"] == "technical"]
    for v in by_vendor.values():
        v["tech_chunks"] = _retrieve(v["vendor_id"], tech_reqs, TECH_DOCS, top_k)
        v["duplicate_notes"] = _duplicates(v["documents"])
    return list(by_vendor.values())


def _retrieve(vendor_id, reqs, doc_types, top_k):
    """Union of the top chunks per requirement (semantic) plus chunks containing a requirement's exact code."""
    chunks = list(DocumentChunk.objects.filter(vendor_id=vendor_id, doc_type__in=doc_types).select_related("document"))
    picked = {}
    for r in reqs:
        q = F.embed(f"{r['label']} {r['raw']}")
        ranked = sorted(chunks, key=lambda c: F.cosine(q, c.embedding), reverse=True)[:top_k]
        for c in ranked:
            picked[c.id] = c
        token = r["raw"].lower()
        for c in chunks:
            if token and token in c.text.lower():
                picked[c.id] = c
    return [{"chunk_id": c.id, "document_id": c.document_id, "filename": c.document.filename, "doc_type": c.doc_type,
             "page": c.page_number, "page_ref": c.page_ref, "text": c.text} for c in picked.values()]


def _duplicates(documents):
    seen, notes = {}, []
    for d in documents:
        key = (d["doc_type"], d["filename"].lower())
        if key in seen:
            notes.append(f"{d['filename']} appears more than once; the latest upload is used where facts conflict.")
        seen[key] = d
    return notes
