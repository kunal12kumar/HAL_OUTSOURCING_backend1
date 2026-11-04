"""Technical evaluation: compares every technical tender requirement with what each vendor's documents say.

Exact comparisons (grades, numbers, units, tolerances) are deterministic Python. Retrieval only decides which
document text to look at; similarity is never treated as proof of compliance.
"""
import difflib
import re
from decimal import Decimal

from .units import is_negative, is_positive, normalise, parse_quantity

COMPLIANT, PARTIAL, NON, NOT_SPEC, VERIFY = "COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "NOT_SPECIFIED", "REQUIRES_VERIFICATION"
POINTS = {COMPLIANT: 1.0, PARTIAL: 0.5, NON: 0.0, NOT_SPEC: 0.0, VERIFY: 0.0}
PARTIAL_MARGIN = Decimal("0.05")  # a numeric miss within 5% of the requirement counts as partial
MISSING = "Information not available in the submitted document."
UNSURE = "Requires manual verification."

TECH_DOCS = {"TECHNICAL_QUOTATION", "DATASHEET", "SPECIFICATION", "CERTIFICATE"}


def material_token(text):
    s = normalise(text).replace("stainless steel", "ss").replace("aisi", "ss").replace("stainless", "ss")
    return s.replace(" ", "")


def split_alternatives(text):
    return [p.strip() for p in re.split(r"\s+or\s+|/|;", str(text)) if p.strip()]


def split_items(text):
    return [p.strip() for p in re.split(r",|\band\b|;|&", str(text)) if p.strip()]


def compare(req, offered):
    """-> (status, deviation_text). `offered` is the vendor's raw value text."""
    if is_negative(offered):
        return NON, f"Vendor states: {offered}"
    rq = req.get("quantity")
    if rq and rq.get("dimension"):
        return _numeric(req, rq, parse_quantity(offered), offered)
    if req["key"] == "material":
        return _material(req, offered)
    if normalise(req["raw"]) in ("yes", "required"):
        return (COMPLIANT, "") if is_positive(offered) else (VERIFY, UNSURE)
    return _text(req, offered)


def _numeric(req, rq, oq, offered):
    if not oq or not oq.get("dimension"):
        return VERIFY, UNSURE
    if oq["dimension"] != rq["dimension"]:
        return VERIFY, f"Unit mismatch (required {rq['unit']}, offered {oq['unit'] or 'no unit'}). {UNSURE}"
    need, have = Decimal(rq["base"]), oq["base"]
    cmp_ = req["comparator"]
    if req["key"] == "tolerance" and cmp_ == "eq":  # a tighter tolerance always satisfies a stated limit
        cmp_ = "lte"
    ok = have >= need if cmp_ == "gte" else have <= need if cmp_ == "lte" else have == need
    if ok:
        return COMPLIANT, ""
    gap = abs(have - need) / need if need else Decimal(1)
    word = {"gte": "below the minimum", "lte": "above the maximum", "eq": "different from the requirement"}[cmp_]
    return (PARTIAL if gap <= PARTIAL_MARGIN else NON), f"Offered {offered} is {word} of {req['raw']}"


def _material(req, offered):
    wanted = {material_token(a) for a in split_alternatives(req["raw"])}
    got = {material_token(a) for a in split_alternatives(offered)}
    if wanted & got:
        return COMPLIANT, ""
    return NON, f"Offered material {offered} is not {req['raw']}; different grades are not treated as equivalent."


def negated_near(term, text):
    """True when `term` appears in `text` with a negation directly before or after it ("AS9100 - Not certified")."""
    low, t = str(text).lower(), str(term).lower()
    i = low.find(t)
    while i >= 0:
        before, after = low[max(0, i - 25): i], low[i + len(t): i + len(t) + 25]
        if re.search(r"\b(not|no|without|nil|non)\b[^.;,|]*$", before) or re.match(r"\s*[:\-\u2013(]*\s*(not|no|nil|n/a|pending)\b", after):
            return True
        i = low.find(t, i + 1)
    return False


