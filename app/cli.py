"""CLI entry point for the domain-relevance agent.

Usage:
    python -m app.cli \
        --domains domains.txt \
        --scene-config scene_config.json \
        --output results.jsonl
"""

import argparse
import json
from pathlib import Path

from app.graph.builder import build_graph
from app.rules.loader import load_scene_config


def _read_domains(path: Path) -> list[str]:
    """Read one domain per line, skipping blanks and comments."""
    domains: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        domains.append(line)
    return domains


def _summarize_probe(results: list[dict] | None) -> list[dict]:
    """Produce a small JSON-serializable summary for results.jsonl."""
    out = []
    for r in results or []:
        out.append(
            {
                "url": r.get("url"),
                "status_code": r.get("status_code"),
                "final_url": r.get("final_url"),
                "response_time_ms": r.get("response_time_ms"),
                "content_length": r.get("content_length"),
                "error": r.get("error"),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Domain relevance agent")
    parser.add_argument("--domains", required=True, type=Path, help="Path to domains.txt")
    parser.add_argument(
        "--scene-config", required=True, type=Path, help="Path to scene_config.yaml or .json"
    )
    parser.add_argument("--output", required=True, type=Path, help="Path to results.jsonl")
    args = parser.parse_args(argv)

    domains = _read_domains(args.domains)
    scene_config = load_scene_config(args.scene_config)

    graph = build_graph()

    processed = 0
    with args.output.open("w", encoding="utf-8") as fh:
        for domain in domains:
            initial_state = {
                "domain": domain,
                "scene_config": scene_config,
                "trace": [],
            }
            final = graph.invoke(initial_state)
            crawl_result = final.get("crawl_result") or {}
            record = {
                "domain": domain,
                "normalized": final.get("normalized"),
                "access_status": final.get("access_status"),
                "selected_url": final.get("selected_url"),
                "crawl_status": {
                    "success": crawl_result.get("success"),
                    "error": crawl_result.get("error"),
                    "title": crawl_result.get("title"),
                },
                "content_quality": final.get("content_quality"),
                "historical_prior": final.get("historical_prior"),
                "generic_tool_result": final.get("generic_tool_result"),
                "evidence": final.get("evidence"),
                "relevance_judgement": final.get("relevance_judgement"),
                "icp_judgement": final.get("icp_judgement"),
                "calibrated_score": final.get("calibrated_score"),
                "match_result": final.get("match_result"),
                "needs_human_review": final.get("needs_human_review", False),
                "next_action": final.get("next_action"),
                "artifact_paths": final.get("artifact_paths"),
                "probe_summary": _summarize_probe(final.get("probe_results")),
                "trace": final.get("trace", []),
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            processed += 1

    print(f"Processed {processed} domains -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
