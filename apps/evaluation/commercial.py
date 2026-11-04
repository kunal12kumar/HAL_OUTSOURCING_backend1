"""Commercial evaluation. Every figure is computed with Decimal in deterministic code and every assumption used is
written into the result. Nothing is inferred for missing values."""
from decimal import Decimal

from .technical import MISSING, NON, NOT_SPEC, COMPLIANT, VERIFY, UNSURE, find_fact
from .units import is_negative, normalise, parse_freight, parse_money, parse_quantity, parse_tax

COMM_DOCS = {"COMMERCIAL_QUOTATION"}
CENT = Decimal("0.01")


def _fact(vendor, key, docs=None):
    for d in vendor["documents"]:
        if docs and d["doc_type"] not in docs:
            continue
        for f in d["facts"]:
            if f["key"] == key:
                return f
    return None


def _days(text):
    q = parse_quantity(text)
    return q["base"] if q and q["dimension"] == "time" else None


def _months(text):
    q = parse_quantity(text)
    return q["base"] / Decimal(30) if q and q["dimension"] == "time" else None


def _src(f):
    return {"source_document": f["filename"], "document_id": f["document_id"], "page": f["page"], "page_ref": f["ref"], "evidence": f["line"]} if f else {}


def evaluate_vendor(vendor, reqs):
    docs = [d for d in vendor["documents"] if d["doc_type"] == "COMMERCIAL_QUOTATION"]
    missing, assumptions, sources = [], [], {}
    if not docs:
        missing.append("No commercial quotation was uploaded for this vendor.")

    def get(key):
        f = _fact(vendor, key, COMM_DOCS) or _fact(vendor, key)
        if f:
            sources[key] = _src(f)
        return f

    qty_req = next((r for r in reqs if r["key"] == "quantity"), None)
    req_qty = parse_quantity(qty_req["raw"]) if qty_req else None
    qf, uf, bf = get("quantity"), get("unit_price"), get("base_price")
    quoted_qty = parse_quantity(qf["value"])["number"] if qf and parse_quantity(qf["value"]) else None
    unit_price = parse_money(uf["value"]) if uf else None
    base = parse_money(bf["value"]) if bf else None
    base_basis = "quoted total" if base is not None else None
    if base is None and unit_price is not None:
        qty = quoted_qty if quoted_qty is not None else (req_qty["number"] if req_qty else None)
        if qty is not None:
            base, base_basis = (unit_price * qty).quantize(CENT), f"unit price x {qty:g} nos"
            assumptions.append(f"Base price calculated as unit price x quantity ({qty:g}); the quotation gives no total.")
            if quoted_qty is None:
                assumptions.append("Quantity taken from the tender because the quotation does not state one.")
    if base is None:
        missing.append("Base price")

    ff = get("freight")
    fr = parse_freight(ff["value"]) if ff else {"mode": "unknown", "amount": None}
    freight = fr["amount"]
    if freight is None:
        missing.append("Freight (not stated or not quantified)")
    elif fr["mode"] == "included":
        assumptions.append("Freight is stated as included; no separate freight cost added.")

    tf = get("taxes")
    tax = parse_tax(tf["value"]) if tf else {"mode": "unknown", "rate": None}
    tax_amount = None
    if tax["mode"] == "included":
        tax_amount = Decimal(0)
        assumptions.append("Taxes are stated as included in the quoted price; none added.")
    elif tax["mode"] == "excluded" and tax["rate"] is not None and base is not None:
        tax_amount = ((base) * tax["rate"] / 100).quantize(CENT)
        assumptions.append(f"Tax of {tax['rate']:g}% applied to the base price only; tax on freight is not assumed.")
    else:
        missing.append("Tax treatment unclear (included or extra not confirmed)" if tf else "Taxes")

    pre_tax = (base + (freight or Decimal(0))) if base is not None else None
    total = (pre_tax + (tax_amount if tax["mode"] != "included" else Decimal(0))) if (pre_tax is not None and tax_amount is not None and freight is not None) else None

    other = get("other_charges")
    if other and parse_money(other["value"]) not in (None, Decimal(0)) and not is_negative(other["value"]):
        missing.append("Other charges are quoted but not included in the comparable cost; requires manual verification.")

    df, wf, vf, pf = get("delivery_days"), get("warranty"), get("validity"), get("payment_terms")
    delivery = _days(df["value"]) if df else None
    warranty = _months(wf["value"]) if wf else None
    validity = _days(vf["value"]) if vf else None
    for name, val, f in (("Delivery timeline", delivery, df), ("Warranty", warranty, wf), ("Quotation validity", validity, vf)):
        if val is None:
            missing.append(name if not f else f"{name} could not be read (requires manual verification)")
    if pf is None:
        missing.append("Payment terms")

    checks = _requirement_checks(vendor, reqs, delivery, warranty, validity, pf, total if total is not None else pre_tax, quoted_qty)
    return {
        "vendor_id": vendor["vendor_id"], "vendor_name": vendor["vendor_name"],
        "base_price": _n(base), "base_price_basis": base_basis, "unit_price": _n(unit_price),
        "quantity": _n(quoted_qty), "freight": _n(freight), "freight_mode": fr["mode"],
        "taxes": {"mode": tax["mode"], "rate": _n(tax["rate"]), "amount": _n(tax_amount)},
        "comparable_pre_tax_cost": _n(pre_tax), "comparable_total_cost": _n(total),
        "delivery_days": _n(delivery), "warranty_months": _n(warranty), "validity_days": _n(validity),
        "payment_terms": pf["value"] if pf else None, "assumptions": assumptions, "missing_information": missing,
        "requirement_checks": checks, "sources": sources,
    }


