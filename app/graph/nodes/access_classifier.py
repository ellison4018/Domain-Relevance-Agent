"""classify_access_status node.

Pure classifier that maps http_probe results into one of:
    reachable, cloud_error, unreachable, login_only, weak_content, unknown
"""

from app.graph.state import DomainGraphState, add_trace


# Headers / body keywords that indicate a cloud/WAF/cdn error page.
_CLOUD_SIGNATURES = [
    "cloudflare",
    "akamai",
    "aws",
    "amazon cloudfront",
    "aliyun",
    "tencent cloud",
    "waf",
    "cdn",
    "server: awselb",
]

# Body keywords that suggest the page is an auth gate.
_LOGIN_SIGNATURES = [
    "login",
    "sign in",
    "signin",
    "password",
    "用户名",
    "密码",
    "登录",
    "认证",
    "authentication",
    "sso",
]


def _has_cloud_signature(result: dict) -> bool:
    """Detect cloud-provider/WAF error pages from headers or body preview."""
    headers_text = " ".join(
        f"{k.lower()}: {str(v).lower()}" for k, v in result.get("headers", {}).items()
    )
    body = (result.get("body_preview") or "").lower()
    combined = f"{headers_text} {body}"
    return any(sig in combined for sig in _CLOUD_SIGNATURES)


def _has_login_signature(result: dict) -> bool:
    """Detect login-only gates."""
    status = result.get("status_code")
    if status not in (401, 403):
        return False
    body = (result.get("body_preview") or "").lower()
    return any(sig in body for sig in _LOGIN_SIGNATURES)


def _is_success(result: dict) -> bool:
    status = result.get("status_code")
    return status is not None and 200 <= status < 400


def _is_client_or_server_error(result: dict) -> bool:
    status = result.get("status_code")
    return status is not None and status >= 400


def _classify_access_status(results: list[dict] | None) -> str:
    """Classify a list of probe results."""
    if not results:
        return "unknown"

    # 1. Reachable: at least one variant returns a successful status.
    successes = [r for r in results if _is_success(r)]
    if successes:
        # If every successful response is empty / zero-length, treat as weak content.
        if all(
            (r.get("content_length") == 0)
            or not (r.get("body_preview") or "").strip()
            for r in successes
        ):
            return "weak_content"
        return "reachable"

    # 2. Login-only: at least one variant looks like an auth gate.
    login_only = [r for r in results if _has_login_signature(r)]
    if login_only:
        return "login_only"

    # 3. Cloud error: WAF / CDN / cloud provider block page.
    cloud_errors = [r for r in results if _has_cloud_signature(r)]
    if cloud_errors:
        return "cloud_error"

    # 4. Unreachable: every variant has a network-level error.
    errors = [r for r in results if r.get("error")]
    if len(errors) == len(results):
        return "unreachable"

    # 5. Weak content: server errors but with tiny body (e.g. default 404 page).
    weak = [
        r
        for r in results
        if _is_client_or_server_error(r)
        and (
            (r.get("content_length") is not None and r["content_length"] < 200)
            or not (r.get("body_preview") or "").strip()
        )
    ]
    if weak:
        return "weak_content"

    return "unknown"


def classify_access_status(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: classify probe results into an access status."""
    status = _classify_access_status(state.get("probe_results"))
    new_state = add_trace(
        state,
        node="classify_access_status",
        input_data={"probe_results": state.get("probe_results")},
        output_data={"access_status": status},
    )
    new_state["access_status"] = status
    return new_state
