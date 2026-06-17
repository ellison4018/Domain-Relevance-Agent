"""normalize_domain node.

Pure functions:
    _strip_domain(raw) -> apex
    _normalize(raw)    -> {domain, apex, variants}
    normalize_domain(state) -> state with normalized + trace
"""

from urllib.parse import urlparse

from app.graph.state import DomainGraphState, add_trace


def _strip_domain(raw: str) -> str:
    """Return the apex host from a raw domain/url string.

    Examples:
        https://www.Example.com/path?q=1 -> example.com
        http://api.example.com:8080/     -> api.example.com
        www.foo.bar/baz                  -> foo.bar
    """
    text = raw.strip().lower()

    # If there is no scheme, urlparse treats the whole string as a path.
    # Prepend a dummy scheme so that hostname parsing works consistently.
    if "://" not in text:
        text = "http://" + text

    parsed = urlparse(text)
    host = parsed.hostname
    if host is None:
        # Fallback: take the part before the first slash or question mark.
        host = raw.lower().strip().split("/")[0].split("?")[0]
        if ":" in host:
            host = host.rsplit(":", 1)[0]

    # Remove a leading 'www.' to get the apex.  Use removeprefix so that
    # subdomains like 'www2.example.com' are preserved.
    host = host.removeprefix("www.")
    return host


def _normalize(raw: str) -> dict:
    """Produce the normalized record used by downstream nodes."""
    apex = _strip_domain(raw)
    variants = [
        f"https://{apex}",
        f"http://{apex}",
        f"https://www.{apex}",
        f"http://www.{apex}",
    ]
    return {"domain": raw, "apex": apex, "variants": variants}


def normalize_domain(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: clean domain and generate probe variants."""
    normalized = _normalize(state["domain"])
    new_state = add_trace(
        state,
        node="normalize_domain",
        input_data={"domain": state["domain"]},
        output_data=normalized,
    )
    new_state["normalized"] = normalized
    return new_state
