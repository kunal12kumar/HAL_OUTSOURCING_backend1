"""generate_explanations: advantages, disadvantages and ranking rationale built only from evaluation records.

Each statement carries the source document and page of the evidence behind it. Nothing is generated freely, so
nothing can be fabricated; the wording is deliberately plain for non-technical readers.
"""


def _ref(r):
    if not r.get("source_document"):
        return None
    return {"document": r["source_document"], "document_id": r["document_id"], "page": r.get("page"), "location": r.get("page_ref"), "quote": r.get("evidence")}


def explain(ranking, merged):
    by_id = {v["vendor_id"]: v for v in merged}
    ranked = {r["vendor_id"]: r for r in ranking}
    eligible = [r for r in ranking if r["rank"]]
    out = {}
    for r in ranking:
        v = by_id[r["vendor_id"]]
        tech, comm = v["technical"], v["commercial"]
        pros, cons, unclear = [], [], []
        for q in (tech or {}).get("requirements", []):
            tag = " (mandatory)" if q["mandatory"] else ""
            if q["status"] == "COMPLIANT":
                pros.append({"text": f"Meets {q['parameter']}{tag}: offers {q['offered']} against {q['required']}.", "evidence": _ref(q)})
            elif q["status"] in ("NON_COMPLIANT", "PARTIALLY_COMPLIANT"):
                cons.append({"text": f"{q['parameter']}{tag}: {q['deviation'] or 'deviation from requirement'}.", "evidence": _ref(q)})
            else:
                unclear.append({"text": f"{q['parameter']}{tag}: information not available in the submitted document. Requires manual verification.", "evidence": None})
        if comm:
            for s in comm["strengths"]:
                pros.append({"text": s + ".", "evidence": None})
            for w in comm["weaknesses"]:
                cons.append({"text": w + ".", "evidence": None})
            for m in comm["missing_information"]:
                unclear.append({"text": f"{m}. Information not available in the submitted document.", "evidence": None})
            for c in comm["requirement_checks"]:
                if c["status"] == "COMPLIANT" and c["offered"]:
                    pros.append({"text": f"Meets {c['parameter']}: {c['offered']} against {c['required']}.", "evidence": None})
        out[r["vendor_id"]] = {"advantages": pros, "disadvantages": cons, "unclear": unclear, "summary": _summary(r, eligible, tech, comm, by_id)}
    return out


def _summary(r, eligible, tech, comm, by_id):
    name = r["vendor_name"]
    if r["eligibility"] == "INCOMPLETE":
        return f"{name} has an incomplete evaluation, so no overall score or rank is given. " + " ".join(r["eligibility_reasons"])
    if r["eligibility"] == "NOT_ELIGIBLE":
        failed = [x.split(" - ", 1)[1].split(":", 1)[0] for x in r["eligibility_reasons"] if x.startswith("Mandatory requirement not met")]
        return (f"{name} is not eligible because it does not meet {len(failed)} mandatory requirement(s): {', '.join(failed)}. "
                f"Its scores (technical {r['technical_score']}, commercial {r['commercial_score']}) are shown for information only and it is not ranked.")
    if r["eligibility"] == "REQUIRES_VERIFICATION":
        return f"{name} cannot be ranked until the following is verified: " + " ".join(r["eligibility_reasons"])
    c = tech["compliance_summary"]
    text = (f"{name} is ranked {r['rank']} of {len(eligible)} eligible vendors with an overall score of {r['overall_score']} "
            f"(technical {r['technical_score']}, commercial {r['commercial_score']}). It complies with {c['compliant']} requirement(s)")
    gaps = c["non_compliant"] + c["partially_compliant"]
    comm_gaps = [x["parameter"] for x in (comm or {}).get("requirement_checks", []) if x["status"] == "NON_COMPLIANT"]
    text += f", has {gaps} technical deviation(s)" if gaps else " with no technical deviations"
    if comm_gaps:
        text += f" and misses {len(comm_gaps)} commercial requirement(s) ({', '.join(comm_gaps)})"
    if c["not_specified"] + c["requires_verification"]:
        text += f" and {c['not_specified'] + c['requires_verification']} item(s) that need verification"
    text += "."
    if r["rank"] and r["rank"] > 1:
        top = eligible[0]
        text += f" It ranks below {top['vendor_name']} ({top['overall_score']}) mainly because: " + _gap(r, top, by_id) + "."
    elif r["rank"] == 1 and len(eligible) > 1:
        nxt = eligible[1]
        text += f" It leads {nxt['vendor_name']} by {round(r['overall_score'] - nxt['overall_score'], 2)} points."
    return text


def _gap(r, top, by_id):
    reasons = []
    if (r["technical_score"] or 0) < (top["technical_score"] or 0):
        reasons.append(f"its technical score is {round(top['technical_score'] - r['technical_score'], 2)} points lower")
    if (r["commercial_score"] or 0) < (top["commercial_score"] or 0):
        a, b = by_id[r["vendor_id"]]["commercial"], by_id[top["vendor_id"]]["commercial"]
        basis = a["cost_basis"]
        if a[basis] and b[basis] and a[basis] > b[basis]:
            reasons.append(f"its comparable cost ({a[basis]:,.0f}) is higher than {b[basis]:,.0f}")
        else:
            reasons.append(f"its commercial score is {round(top['commercial_score'] - r['commercial_score'], 2)} points lower")
    return "; ".join(reasons) or "the scores are very close"
