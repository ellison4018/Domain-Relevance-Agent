"""LLM client for 百炼 (Bailian) OpenAI-compatible API.

Reads ``DASHSCOPE_API_KEY`` from the project root ``.env`` file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


# Load .env from project root (two levels up from this file: app/tools -> app -> root)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


try:
    import openai
except ImportError as exc:  # pragma: no cover - defensive
    raise ImportError("openai SDK is required; run: pip install openai") from exc


BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen3.7-plus")

_client: openai.OpenAI | None = None


def get_client() -> openai.OpenAI:
    """Return a cached synchronous OpenAI client pointing at Bailian."""
    global _client
    if _client is None:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set. Please copy .env.example to .env and fill in your key.")
        _client = openai.OpenAI(
            api_key=api_key,
            base_url=BASE_URL,
        )
    return _client


def complete_json(
    prompt: str,
    *,
    model: str = MODEL,
    temperature: float = 0.0,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Call the LLM with ``response_format={"type":"json_object"}`` and parse JSON.

    On API or JSON failure (after retries), returns a dict with
    ``_llm_failed: True`` and ``error`` so downstream nodes can route to
    ``uncertain`` safely.
    """
    client = get_client()
    messages = [{"role": "user", "content": prompt}]

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                raise ValueError("LLM returned empty content")
            return json.loads(content)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break

    return {
        "_llm_failed": True,
        "error": str(last_exc),
    }
