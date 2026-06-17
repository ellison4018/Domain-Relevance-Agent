"""DomainGraphState definition and trace helpers."""

from datetime import datetime, timezone
from typing import Any, Optional
from typing_extensions import TypedDict


class DomainGraphState(TypedDict, total=False):
    """Shared state carried through the domain-relevance LangGraph."""

    domain: str
    scene_config: dict[str, Any]
    normalized: Optional[dict[str, Any]]
    probe_results: Optional[list[dict[str, Any]]]
    access_status: Optional[str]
    selected_url: Optional[str]
    recovery_attempts: Optional[list[dict[str, Any]]]
    crawl_result: Optional[dict[str, Any]]
    markdown: Optional[str]
    content_quality: Optional[dict[str, Any]]

    # Phase 3 relevance judgement
    historical_prior: Optional[dict[str, Any]]
    generic_tool_result: Optional[dict[str, Any]]
    evidence: Optional[dict[str, Any]]
    relevance_judgement: Optional[dict[str, Any]]
    calibrated_score: Optional[dict[str, Any]]
    match_result: Optional[str]
    needs_human_review: Optional[bool]

    # Phase 4 ICP fallback branch
    icp_result: Optional[dict[str, Any]]
    icp_judgement: Optional[dict[str, Any]]

    next_action: Optional[str]
    artifact_paths: Optional[dict[str, Any]]
    trace: list[dict[str, Any]]
    error: Optional[str]


def add_trace(
    state: DomainGraphState,
    node: str,
    input_data: Any,
    output_data: Any,
) -> DomainGraphState:
    """Return a new state with an appended trace record.

    Nodes are pure(ish) functions: they receive a state dict and return a new
    dict.  This helper makes it easy to record what happened without mutating
    the input state in place.
    """
    new_state = dict(state)
    trace = list(new_state.get("trace") or [])
    trace.append(
        {
            "node": node,
            "input": input_data,
            "output": output_data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    new_state["trace"] = trace
    return new_state
