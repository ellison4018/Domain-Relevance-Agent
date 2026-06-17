"""Path recovery node.

When the apex variants return a cloud/WAF/cdn error (``access_status`` ==
``cloud_error``), this node tries a list of candidate paths under the apex.
Each candidate is probed with :func:`app.tools.http_probe.probe_url`; the first
candidate that returns a usable, non-cloud-blocked page becomes
``selected_url``.  All attempts are recorded in ``recovery_attempts``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.graph.nodes.access_classifier import _has_cloud_signature, _is_success
from app.graph.state import DomainGraphState, add_trace
from app.tools.http_probe import probe_url


_MAX_CANDIDATES = 20


def _load_candidates(rules_path: Path | None = None) -> list[str]:
    if rules_path is None:
        # path_recovery.py lives at app/graph/nodes/; rules/ is under app/.
        rules_path = Path(__file__).parents[2] / "rules" / "path_candidates.yaml"
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    return list(data.get("candidates") or [])


def _build_candidate_urls(apex: str, candidates: list[str]) -> list[tuple[str, str]]:
    """Return (url, path) pairs.  Prefer https for recovery probes."""
    base = f"https://{apex}"
    out: list[tuple[str, str]] = []
    for path in candidates:
        # Avoid double slashes while still allowing the root path.
        if path == "/":
            url = base
        else:
            url = f"{base.rstrip('/')}/{path.lstrip('/')}"
        out.append((url, path))
    return out


def _is_usable(result: dict[str, Any]) -> bool:
    """A candidate is usable if it succeeds and is not a cloud block page."""
    if not _is_success(result):
        return False
    if _has_cloud_signature(result):
        return False
    body = (result.get("body_preview") or "").strip()
    if result.get("content_length") == 0 and not body:
        return False
    return True


def _attempt_path_recovery(
    apex: str,
    candidates: list[str],
    max_candidates: int = _MAX_CANDIDATES,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Probe candidates and return (selected_url, attempts).

    *selected_url* is the first usable candidate URL, or ``None`` if none
    succeeded within ``max_candidates`` attempts.
    """
    attempts: list[dict[str, Any]] = []
    selected_url: str | None = None

    for url, path in _build_candidate_urls(apex, candidates[:max_candidates]):
        result = probe_url(url)
        attempt = {
            "url": url,
            "path": path,
            "status_code": result.get("status_code"),
            "final_url": result.get("final_url"),
            "content_length": result.get("content_length"),
            "response_time_ms": result.get("response_time_ms"),
            "error": result.get("error"),
            "is_usable": _is_usable(result),
        }
        attempts.append(attempt)

        if _is_usable(result):
            selected_url = result.get("final_url") or url
            break

    return selected_url, attempts


def path_recovery(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: attempt to recover a reachable path for cloud_error domains."""
    apex = state.get("normalized", {}).get("apex")
    if not apex:
        raise ValueError("normalized.apex is required for path_recovery")

    candidates = _load_candidates()
    selected_url, attempts = _attempt_path_recovery(apex, candidates)

    output: dict[str, Any] = {
        "candidates_tried": len(attempts),
        "selected_url": selected_url,
    }

    new_state = add_trace(
        state,
        node="path_recovery",
        input_data={"apex": apex, "access_status": state.get("access_status")},
        output_data=output,
    )
    new_state["recovery_attempts"] = attempts
    new_state["selected_url"] = selected_url

    # If we found a usable URL, append its probe result so downstream classifiers
    # can treat it as a normal reachable page.
    if selected_url and attempts:
        usable_attempt = next(a for a in attempts if a["is_usable"])
        new_probe = {
            "url": usable_attempt["url"],
            "status_code": usable_attempt["status_code"],
            "final_url": usable_attempt["final_url"],
            "response_time_ms": usable_attempt["response_time_ms"],
            "headers": {},
            "content_length": usable_attempt["content_length"],
            "body_preview": "",
            "error": None,
            "recovered": True,
        }
        existing = list(new_state.get("probe_results") or [])
        existing.append(new_probe)
        new_state["probe_results"] = existing

    return new_state
