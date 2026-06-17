"""Evidence extraction agent (LLM-based).

Extracts structured factual evidence from crawled content. It does **not**
produce the final relevance verdict; that is left to ``relevance_judge_agent``.

Output schema is intentionally lean: it no longer tags each signal with a 三级
sub-scene id, and it emits no numeric confidence (the score is derived
deterministically by ``score_calibration`` from the verdict + these signals).
"""

from __future__ import annotations

from typing import Any

from app.graph.state import DomainGraphState, add_trace
from app.rules.loader import get_categories, get_scene_rules
from app.tools.llm_client import complete_json


def _build_prompt(state: DomainGraphState, rules: dict[str, Any], categories: list[str]) -> str:
    scene_name = rules.get("primary_scene") or rules.get("scene") or "目标业务场景"
    global_positive = rules.get("global_positive_signals") or []
    global_negative = rules.get("global_negative_signals") or []

    crawl_result = state.get("crawl_result") or {}
    content_quality = state.get("content_quality") or {}

    return f"""你是一名域名业务内容分析助手。请从以下网页抓取内容中提取客观证据，用于后续判断该域名是否与「{scene_name}」业务场景相关。

## 二级类别参考
{', '.join(categories)}

## 全局正向信号
{', '.join(global_positive)}

## 全局负向信号
{', '.join(global_negative)}

## 页面信息
- 域名: {state.get('domain')}
- 选定URL: {state.get('selected_url')}
- 页面标题: {crawl_result.get('title')}
- 内容质量评分: {content_quality.get('score')}
- 内容等级: {content_quality.get('level')}

## 抓取正文（Markdown）
{state.get('markdown') or '(无内容)'}

## 任务要求
1. 只提取客观证据，不要输出最终分类结论，也不要给出数值置信度。
2. 用短引用（quote）支持每条信号，必须来自上面的 Markdown 原文。
3. strength: 1=弱，2=中，3=强。

请严格返回 JSON，格式如下：
{{
  "language": "zh|en|mixed",
  "has_course_or_service": true|false,
  "contact_present": true|false,
  "positive_signals": [
    {{"quote": "...", "strength": 1}}
  ],
  "negative_signals": [
    {{"quote": "...", "strength": 1}}
  ],
  "key_topics": ["..."],
  "reasoning": "..."
}}
"""


def extract_evidence_agent(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: extract structured evidence via LLM."""
    scene_cfg = state.get("scene_config")
    rules = get_scene_rules(scene_cfg)
    categories = get_categories(scene_cfg)
    prompt = _build_prompt(state, rules, categories)

    result = complete_json(prompt)
    if result.get("_llm_failed"):
        result = {
            "language": None,
            "has_course_or_service": False,
            "contact_present": False,
            "positive_signals": [],
            "negative_signals": [],
            "key_topics": [],
            "reasoning": f"LLM evidence extraction failed: {result.get('error')}",
            "_llm_failed": True,
            "error": result.get("error"),
        }
    else:
        result.setdefault("language", None)
        result.setdefault("has_course_or_service", False)
        result.setdefault("contact_present", False)
        result.setdefault("positive_signals", [])
        result.setdefault("negative_signals", [])
        result.setdefault("key_topics", [])
        result.setdefault("reasoning", "")

    new_state = add_trace(
        state,
        node="extract_evidence_agent",
        input_data={
            "domain": state.get("domain"),
            "markdown_length": len((state.get("markdown") or "")),
        },
        output_data=result,
    )
    new_state["evidence"] = result
    return new_state
