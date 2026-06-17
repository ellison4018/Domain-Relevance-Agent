"""Crawl4AI client wrapper.

This module isolates the rest of the codebase from the Crawl4AI API.  Business
nodes call :func:`crawl_url` and receive a plain dict shaped like:

    {
        "url": str,
        "success": bool,
        "error": str | None,
        "raw_markdown": str,
        "fit_markdown": str,
        "title": str | None,
        "metadata": dict,
    }

If the optional ``crawl4ai`` package is installed, a single
:class:`crawl4ai.AsyncWebCrawler` instance is created in a background event-loop
thread and reused for every call in the process.  This avoids the expensive
per-domain browser init.  If ``crawl4ai`` is not available, the function falls
back to a lightweight ``requests`` fetch so that tests and local exploration work
without the heavy dependency.
"""

from __future__ import annotations

import asyncio
import atexit
import re
import threading
from typing import Any, Optional

import requests


try:  # pragma: no cover - environment dependent
    from crawl4ai import AsyncWebCrawler  # type: ignore

    _crawl4ai_available = True
except Exception:  # noqa: BLE001
    _crawl4ai_available = False


class _NoCrawl4AI:
    """Sentinel so we only attempt the optional import once per process."""

    available = False

    @staticmethod
    def extract_title(html: str) -> Optional[str]:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None

    @staticmethod
    def html_to_markdown(html: bytes) -> str:
        """Naive fallback: strip tags and return readable text."""
        text = html.decode("utf-8", errors="ignore")
        # Replace common block tags with newlines.
        text = re.sub(r"</(p|div|h[1-6]|li|tr|pre|blockquote)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        # Collapse whitespace.
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


def _fallback_crawl(url: str, timeout: float = 30.0) -> dict[str, Any]:
    """Lightweight fallback when Crawl4AI is not installed."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "Domain-Relevance-Agent/0.2"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        html_bytes = resp.content
        raw_md = _NoCrawl4AI.html_to_markdown(html_bytes)
        title = _NoCrawl4AI.extract_title(html_bytes.decode("utf-8", errors="ignore"))
        return {
            "url": url,
            "success": True,
            "error": None,
            "raw_markdown": raw_md,
            "fit_markdown": raw_md,
            "title": title,
            "metadata": {
                "status_code": resp.status_code,
                "final_url": resp.url,
                "content_type": resp.headers.get("Content-Type"),
            },
        }
    except Exception as exc:  # pragma: no cover - network path
        return {
            "url": url,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "raw_markdown": "",
            "fit_markdown": "",
            "title": None,
            "metadata": {},
        }


class _SharedCrawler:
    """One AsyncWebCrawler living in a dedicated event-loop-backed thread.

    The browser / playwright init is performed exactly once per process.  All
    ``crawl_url`` calls submit work to this thread and block on a Future result.
    """

    def __init__(self, init_timeout: float = 60.0):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._crawler: Any = None
        self._init_exc: Exception | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=init_timeout):
            raise RuntimeError("Crawl4AI background thread did not start in time")
        if self._init_exc is not None:
            raise self._init_exc

    def _worker(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._crawler = loop.run_until_complete(self._start())
        except Exception as exc:  # noqa: BLE001
            self._init_exc = exc
            self._ready.set()
            return
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    async def _start(self) -> Any:
        crawler = AsyncWebCrawler()  # type: ignore
        await crawler.start()
        return crawler

    @staticmethod
    def _result_to_dict(url: str, result: Any) -> dict[str, Any]:
        md = result.markdown
        metadata = getattr(result, "metadata", None) or {}
        return {
            "url": url,
            "success": result.success,
            "error": result.error_message if not result.success else None,
            "raw_markdown": md.raw_markdown if md else "",
            "fit_markdown": md.fit_markdown if md else md.raw_markdown if md else "",
            "title": metadata.get("title"),
            "metadata": dict(metadata),
        }

    async def _do_crawl(self, url: str) -> dict[str, Any]:
        result = await self._crawler.arun(url=url)
        return self._result_to_dict(url, result)

    def crawl(self, url: str, timeout: float = 60.0) -> dict[str, Any]:
        if self._loop is None:  # pragma: no cover - init failure path
            return _fallback_crawl(url)
        future = asyncio.run_coroutine_threadsafe(self._do_crawl(url), self._loop)
        try:
            return future.result(timeout=timeout)
        except Exception as exc:  # pragma: no cover - network/runtime path
            return {
                "url": url,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "raw_markdown": "",
                "fit_markdown": "",
                "title": None,
                "metadata": {},
            }

    def close(self) -> None:
        """Shut down the background crawler and event loop."""
        if self._loop is None or self._crawler is None:
            return

        async def _close() -> None:
            try:
                await self._crawler.close()
            except Exception:  # noqa: BLE001
                pass
            self._loop.stop()

        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=20)
        except Exception:  # noqa: BLE001
            pass
        self._thread.join(timeout=10)


_shared_crawler: _SharedCrawler | None = None


def _ensure_shared() -> _SharedCrawler:
    """Return the singleton shared crawler, creating it on first call."""
    global _shared_crawler
    if _shared_crawler is None:
        _shared_crawler = _SharedCrawler()
        atexit.register(_shared_crawler.close)
    return _shared_crawler


def crawl_url(url: str) -> dict[str, Any]:
    """Crawl *url* and return a plain CrawlResult dict.

    The returned dict is always JSON-serializable and never raises for network
    failures; failures are represented by ``success=False`` and an ``error``
    string.
    """
    if not _crawl4ai_available:
        return _fallback_crawl(url)
    return _ensure_shared().crawl(url)
