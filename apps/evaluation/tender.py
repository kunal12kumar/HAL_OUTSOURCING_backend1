"""Tender requirement extraction. Parses structured "Label: value" requirements deterministically; falls back to a
local LLM for free text. Output is validated against a fixed schema; unclear items are flagged, never invented."""
import re

from . import facts as F
from . import llm
from .readers import read_pages
from .units import normalise, parse_quantity

TECH_HEAD = re.compile(r"technical|specification|quality|material|manufactur", re.I)
COMM_HEAD = re.compile(r"commercial|financial|price|payment|delivery|terms", re.I)
MANDATORY = re.compile(r"\(?\[?\b(mandatory|essential|must|compulsory)\b\]?\)?", re.I)
OPTIONAL = re.compile(r"\(?\b(desirable|optional|preferred|good to have)\b\)?", re.I)
GTE = re.compile(r"(≥|>=|\bmin(imum)?\b|at least|not less than|or more|and above)", re.I)
LTE = re.compile(r"(≤|<=|\bmax(imum)?\b|not more than|not exceeding|up to|within|or less|and below)", re.I)

SCHEMA_KEYS = {"category", "key", "label", "raw", "comparator", "mandatory", "needs_verification"}


def extract_requirements(path, ext):
    pages = read_pages(path, ext)
    items, section = [], None
    for page in pages:
        heading = page.ref.removeprefix("Section: ")
        section = _section_of(heading, section)
        for line in page.text.splitlines():
            line_section = _section_of(line.strip(), None) if _looks_like_heading(line) else None
            if line_section:
                section = line_section
                continue
            for fact in F.extract_facts([_one(page, line)]):
                items.append(_build(fact, section))
    source = "parsed"
    if not items:
        items, source = _llm_fallback("\n".join(p.text for p in pages))
    items = _dedupe(items)
    for n, item in enumerate(items, start=1):
        item["id"] = f"R{n}"
    return validate({"source": source, "requirements": items, "pages": len(pages)})


def _one(page, line):
    from .readers import Page

    return Page(page.ref, page.number, line)


def _looks_like_heading(line):
    s = line.strip()
    return bool(s) and ":" not in s and "|" not in s and len(s) < 60 and bool(TECH_HEAD.search(s) or COMM_HEAD.search(s)) and "requirement" in s.lower()


def _section_of(text, current):
    t = text.lower()
    if "requirement" not in t and "terms" not in t and "conditions" not in t and "section" not in t:
        return current
    if "commercial" in t:
        return "commercial"
    if "technical" in t:
        return "technical"
    return current


def _build(fact, section):
    key = fact["key"]
    text = fact["value"]
    cat = F.category_for(key) if key in F.ALIASES else (section or "technical")
    mandatory = None
    if MANDATORY.search(text):
        mandatory = True
    elif OPTIONAL.search(text):
        mandatory = False
    clean = OPTIONAL.sub("", MANDATORY.sub("", text)).strip(" ()[],;")
    comparator = "gte" if GTE.search(clean) else "lte" if LTE.search(clean) else "eq"
    q = parse_quantity(clean)
    return {
        "category": cat, "key": key, "label": fact["label"], "raw": clean or text, "comparator": comparator,
        "mandatory": mandatory, "needs_verification": not clean, "quantity": _q(q),
        "source": {"ref": fact["ref"], "line": fact["line"]},
    }


def _q(q):
    if not q:
        return None
    return {"number": str(q["number"]), "unit": q["unit"], "dimension": q["dimension"], "base": str(q["base"]) if q["base"] is not None else None}


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        sig = (it["key"], normalise(it["raw"]))
        if sig not in seen:
            seen.add(sig)
            out.append(it)
    return out


def _llm_fallback(text):
    prompt = (
        "Extract tender requirements from the text. Return JSON {\"requirements\":[{\"category\":\"technical|commercial\","
        "\"label\":str,\"raw\":str,\"comparator\":\"eq|gte|lte\",\"mandatory\":true|false|null}]}. "
        "Use null for mandatory unless the text states it. Do not invent values.\n\n" + text[:6000]
    )
    data = llm.generate_json(prompt)
    out = []
    for r in (data or {}).get("requirements", []) if isinstance(data, dict) else []:
        if not isinstance(r, dict) or not r.get("label") or not r.get("raw"):
            continue
        key = F.canonical_key(str(r["label"]))
        cat = r.get("category") if r.get("category") in ("technical", "commercial") else F.category_for(key)
        cmp_ = r.get("comparator") if r.get("comparator") in ("eq", "gte", "lte") else "eq"
        mand = r.get("mandatory") if isinstance(r.get("mandatory"), bool) else None
        out.append({"category": cat, "key": key, "label": str(r["label"]), "raw": str(r["raw"]), "comparator": cmp_,
                    "mandatory": mand, "needs_verification": True, "quantity": _q(parse_quantity(str(r["raw"]))),
                    "source": {"ref": "LLM-extracted", "line": ""}})
    return out, "llm" if out else "none"


def validate(payload):
    """Schema check. Anything malformed is dropped from the evaluation set and reported, never silently kept."""
    good, problems = [], []
    for item in payload["requirements"]:
        if not SCHEMA_KEYS <= set(item):
            problems.append(f"{item.get('label', '?')}: incomplete record")
        elif item["category"] not in ("technical", "commercial") or item["comparator"] not in ("eq", "gte", "lte"):
            problems.append(f"{item['label']}: invalid category or comparator")
        else:
            good.append(item)
    payload["requirements"] = good
    payload["problems"] = problems
    payload["mandatory_identified"] = any(r["mandatory"] for r in good)
    payload["review_note"] = (
        "" if payload["mandatory_identified"]
        else "The tender does not mark any requirement as mandatory. Mandatory criteria must be set by an authorised reviewer; no vendor is excluded on this basis."
    )
    return payload
