"""LangGraph builder for the domain-relevance pipeline.

Flow:

    START -> normalize_domain -> http_probe -> classify_access_status

    classify_access_status:
        reachable / login_only / weak_content / unknown  -> crawl_content
        cloud_error                                      -> path_recovery
        unreachable                                      -> compute_historical_prior (ICP branch)

    path_recovery:
        selected_url found  -> crawl_content
        not found           -> compute_historical_prior (ICP branch)

    crawl_content -> content_quality_check -> compute_historical_prior
        -> run_generic_filter -> conditional
            has crawled content  -> extract_evidence -> judge_relevance
            no crawled content   -> icp_query -> icp_judge
        -> calibrate_score -> persist -> END

    The ICP branch is also used when crawl_content succeeds but produces no
    usable markdown (crawl_result.success=false), giving registration metadata
    a chance to rescue the domain before routing to human_review.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.access_classifier import classify_access_status
from app.graph.nodes.content_quality import content_quality
from app.graph.nodes.crawl import crawl_content
from app.graph.nodes.evidence_extractor import extract_evidence_agent
from app.graph.nodes.generic_filter import generic_filter
from app.graph.nodes.historical_prior import historical_prior
from app.graph.nodes.icp import icp_query_node, icp_relevance_judge
from app.graph.nodes.normalize import normalize_domain
from app.graph.nodes.path_recovery import path_recovery
from app.graph.nodes.persist import persist
from app.graph.nodes.probe import http_probe_node
from app.graph.nodes.relevance_judge import relevance_judge_agent
from app.graph.nodes.score import score_calibration
from app.graph.state import DomainGraphState


def _has_crawl_content(state: DomainGraphState) -> bool:
    crawl = state.get("crawl_result") or {}
    return bool(crawl.get("success"))


def _route_after_classifier(state: DomainGraphState) -> str:
    status = state.get("access_status")
    if status == "unreachable":
        return "compute_historical_prior"
    if status == "cloud_error":
        return "path_recovery"
    return "crawl_content"


def _route_after_recovery(state: DomainGraphState) -> str:
    if state.get("selected_url"):
        return "crawl_content"
    return "compute_historical_prior"


def _route_after_generic_filter(state: DomainGraphState) -> str:
    if _has_crawl_content(state):
        return "extract_evidence"
    return "icp_query"


def build_graph():
    """Compile and return the domain-relevance state graph."""
    graph = StateGraph(DomainGraphState)

    graph.add_node("normalize_domain", normalize_domain)
    graph.add_node("http_probe", http_probe_node)
    graph.add_node("classify_access_status", classify_access_status)
    graph.add_node("path_recovery", path_recovery)
    graph.add_node("crawl_content", crawl_content)
    graph.add_node("content_quality_check", content_quality)
    graph.add_node("compute_historical_prior", historical_prior)
    graph.add_node("run_generic_filter", generic_filter)
    graph.add_node("extract_evidence", extract_evidence_agent)
    graph.add_node("judge_relevance", relevance_judge_agent)
    graph.add_node("icp_query", icp_query_node)
    graph.add_node("icp_judge", icp_relevance_judge)
    graph.add_node("calibrate_score", score_calibration)
    graph.add_node("persist", persist)

    graph.add_edge(START, "normalize_domain")
    graph.add_edge("normalize_domain", "http_probe")
    graph.add_edge("http_probe", "classify_access_status")

    graph.add_conditional_edges(
        "classify_access_status",
        _route_after_classifier,
        {
            "crawl_content": "crawl_content",
            "path_recovery": "path_recovery",
            "compute_historical_prior": "compute_historical_prior",
        },
    )

    graph.add_conditional_edges(
        "path_recovery",
        _route_after_recovery,
        {
            "crawl_content": "crawl_content",
            "compute_historical_prior": "compute_historical_prior",
        },
    )

    graph.add_edge("crawl_content", "content_quality_check")
    graph.add_edge("content_quality_check", "compute_historical_prior")

    graph.add_edge("compute_historical_prior", "run_generic_filter")

    graph.add_conditional_edges(
        "run_generic_filter",
        _route_after_generic_filter,
        {
            "extract_evidence": "extract_evidence",
            "icp_query": "icp_query",
        },
    )

    graph.add_edge("extract_evidence", "judge_relevance")
    graph.add_edge("judge_relevance", "calibrate_score")

    graph.add_edge("icp_query", "icp_judge")
    graph.add_edge("icp_judge", "calibrate_score")

    graph.add_edge("calibrate_score", "persist")
    graph.add_edge("persist", END)

    return graph.compile()
