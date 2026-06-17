"""Generic platform / tool detection node.

Produces ``generic_tool_result`` used by the relevance judge and score
calibration as a penalty signal.
"""

from __future__ import annotations

from app.graph.state import DomainGraphState, add_trace
from app.rules.loader import get_scene_rules


_DEFAULT_PENALTY = 35
_FALLBACK_GENERIC_SIGNALS = [
    "aliyun",
    "aliyuncs",
    "tencentcloud",
    "qcloud",
    "cloudflare",
    "cloudfront",
    "cdn",
    "stats",
    "analytics",
    "tracking",
    "paypal",
    "stripe",
    "alipay",
    "shopify",
    "myshopify.com",
    "wordpress.com",
    "wixsite.com",
    "wix.com",
    "squarespace",
    "webflow.io",
    "notion.site",
    "github.io",
    "github.com",
    "weixin.qq.com",
    "有赞",
    "微盟",
    "SaaS",
    "自助建站",
    "免费建站",
]


def _get_policy(scene_config: dict | None) -> dict:
    # Prefer an explicit runtime generic_tool_policy; otherwise fall back to
    # canonical scene rules from app/rules/scene_config.yaml.
    if scene_config and "generic_tool_policy" in scene_config:
        policy = scene_config["generic_tool_policy"] or {}
    else:
        rules = get_scene_rules(scene_config)
        policy = rules.get("generic_tool_policy") or {}
    return {
        "default_penalty": policy.get("default_penalty", _DEFAULT_PENALTY),
        "signals": policy.get("signals") or _FALLBACK_GENERIC_SIGNALS,
    }


def generic_filter(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: detect generic platform/tool signals and emit penalty."""
    normalized = state.get("normalized") or {}
    domain = normalized.get("apex") or state.get("domain") or ""
    url = state.get("selected_url") or ""
    markdown = state.get("markdown") or ""

    policy = _get_policy(state.get("scene_config"))
    penalty = policy["default_penalty"]
    signals = policy["signals"]

    text = f"{domain} {url} {markdown}".lower()
    matched: list[str] = []
    for signal in signals:
        if signal.lower() in text:
            matched.append(signal)

    is_generic = bool(matched)
    result = {
        "is_generic": is_generic,
        "matched_signals": matched,
        "penalty": penalty if is_generic else 0,
        "reason": (
            f"matched generic signals: {matched}" if is_generic else "no generic tool/platform signals detected"
        ),
    }

    new_state = add_trace(
        state,
        node="generic_filter",
        input_data={"domain": domain, "selected_url": url, "markdown_length": len(markdown)},
        output_data=result,
    )
    new_state["generic_tool_result"] = result
    return new_state
