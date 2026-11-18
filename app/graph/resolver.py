"""Entity resolution: merge extraction entities into canonical nodes.

Groups all entities from all *.extractions.json files by node_id
(type + canonical name; EXAM/POST also scoped by document). Each
group becomes one canonical node with:
- display_name: the most frequent original spelling (what users see)
- variants: every distinct original spelling (audit trail)
- sources: which notification files mention it
- chunk_refs: (source_file, chunk_index) pairs (provenance)

Entities whose type is outside the Phase 2 schema (the models
sometimes invent types like RELIGION or ORGANISM from syllabus text)
are dropped and counted — the raw data stays untouched in the
extraction files.

Also builds two name -> node_id maps so the builder can turn each
chunk's relations (which reference entity NAMES) into edges:
- chunk_name_to_id: per-chunk lookup (exact scope)
- doc_name_to_id: per-document fallback (a relation head that the
  model spelled differently from the chunk's own entity list may
  still resolve against another chunk of the same document)
Both maps are keyed by NORMALIZED name, so a relation head that
differs from its entity only in case or spacing still resolves.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.graph.normalize import (
    DOC_EXAMS,
    EXAM_DISPLAY,
    SCHEMA_TYPES,
    canonical_exam_for_doc,
    canonical_exam_name,
    canonical_name,
    node_id,
    normalize_name,
)


def load_extractions(extractions_dir: Path) -> dict[str, dict]:
    """Load every *.extractions.json. Returns {stem: parsed_json}."""
    out = {}
    for path in sorted(extractions_dir.glob("*.extractions.json")):
        stem = path.name[: -len(".extractions.json")]
        out[stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


def derive_doc_exams(extractions: dict[str, dict]) -> dict[str, str]:
    """Per-stem canonical exam for documents missing from DOC_EXAMS.

    For each unknown stem, the most frequent derived EXAM name in its
    extractions becomes the stem's single canonical exam — so a new
    notification's EXAM entities merge into one node instead of
    fragmenting by spelling. Stems already in DOC_EXAMS are untouched.
    A notification about TWO exams (like UPSC's CSE + IFoS) still
    needs a manual DOC_EXAMS entry: the override picks the most
    frequent exam only. Register the result with
    set_doc_canon_overrides before calling resolve_entities.
    """
    overrides = {}
    for stem, data in extractions.items():
        if stem in DOC_EXAMS:
            continue
        counts = Counter()
        for result in (data.get("results") or {}).values():
            if result.get("error"):
                continue
            for ent in result.get("entities") or []:
                if ent["type"] != "EXAM":
                    continue
                canon = canonical_exam_name(ent["name"])
                if canon:
                    counts[canon] += 1
        if counts:
            overrides[stem] = counts.most_common(1)[0][0]
    return overrides


def resolve_entities(extractions: dict[str, dict]):
    """Returns (nodes, chunk_name_to_id, doc_name_to_id, stats).

    nodes: {node_id: {id, type, display_name, variants, sources, chunk_refs}}
    chunk_name_to_id: {(stem, chunk_index): {normalized_name: node_id}}
    doc_name_to_id: {stem: {normalized_name: node_id}}
    stats: {raw_entities, dropped_off_schema: {type: count}}
    """
    groups = defaultdict(
        lambda: {"names": Counter(), "sources": set(), "chunk_refs": set()}
    )
    chunk_name_to_id = {}
    doc_name_to_id = defaultdict(dict)
    stats = {"raw_entities": 0, "dropped_off_schema": Counter()}
    for stem, data in extractions.items():
        for chunk_key, result in (data.get("results") or {}).items():
            if result.get("error"):
                continue
            chunk_index = int(chunk_key)
            name_to_id = {}
            for ent in result.get("entities") or []:
                stats["raw_entities"] += 1
                if ent["type"] not in SCHEMA_TYPES:
                    stats["dropped_off_schema"][ent["type"]] += 1
                    continue
                nid = node_id(ent["type"], ent["name"], stem)
                g = groups[nid]
                g["type"] = ent["type"]
                if ent["type"] == "EXAM":
                    g["canon"] = canonical_exam_for_doc(ent["name"], stem)
                else:
                    g["canon"] = canonical_name(ent["type"], ent["name"])
                g["names"][ent["name"]] += 1
                g["sources"].add(stem)
                g["chunk_refs"].add((stem, chunk_index))
                # Last wins if one chunk reuses a name for two types (rare).
                key = normalize_name(ent["name"])
                name_to_id[key] = nid
                doc_name_to_id[stem].setdefault(key, nid)
            chunk_name_to_id[(stem, chunk_index)] = name_to_id
    nodes = {}
    for nid, g in groups.items():
        # EXAM display names come from EXAM_DISPLAY (the most frequent
        # raw spelling is often "CRP" or "Tier-II", not the exam name).
        # AGE_LIMIT / DATE display the canonical form ("21-30 years",
        # "2026-07-21") — the raw spellings are long sentences.
        if g["type"] == "EXAM" and g.get("canon") in EXAM_DISPLAY:
            display = EXAM_DISPLAY[g["canon"]]
        elif g["type"] in ("AGE_LIMIT", "DATE"):
            display = g.get("canon") or g["names"].most_common(1)[0][0]
        else:
            display = g["names"].most_common(1)[0][0]
        nodes[nid] = {
            "id": nid,
            "type": g["type"],
            "display_name": display,
            "variants": sorted(g["names"]),
            "sources": sorted(g["sources"]),
            "chunk_refs": sorted(g["chunk_refs"]),
        }
    stats["dropped_off_schema"] = dict(stats["dropped_off_schema"])
    return nodes, chunk_name_to_id, dict(doc_name_to_id), stats
