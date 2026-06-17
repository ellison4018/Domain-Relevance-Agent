"""Score calibration node — verdict-driven, deterministic.

The LLM no longer emits a numeric confidence. Both the crawl branch
(``relevance_judgement``) and the ICP branch (``icp_judgement``) produce only a
categorical verdict ``relevance`` ∈ {match, no_match, uncertain} plus a 二级
``category`` label. This node maps that verdict, the structured evidence
signals, the historical prior, the generic-tool penalty, and content quality
into a final score and a routing decision.

Single scoring formula (identical for both branches; the crawl branch simply
adds an evidence-derived ``signal_bonus``):

    final = clamp(
        base(relevance)
      + signal_bonus        # crawl branch only: weighted positive/negative signals
      + category_bonus      # LLM gave a non-null category
      + consistency_bonus    # LLM category == historical matched_category
      + prior_bonus         # historical prior, capped
      - generic_penalty     # generic platform/tool
    )

Routing:
    - content quality ``empty``/``low``        -> uncertain, human_review (crawl only)
    - relevance == no_match                      -> no_match, done
    - relevance == match and final >= threshold -> match, done
    - otherwise                                  -> uncertain, human_review

All constants come from ``scene_config.yaml`` (``scoring`` section) via
``get_scoring_config``; canonical defaults are kept in ``loader.py``.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import DomainGraphState, add_trace
from app.rules.loader import get_scoring_config


def _clamp(value: int) -> int:
    return max(0, min(100, value))


def _clamp_range(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _has_crawl_content(state: DomainGraphState) -> bool:
    crawl = state.get("crawl_result") or {}
    return bool(crawl.get("success"))


def _verdict_from(state: DomainGraphState) -> tuple[str, str | None, str]:
    """Return (relevance, category, branch) from whichever judge ran.

    The crawl branch populates ``relevance_judgement``; the ICP branch
    populates ``icp_judgement``. Both share the {relevance, category} schema.
    """
    if _has_crawl_content(state):
        judgement = state.get("relevance_judgement") or {}
        return judgement.get("relevance") or "uncertain", judgement.get("category"), "crawl"
    judgement = state.get("icp_judgement") or {}
    return judgement.get("relevance") or "uncertain", judgement.get("category"), "icp"


def _base_score(relevance: str, cfg: dict[str, Any]) -> int:
    if relevance == "match":
        return int(cfg["base_match"])
    if relevance == "no_match":
        return int(cfg["base_no_match"])
    return int(cfg["base_uncertain"])


def _signal_bonus(state: DomainGraphState, cfg: dict[str, Any]) -> int:
    """Weighted sum of evidence signal strengths (crawl branch only).

    evidence carries positive_signals/negative_signals as
    ``[{"quote", "strength"}, ...]``. The ICP branch has no crawled content,
    so it contributes 0 here.
    """
    evidence = state.get("evidence") or {}
    if not _has_crawl_content(state):
        return 0

    weight = int(cfg["signal_weight"])
    pos = sum(int(s.get("strength") or 0) for s in (evidence.get("positive_signals") or []))
    neg = sum(int(s.get("strength") or 0) for s in (evidence.get("negative_signals") or []))
    raw = (pos - neg) * weight
    return _clamp_range(raw, int(cfg["signal_min"]), int(cfg["signal_max"]))


def _category_bonus(category: str | None, cfg: dict[str, Any]) -> int:
    return int(cfg["category_bonus"]) if category else 0


def _consistency_bonus(category: str | None, matched_category: str | None, cfg: dict[str, Any]) -> int:
    if category and matched_category and category == matched_category:
        return int(cfg["consistency_bonus"])
    return 0


def _prior_bonus(prior_score: int, cfg: dict[str, Any]) -> int:
    cap = int(cfg["prior_bonus_max"])
    return round(min(prior_score / 100.0 * cap, cap))


def score_calibration(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: calibrate final relevance score and route."""
    cfg = get_scoring_config(state.get("scene_config"))

    relevance, category, branch = _verdict_from(state)

    historical_prior = state.get("historical_prior") or {}
    prior_score = int(historical_prior.get("prior_score") or 0)
    matched_category = historical_prior.get("matched_category")

    generic_tool_result = state.get("generic_tool_result") or {}
    generic_penalty = int(generic_tool_result.get("penalty") or 0)

    content_quality = state.get("content_quality") or {}
    quality_level = content_quality.get("level")
    quality_score = int(content_quality.get("score") or 0)

    base = _base_score(relevance, cfg)
    signal_bonus = _signal_bonus(state, cfg)
    cat_b = _category_bonus(category, cfg)
    cons_b = _consistency_bonus(category, matched_category, cfg)
    prior_b = _prior_bonus(prior_score, cfg)

    raw_score = _clamp(base + signal_bonus + cat_b + cons_b + prior_b - generic_penalty)

    components = {
        "base": base,
        "signal_bonus": signal_bonus,
        "category_bonus": cat_b,
        "consistency_bonus": cons_b,
        "prior_bonus": prior_b,
        "generic_penalty": generic_penalty,
    }

    threshold = int(cfg["match_threshold"])
    quality_low = int(cfg["quality_low_threshold"])

    reason_parts: list[str] = [
        f"branch={branch}",
        f"relevance={relevance}",
        f"category={category}",
        f"prior_bonus={prior_b}, generic_penalty={generic_penalty}",
    ]

    # A clear no_match is terminal regardless of content quality: a login page
    # or irrelevant site is confidently negative even on thin content. The
    # quality gate below only protects against false positives / uncertain
    # calls on weak pages.
    if relevance == "no_match":
        match_result = "no_match"
        next_action = "done"
        needs_human_review = False
        reason_parts.append("judged no_match")
    elif branch == "crawl" and (quality_level in ("empty", "low") or quality_score < quality_low):
        match_result = "uncertain"
        next_action = "human_review"
        needs_human_review = True
        reason_parts.append(f"weak content quality (level={quality_level}) triggers human review")
    elif relevance == "match" and raw_score >= threshold:
        match_result = "match"
        next_action = "done"
        needs_human_review = False
        reason_parts.append("strong match")
    elif relevance == "match":
        match_result = "uncertain"
        next_action = "human_review"
        needs_human_review = True
        reason_parts.append(f"match but score {raw_score} below threshold {threshold}")
    else:
        # relevance == uncertain
        match_result = "uncertain"
        next_action = "human_review"
        needs_human_review = True
        reason_parts.append("judged uncertain")

    # ICP-branch special case: when the ICP query itself failed we have no
    # registrant signal at all — leave it for later enrichment rather than
    # human review.
    if branch == "icp":
        icp_result = state.get("icp_result") or {}
        if not icp_result.get("success"):
            next_action = "icp_query"
            reason_parts.append("ICP query failed; insufficient signal")

    result: dict[str, Any] = {
        "final_score": raw_score,
        "match_result": match_result,
        "category": category,
        "next_action": next_action,
        "needs_human_review": needs_human_review,
        "components": components,
        "branch": branch,
        "reason": "; ".join(reason_parts),
    }

    new_state = add_trace(
        state,
        node="score_calibration",
        input_data={
            "branch": branch,
            "relevance": relevance,
            "category": category,
            "prior_score": prior_score,
            "generic_penalty": generic_penalty,
        },
        output_data=result,
    )
    new_state["calibrated_score"] = result
    new_state["match_result"] = match_result
    new_state["needs_human_review"] = needs_human_review
    new_state["next_action"] = next_action
    return new_state
