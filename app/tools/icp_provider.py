"""ICP provider for the local ymicp API.

The local executable ``icp_query/icpApi.exe`` exposes an HTTP API on
``127.0.0.1:16181``.  This module isolates the rest of the codebase from its
request/response shape.
"""

from __future__ import annotations

from typing import Any

import requests


_ICP_API_BASE = "http://127.0.0.1:16181"
_DEFAULT_TIMEOUT = 15.0


def query_icp(domain: str, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Query MIIT/ICP registration info for *domain*.

    Returns a normalized dict that is always JSON-serializable and never raises:

        {
          "success": bool,
          "error": str | None,
          "domain": str,
          "records": [
            {
              "domain": str,
              "unit_name": str,
              "nature_name": str,
              "main_licence": str,
              "service_licence": str,
              "update_record_time": str,
            }
          ]
        }

    An empty ``list`` from the upstream API is represented as ``success=True``
    and ``records=[]`` — it is a valid "no record" result, not an error.
    """
    url = (
        f"{_ICP_API_BASE}/query/web"
        f"?search={requests.utils.quote(domain)}&pageNum=1&pageSize=10"
    )

    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "domain": domain,
        "records": [],
    }

    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    if not isinstance(data, dict):
        result["error"] = "unexpected ICP API response type"
        return result

    if data.get("code") != 200 or not data.get("success"):
        msg = data.get("msg") or "ICP API returned non-success code"
        result["error"] = msg
        return result

    params = data.get("params") or {}
    records = params.get("list") or []

    normalized: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        normalized.append(
            {
                "domain": rec.get("domain") or "",
                "unit_name": rec.get("unitName") or "",
                "nature_name": rec.get("natureName") or "",
                "main_licence": rec.get("mainLicence") or "",
                "service_licence": rec.get("serviceLicence") or "",
                "update_record_time": rec.get("updateRecordTime") or "",
            }
        )

    result["success"] = True
    result["records"] = normalized
    return result
