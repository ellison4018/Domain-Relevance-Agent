"""Historical domain library loader and in-memory index.

Library file is a JSONL with objects:

    {"domain": "...", "category": "兴趣类|副业类", "industry": "..."}

``category`` (二级类别) is the authoritative label used for classification;
``industry`` (三级具体场景) is kept as informational metadata only and is **not**
used for classification or rating.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


try:
    import tldextract
except ImportError as exc:  # pragma: no cover
    raise ImportError("tldextract is required; run: pip install tldextract") from exc


DEFAULT_LIBRARY_PATH = Path("data") / "historical_domain_library.jsonl"


def _normalize_domain(domain: str) -> str:
    """Lowercase and strip leading/trailing whitespace and trailing slash."""
    return domain.strip().lower().rstrip("/")


def _strip_www(domain: str) -> str:
    if domain.startswith("www."):
        return domain[4:]
    return domain


def load_library(path: Path | str | None = None) -> dict[str, Any]:
    """Load historical domain library and build lookup indexes.

    Returns:
        {
            "entries": [{"domain": ..., "category": ..., "industry": ...}, ...],
            "by_domain": {normalized_domain: entry},
            "by_registrable_domain": {registered_domain: [entries]},
        }
    """
    if path is None:
        path = DEFAULT_LIBRARY_PATH
    path = Path(path)

    entries: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict) and "domain" in obj:
                    category = (obj.get("category") or "").strip()
                    entries.append(
                        {
                            "domain": _normalize_domain(obj["domain"]),
                            # category is authoritative; industry is metadata only.
                            "category": category,
                            "industry": (obj.get("industry") or "").strip(),
                        }
                    )

    by_domain: dict[str, dict[str, str]] = {}
    by_registrable_domain: dict[str, list[dict[str, str]]] = {}

    for entry in entries:
        by_domain[entry["domain"]] = entry
        registrable = _registrable_domain(entry["domain"])
        by_registrable_domain.setdefault(registrable, []).append(entry)

    return {
        "entries": entries,
        "by_domain": by_domain,
        "by_registrable_domain": by_registrable_domain,
    }


def _registrable_domain(domain: str) -> str:
    extracted = tldextract.extract(domain)
    return extracted.registered_domain or domain


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_historical_match(
    domain: str,
    library: dict[str, Any] | None = None,
    *,
    similarity_threshold: float = 0.85,
) -> dict[str, Any]:
    """Find the best historical prior match for ``domain``.

    Priority:
        1. exact match
        2. www/non-www variant
        3. same registrable_domain
        4. string similarity >= threshold
        5. none

    Returns a dict with prior_score, matched_category, matched_industry,
    match_type, matched_domain, reason.
    """
    if library is None:
        library = load_library()

    normalized = _normalize_domain(domain)
    apex = _strip_www(normalized)
    registrable = _registrable_domain(normalized)

    entries = library.get("entries", [])
    by_domain = library.get("by_domain", {})
    by_registrable = library.get("by_registrable_domain", {})

    # 1. exact
    if normalized in by_domain:
        entry = by_domain[normalized]
        return _make_result(100, entry, "exact", normalized)

    # 2. www/non-www variant
    variant = "www." + apex if not normalized.startswith("www.") else apex
    if variant in by_domain and variant != normalized:
        entry = by_domain[variant]
        return _make_result(90, entry, "www_variant", variant)

    # 3. same registrable_domain
    candidates = by_registrable.get(registrable, [])
    for entry in candidates:
        if _normalize_domain(entry["domain"]) != normalized:
            return _make_result(80, entry, "registrable_domain", entry["domain"])

    # 4. string similarity
    best: tuple[float, dict[str, str] | None] = (0.0, None)
    for entry in entries:
        other = _strip_www(_normalize_domain(entry["domain"]))
        if other == apex:
            continue
        ratio = _similarity(apex, other)
        if ratio > best[0]:
            best = (ratio, entry)

    if best[1] is not None and best[0] >= similarity_threshold:
        score = round(best[0] * 70)
        return _make_result(score, best[1], "string_similarity", best[1]["domain"])

    # 5. none
    return {
        "prior_score": 0,
        "matched_category": None,
        "matched_industry": None,
        "match_type": "none",
        "matched_domain": None,
        "reason": "historical library empty or no match",
    }


def _make_result(
    score: int,
    entry: dict[str, str],
    match_type: str,
    matched_domain: str,
) -> dict[str, Any]:
    return {
        "prior_score": score,
        "matched_category": entry.get("category") or None,
        "matched_industry": entry.get("industry") or None,
        "match_type": match_type,
        "matched_domain": matched_domain,
        "reason": f"{match_type} match against historical domain {matched_domain}",
    }
