"""Graph retrieval: match query entities, expand, return facts.

Deterministic (no LLM). Matching is deliberately strict, because
loose matching was measured to misfire on this graph:
- display names match as WHOLE words ("service" must not match
  "civil services"), parentheticals stripped, min length 4;
- aliases resolve by EXACT display name ("rrb" must not match
  "RRB Ajmer", "upsc" must not match "https://upsconline.nic.in");
- only EXAM and POST nodes are matched. ORG nodes are not: matching
  "IBPS" would pull in both IBPS exams' facts, and CONDUCTED_BY is
  already reachable from a matched exam.

Expansion is 2 hops, but the 2nd hop goes ONLY through EXAM/POST
intermediate nodes (they are document-scoped, so the 2nd hop stays
inside the same notification). A 2nd hop through a shared node
(AGE_LIMIT / DATE / DOCUMENT / QUALIFICATION / ORG) would pull in
other exams' facts. Hop 2 takes out-edges only: an exam reached in
hop 1 contributes its dates/fees/posts, a post contributes its
qualification / age limit / documents. This is what makes
"age limit for IBPS PO" answerable: the age lives on the POST,
one hop past the EXAM.
"""
import json
import re

from networkx.readwrite import json_graph

from app.config import PROCESSED_DIR

GRAPH_PATH = PROCESSED_DIR / "graph" / "graph.json"

# Shorthand (whole-word matched in the query) -> exact display names.
ALIASES = {
    "ias": ("indian administrative service",),
    "ips": ("indian police service",),
    "ifs": ("indian foreign service",),
    "irs": ("indian revenue service",),
    "upsc": ("civil services examination", "indian forest service examination"),
    "cse": ("civil services examination",),
    "cgl": ("ssc cgl",),
    "chsl": ("ssc chsl",),
    "rrb": ("rrb ntpc",),
    "sbi": ("sbi po",),
    "rbi": ("rbi grade b",),
}

MATCH_TYPES = {"EXAM", "POST"}

# Display order for facts: the exam's own facts first, documents last
# (there are many of them and they would otherwise flood the list).
RELATION_ORDER = {
    "CONDUCTED_BY": 0,
    "OFFERS": 1,
    "LAST_DATE": 2,
    "EXAM_DATE": 3,
    "APPLICATION_FEE": 4,
    "REQUIRES": 5,
    "HAS_AGE_LIMIT": 6,
    "REQUIRES_DOCUMENT": 7,
}

_graph = None


def load_graph():
    global _graph
    if _graph is None:
        data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        _graph = json_graph.node_link_graph(data, directed=True, multigraph=True, edges="links")
    return _graph


def _display_variants(display_name):
    """Display name + parenthetical-stripped form, lowercased."""
    name = display_name.lower().strip()
    out = {name}
    stripped = re.sub(r"\([^)]*\)", "", name).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    if stripped:
        out.add(stripped)
    return out


def _whole_word(haystack, needle):
    """True if needle appears as a whole word/phrase (lookarounds, not
    \\b, so names ending in punctuation still match)."""
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def match_entities(graph, query):
    """Node ids whose display name (or alias) matches the query."""
    q = query.lower()
    matched = set()
    for node_id, data in graph.nodes(data=True):
        if data.get("type") not in MATCH_TYPES:
            continue
        for variant in _display_variants(data.get("display_name", "")):
            if len(variant) >= 4 and _whole_word(q, variant):
                matched.add(node_id)
                break
    for alias, display_names in ALIASES.items():
        if not _whole_word(q, alias):
            continue
        for node_id, data in graph.nodes(data=True):
            if data.get("type") not in MATCH_TYPES:
                continue
            if data.get("display_name", "").lower() in display_names:
                matched.add(node_id)
    return matched


def _add_fact(facts, graph, head_id, tail_id, data, hop):
    head = graph.nodes[head_id].get("display_name", head_id)
    tail = graph.nodes[tail_id].get("display_name", tail_id)
    key = (head, data.get("relation"), tail)
    if key in facts:
        if hop < facts[key]["hop"]:
            facts[key]["hop"] = hop
        return
    facts[key] = {
        "head": head,
        "relation": data.get("relation"),
        "tail": tail,
        "head_type": graph.nodes[head_id].get("type"),
        "tail_type": graph.nodes[tail_id].get("type"),
        "inferred": data.get("inferred"),
        "sources": data.get("sources", []),
        "hop": hop,
    }


def expand(graph, node_ids):
    """1-hop facts (both directions) for matched nodes, plus a 2nd hop
    of out-edges through EXAM/POST neighbours only.

    Deduped, then ordered: non-document facts first (hop 1, then hop 2),
    then document facts; within each group by relation, then alphabetically.
    """
    facts = {}
    frontier = set()
    for node_id in node_ids:
        if node_id not in graph:
            continue
        for _, target, data in graph.out_edges(node_id, data=True):
            _add_fact(facts, graph, node_id, target, data, hop=1)
            if graph.nodes[target].get("type") in ("EXAM", "POST"):
                frontier.add(target)
        for source, _, data in graph.in_edges(node_id, data=True):
            _add_fact(facts, graph, source, node_id, data, hop=1)
            if graph.nodes[source].get("type") in ("EXAM", "POST"):
                frontier.add(source)
    for node_id in frontier - set(node_ids):
        for _, target, data in graph.out_edges(node_id, data=True):
            _add_fact(facts, graph, node_id, target, data, hop=2)
    return sorted(
        facts.values(),
        key=lambda f: (1 if f["relation"] == "REQUIRES_DOCUMENT" else 0,
                       f["hop"], RELATION_ORDER.get(f["relation"], 99),
                       f["head"], f["tail"]),
    )
