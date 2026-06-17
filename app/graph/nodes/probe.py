"""http_probe graph node.

This is a thin wrapper around :mod:`app.tools.http_probe` so that the graph
node remains easy to unit-test in isolation.
"""

from app.graph.state import DomainGraphState, add_trace
from app.tools.http_probe import probe_domain_variants


def http_probe_node(state: DomainGraphState) -> DomainGraphState:
    """Probe https/http + www/non-www variants of the normalized apex."""
    apex = state["normalized"]["apex"]
    results = probe_domain_variants(apex)

    new_state = add_trace(
        state,
        node="http_probe",
        input_data={"apex": apex},
        output_data={"variants_count": len(results)},
    )
    new_state["probe_results"] = results
    return new_state
