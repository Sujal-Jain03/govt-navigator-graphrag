"""Graph construction from resolved entities + chunk relations.

Four passes:
1. Chunk-level edges: each chunk's relations become edges between
   canonical nodes (chunk lookup first, per-document fallback).
   Relations whose head should be an EXAM (CONDUCTED_BY, LAST_DATE,
   EXAM_DATE, APPLICATION_FEE) but whose head is a POST are re-headed
   to the chunk's document canonical exam (DOC_EXAMS). Edges are
   deduplicated: the same (head, relation, tail) from many chunks
   becomes one edge with a count + chunk_refs.
2. Pruning: Phase 2 extraction has low precision on typing — the
   models label syllabus topics, medical conditions and test names
   as POST/QUALIFICATION/AGE_LIMIT. Pruning keeps only nodes that
   pass a type-specific plausibility rule (see normalize.py):
   POST must look like a job title, QUALIFICATION must be a known
   degree level, AGE_LIMIT must be a range/single bound with min >= 18
   (and not a relaxation clause), DOCUMENT must pass is_valid_document.
   Edges are validated against the schema's (head, tail) types, and
   CONDUCTED_BY edges are restricted to each document's own org (the
   models link exams to posts and random orgs as conductors). Edges
   touching pruned nodes are dropped. Everything pruned is counted
   in the stats; the raw data stays untouched in the extraction files.
3. Document-level attachment: for each notification, its kept
   QUALIFICATION / AGE_LIMIT entities are attached to its kept POSTs
   (or its EXAMs if it has no POSTs), marked
   inferred="document-level". This fixes the sparse-relations
   problem: those facts live in different sections of a notification
   and almost never share a chunk with their POST. DOCUMENTs attach
   to the EXAM instead (a notification's document list is one list
   per application, not per post — attaching it to every post would
   create posts x documents edges). DATE and FEE are deliberately
   NOT attached this way — a notification has many dates/fees and
   only the chunk text says which is the last date. OFFERS edges
   are deliberately NOT inferred: a notification mentions posts it
   does not offer (certificate-issuing authorities), so only
   chunk-level OFFERS edges count.
4. Community summaries: one per notification (each document is a
   community by construction). Template text built from the graph —
   no LLM calls. With 9 documents, a Leiden-style algorithm would
   just rediscover the documents; real community detection becomes
   meaningful when Phase 8 grows the corpus.
"""
from collections import Counter

import networkx as nx

from app.graph.normalize import (
    SCHEMA_RELATIONS,
    canonical_exam_for_doc,
    canonical_name,
    is_job_title,
    is_known_level,
    is_parseable_date,
    is_valid_age,
    is_valid_document,
    normalize_name,
)

# Relations whose head must be an EXAM per the Phase 2 schema.
EXAM_HEADED = {"CONDUCTED_BY", "LAST_DATE", "EXAM_DATE", "APPLICATION_FEE"}
# Entity types attached at document level to POSTs (see module docstring).
DOC_ATTACH_POSTS = {
    "QUALIFICATION": "REQUIRES",
    "AGE_LIMIT": "HAS_AGE_LIMIT",
}
# DOCUMENTs attach to the EXAM, not the posts: a notification's
# document list is one list for the whole application, and attaching
# it to every post would create posts x documents edges (20k+ here).
DOC_ATTACH_EXAMS = {"DOCUMENT": "REQUIRES_DOCUMENT"}

# Expected (head types, tail type) per relation. Edges violating the
# schema (e.g. REQUIRES pointing at a DOCUMENT) are dropped in prune.
RELATION_TYPES = {
    "CONDUCTED_BY": ({"EXAM"}, "ORG"),
    "OFFERS": ({"EXAM"}, "POST"),
    "REQUIRES": ({"POST", "EXAM"}, "QUALIFICATION"),
    "HAS_AGE_LIMIT": ({"POST", "EXAM"}, "AGE_LIMIT"),
    "LAST_DATE": ({"EXAM"}, "DATE"),
    "EXAM_DATE": ({"EXAM"}, "DATE"),
    "APPLICATION_FEE": ({"EXAM"}, "FEE"),
    "REQUIRES_DOCUMENT": ({"POST", "EXAM"}, "DOCUMENT"),
}


def _edge_key(head, relation, tail):
    return (head, relation, tail)


def _lookup(name, stem, chunk_index, chunk_name_to_id, doc_name_to_id):
    """Resolve a relation endpoint name to a node id (chunk, then doc)."""
    key = normalize_name(name)
    nid = chunk_name_to_id.get((stem, chunk_index), {}).get(key)
    if nid is None:
        nid = doc_name_to_id.get(stem, {}).get(key)
    return nid


