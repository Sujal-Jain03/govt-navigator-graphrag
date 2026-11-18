"""Phase 3 runner: extractions -> resolved graph -> disk.

Outputs (processed_data/graph/):
- graph.json: the full NetworkX graph (node-link format, reloadable)
- graph_stats.json: node/edge counts + build diagnostics
- community_summaries.json: one template summary per notification
"""
import json

import networkx as nx

from app.config import PROCESSED_DIR
from app.graph.builder import build_all
from app.graph.normalize import set_doc_canon_overrides
from app.graph.resolver import derive_doc_exams, load_extractions, resolve_entities

GRAPH_DIR = PROCESSED_DIR / "graph"


def run():
    """Build the graph from extractions. Returns the stats dict."""
    extractions = load_extractions(PROCESSED_DIR / "extractions")
    print(f"Loaded {len(extractions)} extraction files.")
    overrides = derive_doc_exams(extractions)
    if overrides:
        set_doc_canon_overrides(overrides)
        print(f"Derived canonical exams for new documents: {overrides}")
        print("  (add DOC_EXAMS entries in app/graph/normalize.py to pin these down)")
    nodes, chunk_name_to_id, doc_name_to_id, resolve_stats = resolve_entities(
        extractions
    )
    print(
        f"Resolved {resolve_stats['raw_entities']} raw entities into "
        f"{len(nodes)} canonical nodes "
        f"(dropped off-schema: {resolve_stats['dropped_off_schema'] or 'none'})."
    )
    graph, edges, stats, summaries = build_all(
        extractions, nodes, chunk_name_to_id, doc_name_to_id
    )
    stats["dropped_off_schema_entities"] = resolve_stats["dropped_off_schema"]
    stats["derived_doc_exams"] = overrides
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    (GRAPH_DIR / "graph.json").write_text(
        # Pin edges="links": networkx >= 3.6 changed the default key
        # to "edges", but every loader reads edges="links". Without
        # this, a networkx upgrade would silently break the API,
        # eligibility, matching and retrieval after the next rebuild.
        json.dumps(nx.node_link_data(graph, edges="links"), indent=2),
        encoding="utf-8",
    )
    (GRAPH_DIR / "graph_stats.json").write_text(
        json.dumps(stats, indent=2), encoding="utf-8"
    )
    (GRAPH_DIR / "community_summaries.json").write_text(
        json.dumps(summaries, indent=2), encoding="utf-8"
    )
    print(
        f"Saved graph.json ({graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges), graph_stats.json, "
        f"community_summaries.json to {GRAPH_DIR}"
    )
    print(json.dumps(stats, indent=2))
    return stats
