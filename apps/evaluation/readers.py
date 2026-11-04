"""Turn an uploaded file into pages of text. Each page keeps a reference (page number or sheet) for citations."""
import csv
import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx", ".xlsx", ".csv"}


class UnreadableDocument(Exception):
    pass


@dataclass
class Page:
    ref: str            # "Page 3", "Sheet Commercial", "Section Scope"
    number: int | None  # page number when the format has pages
    text: str
    warnings: list = field(default_factory=list)


def read_pages(path, ext):
    ext = ext.lower()
    try:
        if ext == ".pdf":
            return _pdf(path)
        if ext == ".docx":
            return _docx(path)
        if ext == ".xlsx":
            return _xlsx(path)
        if ext == ".csv":
            return _csv(path)
    except UnreadableDocument:
        raise
    except Exception as exc:  # corrupted or encrypted file
        logger.warning("could not read %s: %s", path, exc)
        raise UnreadableDocument(f"The file could not be read ({exc.__class__.__name__}). It may be corrupted or password protected.")
    raise UnreadableDocument(f"Unsupported file type {ext}.")


def _pdf(path):
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise UnreadableDocument("The PDF is password protected.")
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        text = (p.extract_text() or "").strip()
        warnings = []
        if len(text) < 20:
            text, warnings = _ocr_pdf_page(path, i)
        pages.append(Page(f"Page {i}", i, text, warnings))
    return pages


def _ocr_pdf_page(path, number):
    """OCR is optional. Without Tesseract a scanned page is flagged, never silently treated as empty."""
    try:
        import pytesseract  # noqa: F401
        from pdf2image import convert_from_path

        image = convert_from_path(path, first_page=number, last_page=number)[0]
        text = pytesseract.image_to_string(image).strip()
        if text:
            return text, ["Text recovered by OCR; accuracy not guaranteed. Requires manual verification."]
    except Exception:
        pass
    return "", ["No readable text (scanned page?) and OCR is not available. Requires manual verification."]


def _docx(path):
    import docx

    d = docx.Document(path)
    sections, current, lines = [], "Document", []

    def flush():
        if lines:
            sections.append(Page(f"Section: {current}", None, "\n".join(lines)))

    body = d.element.body
    paragraphs = {p._p: p for p in d.paragraphs}
    tables = {t._tbl: t for t in d.tables}
    for child in body.iterchildren():
        if child in paragraphs:
            p = paragraphs[child]
            text = p.text.strip()
            if not text:
                continue
            if p.style is not None and p.style.name.lower().startswith(("heading", "title")):
                flush()
                current, lines = text, []
            else:
                lines.append(text)
        elif child in tables:
            for row in tables[child].rows:
                cells = [c.text.strip() for c in row.cells]
                # merged cells repeat; drop consecutive duplicates
                dedup = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
                if any(dedup):
                    lines.append(" | ".join(dedup))
    flush()
    return sections or [Page("Document", None, "")]


def _xlsx(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    pages = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                lines.append(" | ".join(cells).rstrip(" |"))
        pages.append(Page(f"Sheet {ws.title}", None, "\n".join(lines)))
    return pages or [Page("Sheet", None, "")]


def _csv(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnreadableDocument("The CSV file uses an unsupported text encoding.")
    lines = []
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if any(cells):
            lines.append(" | ".join(cells).rstrip(" |"))
    return [Page("CSV", None, "\n".join(lines))]