def build_chunk_edges(extractions, nodes, chunk_name_to_id, doc_name_to_id):
    """Returns (edges, stats). edges: {key: edge-dict}.

    POST-headed EXAM relations are re-headed to the chunk's document
    canonical exam (every document's exam is known — see DOC_EXAMS).
    """
    raw_relations = []  # (stem, chunk_index, head, relation, tail)
    dropped_off_schema = Counter()
    for stem, data in extractions.items():
        for chunk_key, result in (data.get("results") or {}).items():
            if result.get("error"):
                continue
            chunk_index = int(chunk_key)
            for rel in result.get("relations") or []:
                if rel["relation"] not in SCHEMA_RELATIONS:
                    dropped_off_schema[rel["relation"]] += 1
                    continue
                raw_relations.append(
                    (stem, chunk_index, rel["head"], rel["relation"], rel["tail"])
                )
    edges = {}
    stats = {
        "raw_relations": len(raw_relations) + sum(dropped_off_schema.values()),
        "dropped_off_schema_relations": dict(dropped_off_schema),
        "orphan_dropped": 0,
        "reheaded": 0,
        "rehead_failed_kept": 0,
    }
    for stem, chunk_index, head_name, relation, tail_name in raw_relations:
        head_id = _lookup(
            head_name, stem, chunk_index, chunk_name_to_id, doc_name_to_id
        )
        tail_id = _lookup(
            tail_name, stem, chunk_index, chunk_name_to_id, doc_name_to_id
        )
        if not head_id or not tail_id:
            stats["orphan_dropped"] += 1
            continue
        if relation in EXAM_HEADED and nodes[head_id]["type"] == "POST":
            exam_id = f"EXAM:{stem}:{canonical_exam_for_doc('', stem)}"
            if exam_id in nodes:
                head_id = exam_id
                stats["reheaded"] += 1
            else:
                stats["rehead_failed_kept"] += 1
        key = _edge_key(head_id, relation, tail_id)
        edge = edges.get(key)
        if edge is None:
            edges[key] = {
                "head": head_id,
                "tail": tail_id,
                "relation": relation,
                "inferred": "chunk-level",
                "count": 1,
                "chunk_refs": [(stem, chunk_index)],
                "sources": [stem],
            }
        else:
            edge["count"] += 1
            edge["chunk_refs"].append((stem, chunk_index))
            if stem not in edge["sources"]:
                edge["sources"].append(stem)
    return edges, stats


def _top_org_per_doc(nodes, extractions):
    """Each document's own org = its most-mentioned ORG (by chunk refs)."""
    top = {}
    for stem in extractions:
        best, best_n = None, -1
        for n in nodes.values():
            if n["type"] != "ORG":
                continue
            refs_here = sum(1 for (s, _) in n["chunk_refs"] if s == stem)
            if refs_here > best_n:
                best, best_n = n, refs_here
        if best is not None:
            top[stem] = canonical_name("ORG", best["display_name"])
    return top


def _keep_node(node):
    """Pruning rule per type (see module docstring)."""
    canon = canonical_name(node["type"], node["display_name"])
    if node["type"] == "POST":
        return is_job_title(canon)
    if node["type"] == "QUALIFICATION":
        return is_known_level(canon)
    if node["type"] == "AGE_LIMIT":
        # Relaxation clauses ("up to 5 years relaxation for SC/ST")
        # canonicalize into fake ranges — drop them; the base limit
        # is a separate node. Relaxation modeling belongs to Phase 5.
        if any("relax" in v.lower() for v in node["variants"]):
            return False
        return is_valid_age(canon)
    if node["type"] == "DOCUMENT":
        return is_valid_document(canon)
    return True


def prune_nodes(nodes, edges, extractions):
    """Drop implausible nodes + their edges; validate edge types;
    restrict CONDUCTED_BY to each doc's own org; drop LAST_DATE /
    EXAM_DATE edges pointing at unparseable dates.

    Returns (kept_nodes, kept_edges, prune_stats).
    """
    top_org = _top_org_per_doc(nodes, extractions)
    kept_nodes = {}
    pruned_by_type = Counter()
    for nid, n in nodes.items():
        if _keep_node(n):
            kept_nodes[nid] = n
        else:
            pruned_by_type[n["type"]] += 1
    kept_edges = {}
    dropped_edge_nodes = 0
    dropped_conducted_by = 0
    dropped_type_mismatch = 0
    dropped_unparseable_date = 0
    for key, e in edges.items():
        if e["head"] not in kept_nodes or e["tail"] not in kept_nodes:
            dropped_edge_nodes += 1
            continue
        head_types, tail_type = RELATION_TYPES[e["relation"]]
        if (
            kept_nodes[e["head"]]["type"] not in head_types
            or kept_nodes[e["tail"]]["type"] != tail_type
        ):
            dropped_type_mismatch += 1
            continue
        if e["relation"] in ("LAST_DATE", "EXAM_DATE"):
            tail_canon = canonical_name(
                "DATE", kept_nodes[e["tail"]]["display_name"]
            )
            if not is_parseable_date(tail_canon):
                dropped_unparseable_date += 1
                continue
        if e["relation"] == "CONDUCTED_BY":
            tail_canon = canonical_name("ORG", kept_nodes[e["tail"]]["display_name"])
            tail_words = set(tail_canon.split())
            ok = False
            for stem in e["sources"]:
                top = top_org.get(stem)
                if top:
                    top_words = set(top.split())
                    if tail_words <= top_words or top_words <= tail_words:
                        ok = True
                        break
            if not ok:
                dropped_conducted_by += 1
                continue
        kept_edges[key] = e
    prune_stats = {
        "nodes_pruned_by_type": dict(pruned_by_type),
        "nodes_kept": len(kept_nodes),
        "edges_dropped_touching_pruned_nodes": dropped_edge_nodes,
        "edges_dropped_type_mismatch": dropped_type_mismatch,
        "edges_dropped_unparseable_date": dropped_unparseable_date,
        "conducted_by_edges_dropped_not_own_org": dropped_conducted_by,
    }
    return kept_nodes, kept_edges, prune_stats


