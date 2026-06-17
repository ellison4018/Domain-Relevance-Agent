"""ICP nodes for the fallback branch (unreachable / cloud-error recovery fail).

Two LangGraph nodes:

* ``icp_query_node`` — calls the local ICP API and stores the raw record.
* ``icp_relevance_judge`` — asks a lightweight LLM to decide whether the ICP
  registrant's unit name / licence suggests an adult-education / course business.
"""

from __future__ import annotations

from typing import Any

from app.graph.state import DomainGraphState, add_trace
from app.rules.loader import get_categories, get_scene_rules
from app.tools.icp_provider import query_icp
from app.tools.llm_client import complete_json

try:
    import tldextract
except ImportError as exc:  # pragma: no cover
    raise ImportError("tldextract is required; run: pip install tldextract") from exc


def _first_record(icp_result: dict[str, Any]) -> dict[str, Any] | None:
    records = icp_result.get("records") or []
    return records[0] if records else None


def _registrable_domain(domain: str) -> str:
    extracted = tldextract.extract(domain)
    return extracted.registered_domain or domain


def icp_query_node(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: query local ICP API for the normalized apex/domain.

    The MIIT database is keyed by registered domain.  If a search for the full
    apex/subdomain returns no records, we automatically fall back to the
    registrable domain (e.g. ``lihua.tenclass.com`` -> ``tenclass.com``).
    """
    normalized = state.get("normalized") or {}
    apex = normalized.get("apex") or state.get("domain") or ""

    result = query_icp(apex)
    queried = [apex]

    # Fallback to registrable domain when the upstream call succeeds but is empty.
    if result.get("success") and not result.get("records"):
        registered = _registrable_domain(apex)
        if registered and registered != apex:
            result = query_icp(registered)
            queried.append(registered)

    new_state = add_trace(
        state,
        node="icp_query",
        input_data={"domain": apex, "queried": queried},
        output_data={
            "success": result.get("success"),
            "records_count": len(result.get("records") or []),
            "error": result.get("error"),
        },
    )
    new_state["icp_result"] = result
    return new_state


def _build_icp_prompt(state: DomainGraphState, rules: dict[str, Any], categories: list[str]) -> str:
    scene_name = rules.get("primary_scene") or rules.get("scene") or "目标业务场景"

    icp_result = state.get("icp_result") or {}
    record = _first_record(icp_result) or {}

    unit_name = record.get("unit_name") or ""
    nature_name = record.get("nature_name") or ""
    main_licence = record.get("main_licence") or ""
    service_licence = record.get("service_licence") or ""
    domain = record.get("domain") or (state.get("domain") or "")

    return f"""你是一名域名备案信息相关性裁判助手。请根据该域名的工信部/ICP备案信息，判断其主办主体是否与「{scene_name}」业务场景相关。

## 目标业务场景
{scene_name}

## 二级类别（category 必须从中选一个，或 null）
{', '.join(categories)}

## ICP 备案信息
- 域名: {domain}
- 主办单位名称: {unit_name}
- 主办单位性质: {nature_name}
- 备案号: {main_licence}
- 服务备案号: {service_licence}

## 裁判规则
1. relevance:
   - "match": 明确是成人教育/课程培训/知识付费/文化传媒相关企业。
   - "no_match": 明确不是。
   - "uncertain": 信息不足或企业性质模糊（如仅显示"科技有限公司"而无具体业务词）。
2. category: 主办单位名称指向某个二级类别（兴趣类/副业类/金融保险类）时填写最相关的一个；无法判断填 null。
3. 不要给出数值置信度评分，评分由后续流程根据结论确定。
4. 只基于备案信息判断，不要脑补网页内容。

请严格返回 JSON，格式如下：
{{
  "relevance": "match|no_match|uncertain",
  "category": "兴趣类|副业类|金融保险类|null",
  "reasoning": "..."
}}
"""


def icp_relevance_judge(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: lightweight LLM judgement on ICP registrant relevance."""
    icp_result = state.get("icp_result") or {}

    # No records or query failed: skip LLM, fall back to uncertain.
    if not icp_result.get("success") or not _first_record(icp_result):
        fallback = {
            "relevance": "uncertain",
            "category": None,
            "reasoning": "ICP query returned no record or failed; skipping ICP judge.",
        }
        new_state = add_trace(
            state,
            node="icp_judge",
            input_data={"icp_success": icp_result.get("success"), "records": 0},
            output_data=fallback,
        )
        new_state["icp_judgement"] = fallback
        return new_state

    scene_cfg = state.get("scene_config")
    rules = get_scene_rules(scene_cfg)
    categories = get_categories(scene_cfg)
    prompt = _build_icp_prompt(state, rules, categories)
    result = complete_json(prompt)

    if result.get("_llm_failed"):
        result = {
            "relevance": "uncertain",
            "category": None,
            "reasoning": f"LLM ICP judge failed: {result.get('error')}",
            "_llm_failed": True,
            "error": result.get("error"),
        }
    else:
        result.setdefault("relevance", "uncertain")
        result.setdefault("category", None)
        result.setdefault("reasoning", "")
        if result["relevance"] not in ("match", "no_match", "uncertain"):
            result["relevance"] = "uncertain"

    new_state = add_trace(
        state,
        node="icp_judge",
        input_data={
            "domain": state.get("domain"),
            "unit_name": (_first_record(icp_result) or {}).get("unit_name"),
        },
        output_data=result,
    )
    new_state["icp_judgement"] = result
    return new_state
