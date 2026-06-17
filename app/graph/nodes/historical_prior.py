"""Historical prior node.

Looks up the domain in the historical domain library and returns a prior score
with match metadata. No LLM is used here.
"""

from __future__ import annotations

from app.graph.state import DomainGraphState, add_trace
from app.tools.historical_domain_loader import find_historical_match, load_library


_LIBRARY = None


def _get_library():
    """Lazy-load and cache the historical library for the process."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = load_library()
    return _LIBRARY


def historical_prior(state: DomainGraphState) -> DomainGraphState:
    """LangGraph node: compute historical-prior evidence for the domain."""
    normalized = state.get("normalized") or {}
    domain = normalized.get("apex") or state.get("domain") or ""

    result = find_historical_match(domain, _get_library())

    new_state = add_trace(
        state,
        node="historical_prior",
        input_data={"domain": domain},
        output_data=result,
    )
    new_state["historical_prior"] = result
    return new_state
