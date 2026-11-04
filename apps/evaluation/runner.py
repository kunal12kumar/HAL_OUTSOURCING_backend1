"""Runs an analysis (in a background thread from the API, or inline in tests) and stores progress and results."""
import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from . import graph, scoring, tender
from .models import AnalysisStatus, TenderAnalysis

logger = logging.getLogger(__name__)


NODES = ["extract_tender_requirements", "prepare_vendor_candidates", "technical_evaluation", "commercial_evaluation",
         "merge_evaluations", "eligibility_check", "vendor_scoring_engine", "generate_explanations", "generate_report"]
# nodes that start once the key node has finished
STARTS_AFTER = {"extract_tender_requirements": ["prepare_vendor_candidates"],
                "prepare_vendor_candidates": ["technical_evaluation", "commercial_evaluation"],
                "merge_evaluations": ["eligibility_check"], "eligibility_check": ["vendor_scoring_engine"],
                "vendor_scoring_engine": ["generate_explanations"], "generate_explanations": ["generate_report"]}


def run_analysis(pk):
    """All database writes happen on this thread; the graph's parallel branches only compute."""
    close_old_connections()
    a = TenderAnalysis.objects.get(pk=pk)
    progress = {n: {"status": "PENDING"} for n in NODES}

    def save():
        TenderAnalysis.objects.filter(pk=pk).update(progress=dict(progress))

    def mark(node, status):
        progress[node]["status"] = status
        progress[node]["at"] = timezone.now().isoformat()

    try:
        cfg = scoring.build_config(a.config)
        compiled = graph.build_graph()
        mark(NODES[0], "RUNNING")
        save()
        final = None
        for mode, chunk in compiled.stream(
            {"evaluation_id": str(a.evaluation_id), "tender_requirements": a.requirements, "config": cfg,
             "vendor_ids": a.config.get("vendor_ids"), "errors": [], "node_log": []},
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                final = chunk
                continue
            for node, update in chunk.items():
                failed = node in ("technical_evaluation", "commercial_evaluation") and (update or {}).get(
                    "technical_results" if node == "technical_evaluation" else "commercial_results") is None
                mark(node, "FAILED" if failed else "DONE")
                for nxt in STARTS_AFTER.get(node, []):
                    mark(nxt, "RUNNING")
            save()
        report = final["report"]
        TenderAnalysis.objects.filter(pk=pk).update(
            result=report, progress=dict(progress), error="", finished_at=timezone.now(),
            status=AnalysisStatus.INCOMPLETE if report["status"] == "INCOMPLETE" else AnalysisStatus.COMPLETED)
    except Exception as exc:
        logger.exception("analysis %s failed", pk)
        for n, p in progress.items():
            if p["status"] == "RUNNING":
                p["status"] = "FAILED"
        TenderAnalysis.objects.filter(pk=pk).update(status=AnalysisStatus.FAILED, error=str(exc)[:1000],
                                                    progress=dict(progress), finished_at=timezone.now())
    finally:
        close_old_connections()


def start_in_background(pk):
    threading.Thread(target=run_analysis, args=(pk,), daemon=True).start()