def _text(req, offered):
    want, have = normalise(req["raw"]), normalise(offered)
    if not want:
        return VERIFY, UNSURE
    if want in have:
        if negated_near(req["raw"], offered):
            return NON, f"The vendor states this is not provided: {offered}"
        return COMPLIANT, ""
    items = [normalise(i) for i in split_items(req["raw"])]
    if len(items) > 1:
        hit = [i for i in items if i and i in have]
        if len(hit) == len(items):
            return COMPLIANT, ""
        if hit:
            return PARTIAL, "Not offered: " + ", ".join(i for i in items if i not in hit)
    return VERIFY, UNSURE


def find_fact(req, facts):
    """Structured lookup by canonical key, then by near-identical label."""
    for f in facts:
        if f["key"] == req["key"]:
            return f
    for f in facts:
        if difflib.SequenceMatcher(None, normalise(f["label"]), normalise(req["label"])).ratio() >= 0.88:
            return f
    return None


def find_in_text(req, chunks):
    """Exact code/standard mention (e.g. "AS9100") in document text. Only for requirements that contain a
    digit-bearing code, so ordinary words are never matched loosely. Returns (chunk, negative?)."""
    codes = [c for c in re.findall(r"[A-Za-z]*\d[\w\-/]*", req["raw"]) if len(c) >= 4]
    for ch in chunks:
        squashed = normalise(ch["text"]).replace(" ", "")
        for code in codes:
            if normalise(code).replace(" ", "") in squashed:
                return ch, negated_near(code, ch["text"])
    return None, False


def evaluate_vendor(vendor, reqs):
    has_tech_doc = any(d["doc_type"] in TECH_DOCS for d in vendor["documents"])
    results = [_evaluate_requirement(vendor, req, has_tech_doc) for req in reqs]
    names = {"compliant": COMPLIANT, "partially_compliant": PARTIAL, "non_compliant": NON,
             "not_specified": NOT_SPEC, "requires_verification": VERIFY}
    counts = {k: sum(1 for r in results if r["status"] == v) for k, v in names.items()}
    score = round(100 * sum(POINTS[r["status"]] for r in results) / len(results), 2) if results else None
    return {
        "vendor_id": vendor["vendor_id"], "vendor_name": vendor["vendor_name"], "technical_score": score,
        "compliance_summary": counts, "requirements": results,
        "strengths": [f"{r['parameter']}: {r['offered']}" for r in results if r["status"] == COMPLIANT],
        "deviations": [f"{r['parameter']}: {r['deviation']}" for r in results if r["status"] in (NON, PARTIAL) and r["deviation"]],
        "missing_information": [r["parameter"] for r in results if r["status"] in (NOT_SPEC, VERIFY)],
    }


def _evaluate_requirement(vendor, req, has_tech_doc):
    out = {"requirement_id": req["id"], "parameter": req["label"], "required": req["raw"], "mandatory": req["mandatory"],
           "offered": None, "status": NOT_SPEC, "deviation": "", "evidence": MISSING, "source_document": None,
           "document_id": None, "page": None, "page_ref": None, "note": ""}
    if not has_tech_doc:
        out["note"] = "No technical document was uploaded for this vendor."
        return out
    facts = [f for d in vendor["documents"] if d["doc_type"] in TECH_DOCS for f in d["facts"]]
    fact = find_fact(req, facts)
    if fact:
        status, deviation = compare(req, fact["value"])
        out.update(offered=fact["value"], status=status, deviation=deviation, evidence=fact["line"],
                   source_document=fact["filename"], document_id=fact["document_id"], page=fact["page"], page_ref=fact["ref"])
        if status == VERIFY:
            out["note"] = UNSURE
        return out
    chunk, negative = find_in_text(req, vendor["tech_chunks"])
    if chunk:
        out.update(offered="Stated as not available" if negative else "Mentioned in document",
                   status=NON if negative else COMPLIANT,
                   deviation="The document states this is not provided." if negative else "",
                   evidence=chunk["text"][:300], source_document=chunk["filename"], document_id=chunk["document_id"],
                   page=chunk["page"], page_ref=chunk["page_ref"])
        if negative:
            out["note"] = "Explicit negative statement found in the document."
        return out
    out["note"] = "Documents were found but contain no information on this requirement."
    return out
