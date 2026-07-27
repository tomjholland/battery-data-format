"""Canonical-graph comparison for the RDF round-trip tests.

The graph contract is RDF equivalence, never byte identity: two serialisations
of the same graph differ in triple order and formatting, and comparing their
text is a test that fails for no reason. This lives in the test tree rather than
in ``bdf`` because nothing the package ships needs it — it is how the tests ask
"are these the same graph?", not part of the published format surface.
"""

from __future__ import annotations

from rdflib import Graph
from rdflib.compare import to_canonical_graph


def canonical_nquads(graph: Graph) -> str:
    """Return ``graph`` as canonical, sorted N-Quads.

    Blank nodes are canonically relabelled first, then the lines are sorted, so
    the output depends only on the graph's content.

    The projection emits no blank nodes at all — every node is named
    (decision 14) — so on the documents this package writes the canonical
    relabelling is a no-op and this output is exactly URDNA2015's canonical
    N-Quads. For a graph that does carry blank nodes, the labels are rdflib's
    deterministic hashes rather than URDNA2015's ``_:c14n`` series; the
    equivalence relation is the same, the label text is not. A single graph
    holds only a default graph, whose N-Quads lines are by definition its
    N-Triples lines, so that is what a plain ``Graph`` serialises to.

    Args:
        graph: The graph to canonicalise.

    Returns:
        The sorted canonical N-Quads text.
    """
    canonical = to_canonical_graph(graph)
    fmt = "application/n-quads" if getattr(canonical, "context_aware", False) else "application/n-triples"
    lines = canonical.serialize(format=fmt).splitlines()
    return "\n".join(sorted(line for line in lines if line.strip()))


def graphs_equivalent(left: Graph, right: Graph) -> bool:
    """Report whether two graphs are RDF-equivalent.

    Args:
        left: First graph.
        right: Second graph.

    Returns:
        True when both canonicalise to the same N-Quads.
    """
    return canonical_nquads(left) == canonical_nquads(right)
