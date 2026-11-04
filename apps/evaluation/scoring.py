"""Eligibility and scoring. Pure functions over evaluation results: same inputs and config -> same ranking."""
from copy import deepcopy

DEFAULT_CONFIG = {
    "technical_weight": 0.60,
    "commercial_weight": 0.40,
    "commercial_weights": {"cost": 70, "delivery": 15, "warranty": 10, "validity": 5},
    "min_validity_days": 30,
}


def build_config(overrides=None):
    cfg = deepcopy(DEFAULT_CONFIG)
    o = overrides or {}
    for k in ("technical_weight", "commercial_weight"):
        if k in o:
            cfg[k] = float(o[k])
    if "commercial_weights" in o:
        cfg["commercial_weights"].update({k: float(v) for k, v in o["commercial_weights"].items() if k in cfg["commercial_weights"]})
    total = cfg["technical_weight"] + cfg["commercial_weight"]
    if total <= 0 or any(v < 0 for v in (cfg["technical_weight"], cfg["commercial_weight"])):
        raise ValueError("Weights must be non-negative and not all zero.")
    cfg["technical_weight"], cfg["commercial_weight"] = cfg["technical_weight"] / total, cfg["commercial_weight"] / total
    return cfg


BAD = {"NON_COMPLIANT"}
UNKNOWN = {"NOT_SPECIFIED", "REQUIRES_VERIFICATION"}


def eligibility(tech, comm):
    """-> (status, reasons). Only requirements the tender itself marks mandatory can exclude a vendor."""
    if tech is None or comm is None:
        return "INCOMPLETE", ["One of the evaluations could not be completed, so no overall score is given."]
    gaps = []
    if tech.get("technical_score") is None:
        gaps.append("No technical requirements could be scored.")
    if comm.get("commercial_score") is None:
        gaps.append("The commercial score is unavailable: " + ("; ".join(comm.get("missing_information", [])[:3]) or "price information missing") + ".")
    if gaps:
        return "INCOMPLETE", gaps
    failed, unclear = [], []
    for r in tech["requirements"] + comm["requirement_checks"]:
        if r["mandatory"] is not True:
            continue
        if r["status"] in BAD:
            failed.append(f"Mandatory requirement not met - {r['parameter']}: {r['deviation'] or 'non-compliant'}")
        elif r["status"] in UNKNOWN:
            unclear.append(f"Mandatory requirement could not be confirmed - {r['parameter']}: {r['evidence'] or 'requires manual verification'}")
    if failed:
        return "NOT_ELIGIBLE", failed + unclear
    if unclear:
        return "REQUIRES_VERIFICATION", unclear
    return "ELIGIBLE", []


def score_and_rank(merged, config):
    out = []
    for v in merged:
        tech, comm = v["technical"], v["commercial"]
        status, reasons = eligibility(tech, comm)
        ts = tech["technical_score"] if tech else None
        cs = comm["commercial_score"] if comm else None
        overall = None
        if status != "INCOMPLETE" and ts is not None and cs is not None:
            overall = round(ts * config["technical_weight"] + cs * config["commercial_weight"], 2)
        out.append({"vendor_id": v["vendor_id"], "vendor_name": v["vendor_name"], "technical_score": ts, "commercial_score": cs,
                    "overall_score": overall, "eligibility": status, "eligibility_reasons": reasons, "rank": None})
    eligible = [o for o in out if o["eligibility"] == "ELIGIBLE" and o["overall_score"] is not None]
    cost = {v["vendor_id"]: (v["commercial"] or {}).get("comparable_total_cost") or (v["commercial"] or {}).get("comparable_pre_tax_cost") or float("inf") for v in merged}
    eligible.sort(key=lambda o: (-o["overall_score"], cost[o["vendor_id"]], o["vendor_name"].lower()))
    for i, o in enumerate(eligible, start=1):
        o["rank"] = i
    order = {"ELIGIBLE": 0, "REQUIRES_VERIFICATION": 1, "NOT_ELIGIBLE": 2, "INCOMPLETE": 3}
    out.sort(key=lambda o: (order[o["eligibility"]], o["rank"] or 999, -(o["overall_score"] or -1)))
    return out
