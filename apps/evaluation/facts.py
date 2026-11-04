"""Document classification, "Label: value" fact extraction, chunking and embeddings (all deterministic)."""
import math
import re
from collections import Counter

from .units import normalise, slug

DOC_TYPES = {
    "TECHNICAL_QUOTATION": ["technical quotation", "technical offer", "technical bid", "technical proposal", "technical compliance"],
    "COMMERCIAL_QUOTATION": ["commercial quotation", "commercial offer", "price bid", "price schedule", "commercial bid", "quotation", "unit price", "payment terms"],
    "DATASHEET": ["datasheet", "data sheet", "product data", "catalogue", "catalog"],
    "SPECIFICATION": ["specification", "technical specification", "drawing"],
    "CERTIFICATE": ["certificate", "certification", "accreditation", "iso", "as9100", "nadcap"],
    "OTHER": [],
}
DOC_TYPE_LABELS = {
    "TECHNICAL_QUOTATION": "Technical quotation", "COMMERCIAL_QUOTATION": "Commercial quotation",
    "DATASHEET": "Product datasheet", "SPECIFICATION": "Technical specification",
    "CERTIFICATE": "Certificate / supporting", "OTHER": "Other",
}


def classify(filename, text):
    """Filename first (users name files sensibly), then content keywords. Returns (type, confidence note)."""
    name = normalise(filename)
    for t, words in DOC_TYPES.items():
        if any(normalise(w) in name for w in words if w not in ("iso", "quotation")):
            return t
    scores = Counter()
    low = normalise(text[:6000])
    for t, words in DOC_TYPES.items():
        for w in words:
            scores[t] += low.count(normalise(w))
    if scores and scores.most_common(1)[0][1] > 0:
        best = scores.most_common(1)[0][0]
        return best
    if "quotation" in name:
        return "COMMERCIAL_QUOTATION"
    return "OTHER"


# canonical key -> label wordings seen in quotations and tenders
ALIASES = {
    "material": ["material", "material of construction", "material grade", "material specification", "raw material", "grade"],
    "tolerance": ["tolerance", "dimensional tolerance", "machining tolerance", "tolerances"],
    "wall_thickness": ["wall thickness", "thickness", "minimum wall thickness"],
    "surface_finish": ["surface finish", "finish", "surface roughness", "roughness"],
    "certification": ["certification", "certifications", "certificate", "certificates", "quality certification", "quality standard", "quality standards", "quality system", "accreditation"],
    "inspection_testing": ["inspection and testing", "inspection", "testing", "test", "ndt", "inspection requirement", "inspection and test"],
    "manufacturing_process": ["manufacturing process", "process", "method of manufacture", "manufacturing method"],
    "capacity": ["manufacturing capacity", "monthly capacity", "production capacity", "capacity"],
    "standards": ["applicable standards", "standards", "applicable standard", "compliance standard"],
    "quantity": ["quantity", "qty", "required quantity", "order quantity", "quoted quantity"],
    "unit_price": ["unit price", "price per unit", "rate", "unit rate", "price each"],
    "base_price": ["base price", "total base price", "basic price", "quoted price", "total price", "price", "total basic price", "ex-works price", "total value"],
    "freight": ["freight", "transportation", "freight and transportation", "freight charges", "delivery charges", "transport"],
    "taxes": ["taxes", "tax", "gst", "taxes and duties", "applicable taxes", "duties"],
    "delivery_days": ["delivery", "delivery time", "delivery timeline", "delivery period", "lead time", "delivery schedule"],
    "payment_terms": ["payment terms", "payment", "terms of payment", "payment conditions"],
    "warranty": ["warranty", "warranty period", "guarantee"],
    "validity": ["quotation validity", "validity", "offer validity", "bid validity", "validity of quotation"],
    "budget": ["estimated budget", "budget", "estimated cost", "estimated value", "approximate budget"],
    "other_charges": ["other charges", "additional charges", "packing", "packing and forwarding", "packing & forwarding", "insurance"],
}
_ALIAS_INDEX = {normalise(w): key for key, words in ALIASES.items() for w in words}
COMMERCIAL_KEYS = {"quantity", "unit_price", "base_price", "freight", "taxes", "delivery_days", "payment_terms", "warranty", "validity", "budget", "other_charges"}


def canonical_key(label):
    n = normalise(label)
    if n in _ALIAS_INDEX:
        return _ALIAS_INDEX[n]
    return slug(label)


def category_for(key):
    return "commercial" if key in COMMERCIAL_KEYS else "technical"


_KV = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 /&()\-.,%]{1,60}?)\s*(?::|\||\t|-{1,2}\s)\s*(?P<value>\S.*?)\s*$")
_HEADER_CELLS = {"parameter", "description", "item", "requirement", "specification", "particulars", "sl no", "s no", "sr no", "details"}


def extract_facts(pages):
    """Every "Label: value" line and "label | value" table row becomes a fact with its source reference."""
    facts = []
    for page in pages:
        for line in page.text.splitlines():
            if not line.strip():
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 2 and cells[0] and cells[1]:
                label, value = cells[0], " | ".join(cells[1:])
            else:
                m = _KV.match(line)
                if not m:
                    continue
                label, value = m.group("label").strip(), m.group("value").strip()
            if normalise(label) in _HEADER_CELLS or normalise(value) in _HEADER_CELLS or len(label) > 70:
                continue
            facts.append({"key": canonical_key(label), "label": label, "value": value, "line": line.strip(), "ref": page.ref, "page": page.number})
    return facts


def chunk_pages(pages, size=700):
    """Split page text into paragraph-aligned chunks, each remembering its page reference."""
    chunks = []
    for page in pages:
        buf = ""
        for para in re.split(r"\n+", page.text):
            if buf and len(buf) + len(para) > size:
                chunks.append((page, buf.strip()))
                buf = ""
            buf += para + "\n"
        if buf.strip():
            chunks.append((page, buf.strip()))
    return chunks


# Hashed bag-of-words vectors: local, deterministic, no external service. Swap `embed` for an Ollama or
# sentence-transformer model (and pgvector on Postgres) without touching the callers.
DIM = 512
_TOKEN = re.compile(r"[a-z0-9]+")


def embed(text):
    counts = Counter()
    toks = _TOKEN.findall(str(text).lower())
    for t in toks:
        counts[hash_token(t)] += 1
    for a, b in zip(toks, toks[1:]):
        counts[hash_token(a + "_" + b)] += 1
    vec = {i: 1 + math.log(c) for i, c in counts.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {str(i): round(v / norm, 5) for i, v in vec.items()}


def hash_token(tok):
    h = 2166136261
    for ch in tok.encode():
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return h % DIM


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())