def attach_document_level(extractions, nodes, edges):
    """Document-level inference (see module docstring).

    - QUALIFICATION / AGE_LIMIT attach to each doc's POSTs (or its
      EXAMs if it has no POSTs).
    - DOCUMENT attaches to each doc's EXAMs only (the document list
      is one list per application, not per post).

    NOTE: OFFERS edges are NOT inferred here. A notification mentions
    many posts it does not offer (certificate-issuing authorities
    like Deputy Commissioner, Tehsildar), so document membership
    does not imply offered-by. Only chunk-level OFFERS edges count.

    Returns (added, skipped). Mutates `edges`.
    """
    added = skipped = 0

    def _add(head, relation, tail, stem):
        nonlocal added, skipped
        key = _edge_key(head, relation, tail)
        if key in edges:
            skipped += 1
        else:
            edges[key] = {
                "head": head,
                "tail": tail,
                "relation": relation,
                "inferred": "document-level",
                "count": 1,
                "chunk_refs": [],
                "sources": [stem],
            }
            added += 1

    for stem in extractions:
        doc_nodes = [n for n in nodes.values() if stem in n["sources"]]
        posts = [n["id"] for n in doc_nodes if n["type"] == "POST"]
        exams = [n["id"] for n in doc_nodes if n["type"] == "EXAM"]
        if not exams and not posts:
            continue
        targets = posts if posts else exams
        for ent_type, relation in DOC_ATTACH_POSTS.items():
            for n in doc_nodes:
                if n["type"] != ent_type:
                    continue
                for target in targets:
                    _add(target, relation, n["id"], stem)
        for ent_type, relation in DOC_ATTACH_EXAMS.items():
            for n in doc_nodes:
                if n["type"] != ent_type:
                    continue
                for exam in exams:
                    _add(exam, relation, n["id"], stem)
    return added, skipped


def build_community_summaries(extractions, nodes, edges):
    """One summary per notification. Template text from graph facts.

    The post list comes from OFFERS edges only (a notification
    mentions posts it does not offer — e.g. certificate-issuing
    authorities — so document membership is not enough). Facts are
    collected from the exam's and all its posts' edges, with the
    tail type checked per relation.
    """
    summaries = {}
    edge_list = list(edges.values())
    for stem in extractions:
        doc_nodes = [n for n in nodes.values() if stem in n["sources"]]
        exams = [n for n in doc_nodes if n["type"] == "EXAM"]
        posts = [n for n in doc_nodes if n["type"] == "POST"]
        orgs = [n for n in doc_nodes if n["type"] == "ORG"]
        exam_ids = {n["id"] for n in exams}
        heads = exam_ids | {n["id"] for n in posts}
        offered = {
            e["tail"]
            for e in edge_list
            if e["relation"] == "OFFERS" and e["head"] in exam_ids
        }
        last_dates, exam_dates, fees, quals, ages, docs = [], [], [], [], [], []
        for e in edge_list:
            if e["head"] not in heads:
                continue
            tail = nodes[e["tail"]]
            tail_name = tail["display_name"]
            if e["relation"] == "LAST_DATE" and tail["type"] == "DATE":
                last_dates.append(tail_name)
            elif e["relation"] == "EXAM_DATE" and tail["type"] == "DATE":
                exam_dates.append(tail_name)
            elif e["relation"] == "APPLICATION_FEE" and tail["type"] == "FEE":
                fees.append(tail_name)
            elif e["relation"] == "REQUIRES" and tail["type"] == "QUALIFICATION":
                quals.append(tail_name)
            elif e["relation"] == "HAS_AGE_LIMIT" and tail["type"] == "AGE_LIMIT":
                ages.append(tail_name)
            elif e["relation"] == "REQUIRES_DOCUMENT" and tail["type"] == "DOCUMENT":
                docs.append(tail_name)
        exam_names = sorted({e["display_name"] for e in exams})
        # The conducting org comes from the graph's CONDUCTED_BY edge,
        # falling back to the doc's most-mentioned ORG.
        org_name = "Unknown org"
        for e in edge_list:
            if e["relation"] == "CONDUCTED_BY" and e["head"] in exam_ids:
                org_name = nodes[e["tail"]]["display_name"]
                break
        if org_name == "Unknown org" and orgs:
            org_name = max(orgs, key=lambda n: len(n["chunk_refs"]))["display_name"]
        post_names = sorted({nodes[p]["display_name"] for p in offered})

        def _join(xs):
            return ", ".join(sorted(set(xs))) or "not stated"

        text = (
            f"{', '.join(exam_names) or stem} is conducted by {org_name}. "
            f"Posts: {_join(post_names)}. "
            f"Last date(s): {_join(last_dates)}. "
            f"Age limit(s): {_join(ages)}. "
            f"Qualification(s): {_join(quals)}."
        )
        summaries[stem] = {
            "exams": exam_names,
            "org": org_name,
            "posts": post_names,
            "last_dates": sorted(set(last_dates)),
            "exam_dates": sorted(set(exam_dates)),
            "fees": sorted(set(fees)),
            "qualifications": sorted(set(quals)),
            "age_limits": sorted(set(ages)),
            "documents": sorted(set(docs)),
            "text": text,
        }
    return summaries


