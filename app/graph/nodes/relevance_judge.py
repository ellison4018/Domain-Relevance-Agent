"""Relevance judge agent (LLM-based).

Consumes extracted evidence, scene rules, historical prior, generic-tool
result, and content quality. Outputs a categorical verdict only — no numeric
confidence. The final score is derived deterministically by
``score_calibration`` from this verdict, the category, and the structured
evidence signals.

Output schema:
    relevance: "match" | "no_match" | "uncertain"
    category:   二级 category label (兴趣类 / 副业类 / 金融保险类) or null
    reasoning:  short explanation
"""

from __future__ import annotations

import json
from typing import Any

from app.graph.state import DomainGraphState, add_trace
from app.rules.loader import get_categories, get_scene_rules
from app.tools.llm_client import complete_json


def _build_prompt(state: DomainGraphState, rules: dict[str, Any], categories: list[str]) -> str:
    scene_name = rules.get("primary_scene") or rules.get("scene") or "目标业务场景"
    global_positive = rules.get("global_positive_signals") or []
    global_negative = rules.get("global_negative_signals") or []

    evidence = state.get("evidence") or {}
    historical_prior = state.get("historical_prior") or {}
    generic_tool = state.get("generic_tool_result") or {}
    content_quality = state.get("content_quality") or {}

    positive_signals = evidence.get("positive_signals") or []
    negative_signals = evidence.get("negative_signals") or []

    return f"""你是一名业务相关性裁判助手。请根据以下输入，判断该域名是否与「{scene_name}」相关。

## 二级类别（category 必须从中选一个，或 null）
{', '.join(categories)}

## 全局正向信号
{', '.join(global_positive)}

## 全局负向信号
{', '.join(global_negative)}

## 历史先验
- prior_score: {historical_prior.get('prior_score')}
- matched_category: {historical_prior.get('matched_category')}
- match_type: {historical_prior.get('match_type')}
- reason: {historical_prior.get('reason')}

## 通用平台/工具检测结果
- is_generic: {generic_tool.get('is_generic')}
- matched_signals: {generic_tool.get('matched_signals')}
- reason: {generic_tool.get('reason')}

## 内容质量
- level: {content_quality.get('level')}
- score: {content_quality.get('score')}

## 提取到的证据
- language: {evidence.get('language')}
- has_course_or_service: {evidence.get('has_course_or_service')}
- contact_present: {evidence.get('contact_present')}
- key_topics: {evidence.get('key_topics')}
- reasoning: {evidence.get('reasoning')}

### 正向信号（含引用）
{json.dumps(positive_signals, ensure_ascii=False, indent=2)}

### 负向信号（含引用）
{json.dumps(negative_signals, ensure_ascii=False, indent=2)}

## 裁判规则
1. relevance 只能是 "match"、"no_match"、"uncertain" 之一。
2. category 从二级类别中选取最相关的一个；与业务无关或无法判断时填 null。
3. 不要给出数值置信度评分，评分由后续流程根据结论与证据确定。
4. 如果 generic_tool_result.is_generic 为 true 但页面内容强烈证明承载具体课程业务，仍可判 match，但需在 reasoning 中说明。
5. 如果历史先验 matched_category 非空，应作为重要参考（它指明该域名历史上所属的二级类别），但不能凌驾于内容证据之上。
6. 内容质量 low/empty 时，优先判 uncertain。

请严格返回 JSON，格式如下：
{{
  "relevance": "match|no_match|uncertain",
  "category": "兴趣类|副业类|金融保险类|null",
  "reasoning": "..."
}}
"""


def relevance_judge_agent(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: LLM relevance judgement."""
    scene_cfg = state.get("scene_config")
    rules = get_scene_rules(scene_cfg)
    categories = get_categories(scene_cfg)
    prompt = _build_prompt(state, rules, categories)

    result = complete_json(prompt)
    if result.get("_llm_failed"):
        result = {
            "relevance": "uncertain",
            "category": None,
            "reasoning": f"LLM relevance judge failed: {result.get('error')}",
            "_llm_failed": True,
            "error": result.get("error"),
        }
    else:
        result.setdefault("relevance", "uncertain")
        result.setdefault("category", None)
        result.setdefault("reasoning", "")
        # Coerce relevance into the allowed set.
        if result["relevance"] not in ("match", "no_match", "uncertain"):
            result["relevance"] = "uncertain"

    new_state = add_trace(
        state,
        node="relevance_judge_agent",
        input_data={
            "domain": state.get("domain"),
            "evidence_signal_counts": {
                "pos": len((state.get("evidence") or {}).get("positive_signals") or []),
                "neg": len((state.get("evidence") or {}).get("negative_signals") or []),
            },
        },
        output_data=result,
    )
    new_state["relevance_judgement"] = result
    return new_state
