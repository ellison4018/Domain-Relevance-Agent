"""Crawl content node.

Fetches the page at ``selected_url`` (derived from recovery or probe results)
through the Crawl4AI client wrapper and stores the raw / fit markdown plus the
full crawl result dict.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import DomainGraphState, add_trace
from app.tools.crawl4ai_client import crawl_url


def _pick_selected_url(state: DomainGraphState) -> str | None:
    """Return the URL we should crawl.

    Priority:
      1. Explicit ``state["selected_url"]`` (set by path_recovery or earlier).
      2. The first successful probe result with a non-empty final_url.
    """
    explicit = state.get("selected_url")
    if explicit:
        return explicit

    for r in state.get("probe_results") or []:
        status = r.get("status_code")
        if status is not None and 200 <= status < 400:
            return r.get("final_url") or r.get("url")
    return None


def crawl_content(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: crawl the selected URL and store markdown."""
    url = _pick_selected_url(state)
    if not url:
        new_state = add_trace(
            state,
            node="crawl_content",
            input_data={},
            output_data={"error": "no_selected_url"},
        )
        new_state["crawl_result"] = {"success": False, "error": "no_selected_url"}
        new_state["markdown"] = ""
        return new_state

    result = crawl_url(url)

    markdown = ""
    if result.get("success"):
        markdown = result.get("fit_markdown") or result.get("raw_markdown") or ""

    new_state = add_trace(
        state,
        node="crawl_content",
        input_data={"url": url},
        output_data={
            "success": result.get("success"),
            "markdown_length": len(markdown),
            "title": result.get("title"),
        },
    )
    new_state["selected_url"] = url
    new_state["crawl_result"] = result
    new_state["markdown"] = markdown
    return new_state
