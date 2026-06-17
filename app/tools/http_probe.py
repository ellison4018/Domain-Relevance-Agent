"""HTTP probe utilities.

Functions are pure side-effect wrappers around `requests`.  They are thin so
that unit tests can mock `requests.Session` or simply assert on returned dicts.
"""

import time
from typing import Optional

import requests


def probe_url(
    url: str,
    timeout: float = 5.0,
    body_preview_limit: int = 2048,
) -> dict:
    """Probe a single URL and return a structured result dict.

    Strategy:
      1. Try HEAD with redirects.
      2. If HEAD fails (method not allowed, network error, etc.), fall back to
         a streaming GET so that we do not download huge payloads.
      3. Capture a small body preview for downstream classifiers.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Domain-Relevance-Agent/0.1"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
    )

    result: dict = {
        "url": url,
        "status_code": None,
        "final_url": None,
        "response_time_ms": None,
        "headers": {},
        "content_length": None,
        "body_preview": None,
        "error": None,
    }

    start = time.perf_counter()
    try:
        try:
            resp = session.head(url, timeout=timeout, allow_redirects=True)
        except requests.exceptions.RequestException:
            resp = session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            )
        else:
            # Some endpoints accept HEAD but return a useless status (e.g. 405).
            # For classifiers we want a body preview, so fall back to GET.
            if resp.status_code in (405,):
                resp.close()
                resp = session.get(
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                    stream=True,
                )
            else:
                # We already have headers; still try a tiny GET for body preview.
                resp.close()
                resp = session.get(
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                    stream=True,
                )

        result["response_time_ms"] = round((time.perf_counter() - start) * 1000, 2)
        result["status_code"] = resp.status_code
        result["final_url"] = resp.url
        result["headers"] = dict(resp.headers)

        # Prefer Content-Length header; otherwise read a small preview.
        content_length_header = resp.headers.get("Content-Length")
        if content_length_header is not None:
            try:
                result["content_length"] = int(content_length_header)
            except ValueError:
                result["content_length"] = None

        if resp.raw is not None:
            content = b""
            try:
                for chunk in resp.iter_content(chunk_size=1024):
                    if not chunk:
                        continue
                    content += chunk
                    if len(content) >= body_preview_limit:
                        break
            finally:
                resp.close()

            if result["content_length"] is None:
                result["content_length"] = len(content)
            result["body_preview"] = content[:body_preview_limit].decode(
                "utf-8", errors="ignore"
            )

    except Exception as exc:  # pragma: no cover - broad catch for robustness
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        session.close()

    return result


def probe_domain_variants(
    apex: str,
    timeout: float = 5.0,
) -> list[dict]:
    """Probe the four canonical variants for an apex domain."""
    variants = [
        f"https://{apex}",
        f"http://{apex}",
        f"https://www.{apex}",
        f"http://www.{apex}",
    ]
    return [probe_url(v, timeout=timeout) for v in variants]
