"""Persistence node.

Writes probe results, recovery attempts, crawl result, markdown, and quality
assessment to a deterministic directory:

    data/crawl_artifacts/{batch_id}/{domain_hash}/

The ``artifact_paths`` field in state records the written file paths so that
later stages (or human reviewers) can locate the raw crawl artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.graph.state import DomainGraphState, add_trace


_ARTIFACT_ROOT = Path("data") / "crawl_artifacts"


def _domain_hash(domain: str) -> str:
    """Stable short hash for the domain/apex used as a directory name."""
    return hashlib.md5(domain.encode("utf-8")).hexdigest()[:12]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def persist(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: persist crawl artifacts to disk."""
    scene_config = state.get("scene_config") or {}
    batch_id = scene_config.get("batch_id") or "default"
    apex = (state.get("normalized") or {}).get("apex") or state.get("domain") or "unknown"

    out_dir = _ARTIFACT_ROOT / batch_id / _domain_hash(apex)
    _ensure_dir(out_dir)

    artifact_paths: dict[str, str] = {}

    probe_results = state.get("probe_results")
    if probe_results is not None:
        path = out_dir / "probe_results.json"
        path.write_text(json.dumps(probe_results, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_paths["probe_results"] = str(path)

    recovery_attempts = state.get("recovery_attempts")
    if recovery_attempts is not None:
        path = out_dir / "recovery_attempts.json"
        path.write_text(json.dumps(recovery_attempts, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_paths["recovery_attempts"] = str(path)

    crawl_result = state.get("crawl_result")
    if crawl_result is not None:
        path = out_dir / "crawl_result.json"
        path.write_text(json.dumps(crawl_result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_paths["crawl_result"] = str(path)

    quality = state.get("content_quality")
    if quality is not None:
        path = out_dir / "content_quality.json"
        path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_paths["content_quality"] = str(path)

    for key, filename in (
        ("historical_prior", "historical_prior.json"),
        ("generic_tool_result", "generic_tool_result.json"),
        ("evidence", "evidence.json"),
        ("relevance_judgement", "relevance_judgement.json"),
        ("icp_result", "icp_result.json"),
        ("icp_judgement", "icp_judgement.json"),
        ("calibrated_score", "calibrated_score.json"),
    ):
        value = state.get(key)
        if value is not None:
            path = out_dir / filename
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact_paths[key] = str(path)

    markdown = state.get("markdown")
    if markdown is not None:
        path = out_dir / "markdown.md"
        path.write_text(markdown, encoding="utf-8")
        artifact_paths["markdown"] = str(path)

    # Persist a normalized snapshot of the final state for reproducibility.
    state_path = out_dir / "state.json"
    serializable_state = {
        k: v
        for k, v in state.items()
        if k not in {"trace"}
    }
    state_path.write_text(json.dumps(serializable_state, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact_paths["state"] = str(state_path)

    new_state = add_trace(
        state,
        node="persist",
        input_data={"batch_id": batch_id, "domain_hash": _domain_hash(apex)},
        output_data={"artifact_paths": artifact_paths},
    )
    new_state["artifact_paths"] = artifact_paths
    return new_state
