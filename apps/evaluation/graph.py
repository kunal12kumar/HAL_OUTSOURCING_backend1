"""LangGraph workflow for a tender evaluation.

START -> extract_tender_requirements -> prepare_vendor_candidates -> (technical_evaluation || commercial_evaluation)
      -> merge_evaluations -> eligibility_check -> vendor_scoring_engine -> generate_explanations -> generate_report -> END

The two evaluation nodes write to separate state keys and run in parallel. Failures are caught per branch (after
retries), recorded in `errors`, and surfaced by the merge node as an incomplete evaluation.
"""
import logging
import operator
import time
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import commercial, explain, retrieval, scoring, technical

logger = logging.getLogger(__name__)
RETRIES = 2


class EvalState(TypedDict, total=False):
    evaluation_id: str
    tender_requirements: dict
    config: dict
    vendor_ids: list
    candidates: list
    technical_results: list | None
    commercial_results: list | None
    errors: Annotated[list, operator.add]
    node_log: Annotated[list, operator.add]
    merged: list
    ranking: list
    explanations: dict
    report: dict


def _now():
    return datetime.now(timezone.utc).isoformat()


def with_retry(fn, label):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            return fn(), None
        except Exception as exc:
            last = f"{label} failed on attempt {attempt}: {exc.__class__.__name__}: {exc}"
            logger.warning(last)
            time.sleep(0.05)
    return None, last


def build_graph(loaders=None):
    """`loaders` lets the caller supply requirement extraction. Nodes touch no shared mutable state, so the two
    evaluation branches can run in separate threads; progress is reported by the caller from the update stream."""
    loaders = loaders or {}

    def tracked(name):
        def wrap(fn):
            def node(state):
                started = _now()
                update = fn(state)
                return {**update, "node_log": [{"node": name, "started_at": started, "finished_at": _now()}]}
            node.__name__ = name
            return node
        return wrap

    @tracked("extract_tender_requirements")
    def extract_tender_requirements(state):
        reqs = state.get("tender_requirements") or (loaders["extract"]() if "extract" in loaders else None)
        if not reqs or not reqs.get("requirements"):
            raise ValueError("No requirements could be extracted from the tender document.")
        return {"tender_requirements": reqs}

    @tracked("prepare_vendor_candidates")
    def prepare_vendor_candidates(state):
        cands = retrieval.prepare(state["tender_requirements"], state.get("vendor_ids"))
        if not cands:
            raise ValueError("No processed vendor documents are available to evaluate.")
        return {"candidates": cands}

    @tracked("technical_evaluation")
    def technical_evaluation(state):
        reqs = [r for r in state["tender_requirements"]["requirements"] if r["category"] == "technical"]
        result, err = with_retry(lambda: [technical.evaluate_vendor(c, reqs) for c in state["candidates"]], "Technical evaluation")
        return {"technical_results": result, "errors": [{"branch": "technical", "error": err}] if err else []}

    @tracked("commercial_evaluation")
    def commercial_evaluation(state):
        reqs = [r for r in state["tender_requirements"]["requirements"] if r["category"] == "commercial"]

        def run():
            rows = [commercial.evaluate_vendor(c, reqs) for c in state["candidates"]]
            return commercial.score_vendors(rows, reqs, state["config"])

        result, err = with_retry(run, "Commercial evaluation")
        return {"commercial_results": result, "errors": [{"branch": "commercial", "error": err}] if err else []}

    @tracked("merge_evaluations")
    def merge_evaluations(state):
        tech = {r["vendor_id"]: r for r in state.get("technical_results") or []}
        comm = {r["vendor_id"]: r for r in state.get("commercial_results") or []}
        merged = []
        for c in state["candidates"]:
            vid = c["vendor_id"]
            merged.append({"vendor_id": vid, "vendor_name": c["vendor_name"], "vendor_code": c["vendor_code"],
                           "technical": tech.get(vid), "commercial": comm.get(vid), "documents": [
                               {"document_id": d["document_id"], "filename": d["filename"], "doc_type": d["doc_type"], "warnings": d["warnings"]}
                               for d in c["documents"]], "notes": c["duplicate_notes"]})
        return {"merged": merged}

    @tracked("eligibility_check")
    def eligibility_check(state):
        for v in state["merged"]:
            v["eligibility"], v["eligibility_reasons"] = scoring.eligibility(v["technical"], v["commercial"])
        return {"merged": state["merged"]}

    @tracked("vendor_scoring_engine")
    def vendor_scoring_engine(state):
        return {"ranking": scoring.score_and_rank(state["merged"], state["config"])}

    @tracked("generate_explanations")
    def generate_explanations(state):
        return {"explanations": explain.explain(state["ranking"], state["merged"])}

    @tracked("generate_report")
    def generate_report(state):
        reqs = state["tender_requirements"]
        incomplete = [e for e in state.get("errors", []) if e.get("error")]
        report = {
            "evaluation_id": state["evaluation_id"], "generated_at": _now(), "config": state["config"],
            "requirements": reqs["requirements"], "mandatory_identified": reqs["mandatory_identified"],
            "review_note": reqs["review_note"], "status": "INCOMPLETE" if incomplete else "COMPLETED", "errors": incomplete,
            "ranking": state["ranking"], "vendors": {str(v["vendor_id"]): {**v, **state["explanations"][v["vendor_id"]],
                                                                          **{k: r for k, r in next(x for x in state["ranking"] if x["vendor_id"] == v["vendor_id"]).items() if k in ("overall_score", "rank")}}
                                                     for v in state["merged"]},
            "node_log": state.get("node_log", []),
        }
        return {"report": report}

    g = StateGraph(EvalState)
    for fn in (extract_tender_requirements, prepare_vendor_candidates, technical_evaluation, commercial_evaluation,
               merge_evaluations, eligibility_check, vendor_scoring_engine, generate_explanations, generate_report):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "extract_tender_requirements")
    g.add_edge("extract_tender_requirements", "prepare_vendor_candidates")
    g.add_edge("prepare_vendor_candidates", "technical_evaluation")
    g.add_edge("prepare_vendor_candidates", "commercial_evaluation")
    g.add_edge(["technical_evaluation", "commercial_evaluation"], "merge_evaluations")  # waits for both branches
    for a, b in (("merge_evaluations", "eligibility_check"), ("eligibility_check", "vendor_scoring_engine"),
                 ("vendor_scoring_engine", "generate_explanations"), ("generate_explanations", "generate_report"),
                 ("generate_report", END)):
        g.add_edge(a, b)
    return g.compile()
