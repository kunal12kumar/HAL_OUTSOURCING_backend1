"""Document intelligence pipeline for vendor documents: read -> classify -> facts -> chunks -> embeddings."""
import hashlib
import logging
import os
import threading

from django.db import close_old_connections, transaction

from . import facts as F
from .models import DocStatus, DocumentChunk, VendorDocument
from .readers import SUPPORTED, UnreadableDocument, read_pages

logger = logging.getLogger(__name__)
MAX_BYTES = 15 * 1024 * 1024


def validate_upload(upload):
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in SUPPORTED:
        return f"{upload.name}: unsupported file type. Use PDF, DOCX, XLSX or CSV."
    if upload.size > MAX_BYTES:
        return f"{upload.name}: larger than {MAX_BYTES // (1024 * 1024)} MB."
    if upload.size == 0:
        return f"{upload.name}: the file is empty."
    return None


def sha256_of(upload):
    h = hashlib.sha256()
    for chunk in upload.chunks():
        h.update(chunk)
    upload.seek(0)
    return h.hexdigest()


def process_document(document_id):
    """Idempotent: safe to call again to reprocess. Failures are recorded on the document, never raised."""
    close_old_connections()
    doc = VendorDocument.objects.get(pk=document_id)
    doc.status, doc.status_detail = DocStatus.PROCESSING, ""
    doc.save(update_fields=["status", "status_detail"])
    try:
        pages = read_pages(doc.file.path, os.path.splitext(doc.filename)[1])
        text = "\n".join(p.text for p in pages)
        warnings = [f"{p.ref}: {w}" for p in pages for w in p.warnings]
        if not text.strip():
            raise UnreadableDocument("No readable text was found in this document. Requires manual verification.")
        doc_type = doc.doc_type if doc.doc_type_confirmed else F.classify(doc.filename, text)
        facts = F.extract_facts(pages)
        with transaction.atomic():
            doc.chunks.all().delete()
            DocumentChunk.objects.bulk_create(
                DocumentChunk(
                    document=doc, vendor_id=doc.vendor_id, index=i, page_ref=page.ref, page_number=page.number,
                    doc_type=doc_type, text=body, embedding=F.embed(body),
                )
                for i, (page, body) in enumerate(F.chunk_pages(pages))
            )
            doc.doc_type = doc_type
            doc.structured = {"pages": len(pages), "facts": facts, "warnings": warnings}
            doc.status = DocStatus.NEEDS_REVIEW if warnings else DocStatus.READY
            doc.status_detail = "; ".join(warnings)[:1000]
            doc.save()
    except UnreadableDocument as exc:
        _fail(doc, str(exc))
    except Exception as exc:
        logger.exception("document %s failed", document_id)
        _fail(doc, f"Processing failed ({exc.__class__.__name__}). Try reprocessing, or upload a clean copy.")
    return doc


def _fail(doc, message):
    doc.status, doc.status_detail = DocStatus.FAILED, message
    doc.save(update_fields=["status", "status_detail"])


def process_in_background(document_ids):
    def run():
        for pk in document_ids:
            try:
                process_document(pk)
            finally:
                close_old_connections()

    threading.Thread(target=run, daemon=True).start()