def _n(x):
    return None if x is None else float(x)


def _requirement_checks(vendor, reqs, delivery, warranty, validity, pf, cost, quoted_qty):
    checks = []
    values = {"delivery_days": (delivery, "days"), "warranty": (warranty, "months"), "validity": (validity, "days")}
    for r in reqs:
        if r["category"] != "commercial" or r["key"] in ("budget",):
            continue
        row = {"requirement_id": r["id"], "parameter": r["label"], "required": r["raw"], "mandatory": r["mandatory"],
               "offered": None, "status": NOT_SPEC, "deviation": "", "evidence": MISSING}
        if r["key"] in values:
            val, unit = values[r["key"]]
            rq = parse_quantity(r["raw"])
            if val is None:
                pass
            elif not rq or rq["dimension"] != "time":
                row.update(offered=f"{val:g} {unit}", status=VERIFY, evidence=UNSURE)
            else:
                need = rq["base"] if unit == "days" else rq["base"] / Decimal(30)
                cmp_ = r["comparator"] if r["comparator"] != "eq" else ("lte" if r["key"] == "delivery_days" else "gte")
                ok = val >= need if cmp_ == "gte" else val <= need
                row.update(offered=f"{val:g} {unit}", status=COMPLIANT if ok else NON, evidence="",
                           deviation="" if ok else f"{val:g} {unit} offered against {r['raw']}")
        elif r["key"] == "payment_terms" and pf:
            row.update(offered=pf["value"], status=COMPLIANT if normalise(r["raw"]) in normalise(pf["value"]) else VERIFY,
                       evidence=pf["line"] if normalise(r["raw"]) in normalise(pf["value"]) else UNSURE)
        elif r["key"] == "quantity" and quoted_qty is not None:
            rq = parse_quantity(r["raw"])
            ok = rq and Decimal(rq["number"]) == Decimal(quoted_qty)
            row.update(offered=f"{quoted_qty:g}", status=COMPLIANT if ok else NON,
                       deviation="" if ok else f"Quoted quantity {quoted_qty:g} differs from required {r['raw']}", evidence="")
        elif r["key"] in ("delivery_days", "warranty", "validity", "payment_terms", "quantity"):
            pass
        else:
            row.update(status=VERIFY, evidence=UNSURE)
        checks.append(row)
    return checks


def score_vendors(results, reqs, config):
    """0-100 commercial score for each vendor with a deterministic, configurable breakdown.

    Cost is scored against the lowest comparable cost on a basis common to all vendors: total landed cost if every
    vendor's tax is resolved, otherwise the pre-tax subtotal for everyone (stated in the result).
    """
    w = config["commercial_weights"]
    basis = "comparable_total_cost" if results and all(r["comparable_total_cost"] is not None for r in results) else "comparable_pre_tax_cost"
    costs = [r[basis] for r in results if r[basis] is not None]
    lowest = min(costs) if costs else None
    deliveries = [r["delivery_days"] for r in results if r["delivery_days"] is not None]
    warranties = [r["warranty_months"] for r in results if r["warranty_months"] is not None]
    for r in results:
        parts = {}
        parts["cost"] = round(w["cost"] * (lowest / r[basis]), 2) if lowest and r[basis] else 0.0
        parts["delivery"] = round(w["delivery"] * (min(deliveries) / r["delivery_days"]), 2) if deliveries and r["delivery_days"] else 0.0
        parts["warranty"] = round(w["warranty"] * (r["warranty_months"] / max(warranties)), 2) if warranties and r["warranty_months"] and max(warranties) else 0.0
        parts["validity"] = float(w["validity"]) if r["validity_days"] and r["validity_days"] >= config.get("min_validity_days", 30) else 0.0
        r["cost_basis"] = basis
        r["score_breakdown"] = parts
        r["commercial_score"] = round(sum(parts.values()) * 100 / sum(w.values()), 2) if r["base_price"] is not None else None
        r["cost_comparison"] = {
            "basis": basis, "lowest_cost": lowest, "difference_from_lowest": None if r[basis] is None or lowest is None else round(r[basis] - lowest, 2),
            "percent_above_lowest": None if r[basis] is None or not lowest else round((r[basis] - lowest) * 100 / lowest, 2),
        }
        r["strengths"] = []
        r["weaknesses"] = []
        if lowest is not None and r[basis] == lowest:
            r["strengths"].append("Lowest comparable cost among compared vendors")
        elif r["cost_comparison"]["percent_above_lowest"]:
            r["weaknesses"].append(f"Comparable cost is {r['cost_comparison']['percent_above_lowest']:.1f}% above the lowest")
        if deliveries and r["delivery_days"] == min(deliveries):
            r["strengths"].append(f"Shortest delivery commitment ({r['delivery_days']:g} days)")
        if warranties and r["warranty_months"] == max(warranties):
            r["strengths"].append(f"Longest warranty ({r['warranty_months']:g} months)")
        r["weaknesses"] += [f"{c['parameter']}: {c['deviation']}" for c in r["requirement_checks"] if c["status"] == NON and c["deviation"]]
    return results
