"""Eligibility rules, extracted from the Phase 3 graph.

One rule set per exam (EXAM nodes merged by display name — IBPS PO
has two nodes: the notification and the PwBD notice).

Two data-driven heuristics, both stated openly because the graph
cannot express them directly:
- BASE_MIN_CUTOFF: a base minimum age for government exams is 18-21.
  A range starting above this (e.g. Clerk's 35-38) is a relaxation
  band, not a base range — it is only used for reserved-category
  profiles.
- MIN_QUAL_IGNORED: in this corpus, class-X entries are DOB-proof
  mentions and 'certificate' is the ESM-only Army Special
  Certificate — neither is the qualifying degree for a general
  candidate. The minimum qualification is taken over class-XII and
  above, falling back to all levels if an exam has nothing else.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

from networkx.readwrite import json_graph

from app.config import PROCESSED_DIR
from app.eligibility.profile import LEVEL_RANK

GRAPH_PATH = PROCESSED_DIR / "graph" / "graph.json"
SUMMARIES_PATH = PROCESSED_DIR / "graph" / "community_summaries.json"

BASE_MIN_CUTOFF = 22
MIN_QUAL_IGNORED = {"class x", "certificate"}

AGE_RANGE_RE = re.compile(r"^(\d{1,2})-(\d{1,2}) years$")
AGE_SINGLE_RE = re.compile(r"^(\d{1,2}) years$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ExamRules:
    exam: str
    org: str | None = None
    posts: list[str] = field(default_factory=list)
    base_ranges: list[tuple[int, int]] = field(default_factory=list)
    relaxation_bands: list[tuple[int, int]] = field(default_factory=list)
    relaxation_singles: list[int] = field(default_factory=list)
    min_qual: str | None = None
    qual_levels: list[str] = field(default_factory=list)
    last_dates: list[date] = field(default_factory=list)
    last_dates_raw: list[str] = field(default_factory=list)


def _parse_age(text):
    m = AGE_RANGE_RE.match(text.strip())
    if m:
        return ("range", int(m.group(1)), int(m.group(2)))
    m = AGE_SINGLE_RE.match(text.strip())
    if m:
        return ("single", int(m.group(1)))
    return None


def _qual_level(node_id):
    # node id: "QUALIFICATION:bachelor's degree"
    return node_id.split(":", 1)[1].strip().lower()


def _load_summary_orgs():
    """Org per document, from Phase 3's community summaries.

    The org is NOT re-derived from CONDUCTED_BY edges here: chunk-ref
    voting fails (CHSL's junk org 'Commission' has 16 refs vs 10 for
    the real 'Staff Selection Commission'). Phase 3 already resolved
    this correctly per document, so reuse it.
    """
    try:
        summaries = json.loads(SUMMARIES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {stem: s.get("org") for stem, s in summaries.items()}


def load_exam_rules():
    data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    graph = json_graph.node_link_graph(data, directed=True, multigraph=True, edges="links")
    summary_orgs = _load_summary_orgs()
    exams = {}
    for node_id, nd in graph.nodes(data=True):
        if nd.get("type") != "EXAM":
            continue
        disp = nd.get("display_name", node_id)
        rules = exams.setdefault(disp, ExamRules(exam=disp))
        if rules.org is None:
            sources = nd.get("sources") or []
            if sources:
                rules.org = summary_orgs.get(sources[0])
        posts = set()
        for _, t, ed in graph.out_edges(node_id, data=True):
            rel = ed.get("relation")
            td = graph.nodes[t]
            if rel == "OFFERS":
                posts.add(t)
            elif rel == "LAST_DATE":
                raw = td.get("display_name", "")
                rules.last_dates_raw.append(raw)
                if ISO_DATE_RE.match(raw):
                    rules.last_dates.append(date.fromisoformat(raw))
        for post_id in posts:
            rules.posts.append(graph.nodes[post_id].get("display_name", post_id))
            for _, t, ed in graph.out_edges(post_id, data=True):
                rel = ed.get("relation")
                td = graph.nodes[t]
                if rel == "HAS_AGE_LIMIT":
                    parsed = _parse_age(td.get("display_name", ""))
                    if parsed is None:
                        continue
                    if parsed[0] == "range":
                        _, lo, hi = parsed
                        if lo <= BASE_MIN_CUTOFF:
                            rules.base_ranges.append((lo, hi))
                        else:
                            rules.relaxation_bands.append((lo, hi))
                    else:
                        rules.relaxation_singles.append(parsed[1])
                elif rel == "REQUIRES":
                    lvl = _qual_level(t)
                    if lvl in LEVEL_RANK and lvl not in rules.qual_levels:
                        rules.qual_levels.append(lvl)
    for disp, rules in exams.items():
        rules.posts = sorted(set(rules.posts))
        rules.base_ranges = sorted(set(rules.base_ranges))
        rules.relaxation_bands = sorted(set(rules.relaxation_bands))
        rules.relaxation_singles = sorted(set(rules.relaxation_singles))
        rules.last_dates = sorted(set(rules.last_dates))
        rules.last_dates_raw = sorted(set(rules.last_dates_raw))
        rules.qual_levels = sorted(rules.qual_levels, key=lambda l: LEVEL_RANK[l])
        pool = [l for l in rules.qual_levels if l not in MIN_QUAL_IGNORED]
        pool = pool or rules.qual_levels
        rules.min_qual = pool[0] if pool else None
    return exams
