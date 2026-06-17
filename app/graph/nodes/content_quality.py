"""Content quality node (rule-based).

Scores the crawled markdown and emits:

    {
        "level": "high|medium|low|empty",
        "score": 0-100,
        "positive_signals": [...],
        "negative_signals": [...],
        "reason": "...",
    }

Rules only — no LLM here.  The score is intentionally coarse; it exists to
route weak pages toward ``icp_query`` and keep strong pages for downstream
classification.

Design notes for this domain-relevance use-case:

* We target Chinese adult-education / course landing pages. Positive keyword
  list is therefore Chinese-centric.
* A page that merely contains login keywords (e.g. 登录 / 密码) is **not**
  automatically downgraded to ``low``.  Many education sites have a login gate
  while still carrying highly relevant business information (e.g. "微课太极网
  登录入口").  Login keywords only apply a small penalty and are recorded as a
  soft signal so the downstream LLM judge can still see the real content.
* ``认证`` is intentionally removed from the negative list — it is a common
  positive word on Chinese education pages (资质认证 / 等级考评规范).
"""

from __future__ import annotations

import re
from typing import Any

from app.graph.state import DomainGraphState, add_trace


# Chinese-centric positive signals for adult-education / course landing pages,
# plus a few generic Chinese business page signals.
_POSITIVE_KEYWORDS = [
    # Education / course signals
    "课程",
    "训练营",
    "直播课",
    "录播课",
    "公开课",
    "体验课",
    "付费课",
    "学员",
    "老师",
    "讲师",
    "导师",
    "教练",
    "社群",
    "报名",
    "零基础",
    "小白",
    "入门",
    "变现",
    "副业",
    "私域",
    "领取资料",
    "添加老师",
    "扫码进群",
    "教学",
    "学习",
    "练习",
    "授课",
    # Generic business page signals (Chinese)
    "关于我们",
    "公司简介",
    "产品介绍",
    "服务",
    "解决方案",
    "案例",
    "新闻",
    "博客",
    "团队",
    "合作",
    "技术",
    "平台",
    "联系我们",
    "售后",
    "客户",
]

# Auth-gate keywords.  We keep them separate from placeholder/error keywords
# because their presence should only mildly depress the quality score; they do
# NOT force the page level to "low".
_LOGIN_KEYWORDS = [
    "login",
    "log in",
    "sign in",
    "signin",
    "password",
    "用户名",
    "密码",
    "登录",
    "authentication",
    "sso",
]

# Cloud/WAF/error or placeholder-page keywords.  These remain strong negatives.
_NEGATIVE_KEYWORDS = [
    "403 forbidden",
    "404 not found",
    "503 service unavailable",
    "cloudflare",
    "akamai",
    "waf",
    "under construction",
    "coming soon",
    "maintenance",
    "建设中",
    "即将上线",
    "维护中",
    "无法访问",
    "错误",
]


def _count_structural_elements(text: str) -> dict[str, int]:
    headings = len(re.findall(r"^#{1,6}\s", text, re.MULTILINE))
    lists = len(re.findall(r"^\s*[-*+]\s", text, re.MULTILINE))
    paragraphs = len(
        [p for p in re.split(r"\n\s*\n", text.strip()) if len(p.strip()) > 40]
    )
    return {"headings": headings, "lists": lists, "paragraphs": paragraphs}


def _evaluate_quality(markdown: str | None, url: str | None) -> dict[str, Any]:
    text = (markdown or "").strip()
    stripped_len = len(text)
    lower = text.lower()
    url_lower = (url or "").lower()

    positive_signals: list[str] = []
    negative_signals: list[str] = []

    # Empty / near-empty.
    if stripped_len < 50:
        return {
            "level": "empty",
            "score": 0,
            "positive_signals": [],
            "negative_signals": ["markdown_empty_or_too_short"],
            "reason": "Markdown is empty or shorter than 50 characters.",
        }

    score = 0

    # Length contribution (up to 40).
    length_score = min(stripped_len / 8.0, 40.0)
    score += length_score
    if stripped_len >= 600:
        positive_signals.append(f"substantial_content_length:{stripped_len}")

    # Structure contribution (up to 20).
    struct = _count_structural_elements(text)
    struct_score = min(
        (struct["headings"] * 2 + struct["lists"] * 1 + struct["paragraphs"] * 1), 20
    )
    score += struct_score
    if struct["headings"]:
        positive_signals.append(f"has_headings:{struct['headings']}")
    if struct["paragraphs"]:
        positive_signals.append(f"has_paragraphs:{struct['paragraphs']}")

    # Keyword contribution (up to 20).
    keyword_hits = [kw for kw in _POSITIVE_KEYWORDS if kw in lower]
    keyword_score = min(len(keyword_hits) * 3, 20)
    score += keyword_score
    positive_signals.extend([f"keyword:{kw}" for kw in keyword_hits])

    # Login keywords: soft penalty only (10).  A login gate with real business
    # content should remain score-able; an empty auth page will already be
    # caught by the empty threshold or score below 40.
    login_hits = [kw for kw in _LOGIN_KEYWORDS if kw in lower or kw in url_lower]
    if login_hits:
        score -= 10
        negative_signals.append(f"login_keyword:{login_hits[0]}")

    # Cloud/WAF/error/placeholder keywords: strong penalty (25).
    block_hits = [kw for kw in _NEGATIVE_KEYWORDS if kw in lower]
    if block_hits:
        score -= 25
        negative_signals.append(f"error_or_placeholder_page:{block_hits[0]}")

    # Penalise very short pages that made it past the empty threshold.
    if stripped_len < 200:
        score -= 20
        negative_signals.append("very_short_content")

    score = int(max(0, min(100, score)))

    # Level thresholds (login keywords no longer force "low").
    if stripped_len < 50:
        level = "empty"
    elif score < 40 or block_hits:
        level = "low"
    elif score < 70:
        level = "medium"
    else:
        level = "high"

    reason = (
        f"score={score}, length={stripped_len}, "
        f"structure={struct}, keywords={len(keyword_hits)}"
    )
    if negative_signals:
        reason += f", negatives={negative_signals}"

    return {
        "level": level,
        "score": score,
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
        "reason": reason,
    }


def content_quality(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: evaluate the quality of the crawled markdown."""
    quality = _evaluate_quality(
        state.get("markdown"),
        state.get("selected_url"),
    )

    new_state = add_trace(
        state,
        node="content_quality",
        input_data={
            "markdown_length": len((state.get("markdown") or "").strip()),
            "selected_url": state.get("selected_url"),
        },
        output_data=quality,
    )
    new_state["content_quality"] = quality
    return new_state