def build_all(extractions, nodes, chunk_name_to_id, doc_name_to_id):
    """Returns (graph, edges, stats, summaries)."""
    edges, edge_stats = build_chunk_edges(
        extractions, nodes, chunk_name_to_id, doc_name_to_id
    )
    nodes, edges, prune_stats = prune_nodes(nodes, edges, extractions)
    added, skipped = attach_document_level(extractions, nodes, edges)
    # MultiDiGraph: two relations can share a head+tail pair (e.g. an
    # exam with both a LAST_DATE and an EXAM_DATE to the same date).
    # A plain DiGraph would silently drop one of them.
    graph = nx.MultiDiGraph()
    for nid, n in nodes.items():
        graph.add_node(
            nid,
            type=n["type"],
            display_name=n["display_name"],
            variants=n["variants"],
            sources=n["sources"],
            chunk_refs=n["chunk_refs"],
        )
    for e in edges.values():
        # Sort chunk_refs + sources: the raw order follows the
        # extraction files' chunk-key order, which a JSON
        # re-serialization can scramble (alphabetical "10" before
        # "2"). Sorted refs make the graph deterministic — and the
        # last-date classifier reads refs in order, so its verdict
        # must not depend on key order.
        e["chunk_refs"] = sorted(e["chunk_refs"])
        e["sources"] = sorted(e["sources"])
        graph.add_edge(
            e["head"],
            e["tail"],
            relation=e["relation"],
            inferred=e["inferred"],
            count=e["count"],
            chunk_refs=e["chunk_refs"],
            sources=e["sources"],
        )
    summaries = build_community_summaries(extractions, nodes, edges)
    stats = {
        "nodes_total": len(nodes),
        "nodes_by_type": dict(Counter(n["type"] for n in nodes.values())),
        "edges_total": len(edges),
        "edges_by_relation": dict(Counter(e["relation"] for e in edges.values())),
        "edges_by_inference": dict(Counter(e["inferred"] for e in edges.values())),
        "raw_relations": edge_stats["raw_relations"],
        "dropped_off_schema_relations": edge_stats["dropped_off_schema_relations"],
        "orphan_relations_dropped": edge_stats["orphan_dropped"],
        "relations_reheaded": edge_stats["reheaded"],
        "relations_rehead_failed_kept": edge_stats["rehead_failed_kept"],
        "nodes_pruned_by_type": prune_stats["nodes_pruned_by_type"],
        "edges_dropped_touching_pruned_nodes": prune_stats[
            "edges_dropped_touching_pruned_nodes"
        ],
        "edges_dropped_type_mismatch": prune_stats["edges_dropped_type_mismatch"],
        "edges_dropped_unparseable_date": prune_stats[
            "edges_dropped_unparseable_date"
        ],
        "conducted_by_edges_dropped_not_own_org": prune_stats[
            "conducted_by_edges_dropped_not_own_org"
        ],
        "doc_level_edges_added": added,
        "doc_level_edges_skipped_existing": skipped,
        "isolated_nodes": sum(1 for n in graph.nodes if graph.degree(n) == 0),
    }
    return graph, edges, stats, summaries
