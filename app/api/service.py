"""Service layer for the Govt Navigator app (Phase 10).

Wires the Phase 4-8 modules into app-level operations the API and
the UI both use. No HTTP code lives here, so every function is
testable directly. Singletons are built lazily and reused: the
BGE model + index load once, then serve every request.
"""
import json
from datetime import date

from app import config
from app.eligibility.engine import check_all
from app.eligibility.profile import Profile
from app.eligibility.rules import load_exam_rules
from app.generation.generator import generate
from app.guardrails.guards import check_input
from app.retrieval.retriever import HybridRetriever
from app.updates.expiry import expiry_report
from app.matching.dates import classify_last_dates
from app.matching.matcher import match_jobs


# Test injection point: when set, ask_question uses this instead of
# the real LLM (same llm_fn dependency injection generate() has).
# None (the default) means the real rotated LLM path.
LLM_FN = None

_RETRIEVER = None
_RULES = None
_SUMMARIES = None
_CLOSING = None


def get_closing():
    """Last-date classification, loaded once from the graph."""
    global _CLOSING
    if _CLOSING is None:
        _CLOSING = classify_last_dates()
    return _CLOSING

def get_retriever():
    """The shared HybridRetriever (loads BGE + index on first use)."""
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = HybridRetriever()
    return _RETRIEVER


def get_rules():
    """Exam rules, loaded once from the graph."""
    global _RULES
    if _RULES is None:
        _RULES = load_exam_rules()
    return _RULES


def get_summaries():
    """Community summaries keyed by exam display name.

    The summaries file is keyed by document stem; each summary
    lists its exams, so one document (UPSC) can serve two exams.
    Non-dict entries (e.g. the downloader's retrieved_at key)
    are skipped.
    """
    global _SUMMARIES
    if _SUMMARIES is None:
        path = config.PROCESSED_DIR / "graph" / "community_summaries.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        _SUMMARIES = {}
        for summary in raw.values():
            if not isinstance(summary, dict):
                continue
            for exam in summary.get("exams", []):
                _SUMMARIES.setdefault(exam, summary)
    return _SUMMARIES


def index_stats():
    """Cheap counts for the health endpoint (no model load)."""
    meta_path = config.PROCESSED_DIR / "retrieval" / "chunk_meta.json"
    stats_path = config.PROCESSED_DIR / "graph" / "graph_stats.json"
    chunks = len(json.loads(meta_path.read_text(encoding="utf-8")))
    if stats_path.exists():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        nodes = stats.get("nodes_total", stats.get("nodes"))
        edges = stats.get("edges_total", stats.get("edges"))
    else:
        # Fallback: the graph itself is the truth; count from it.
        import networkx as nx
        graph_path = config.PROCESSED_DIR / "graph" / "graph.json"
        graph = nx.node_link_graph(
            json.loads(graph_path.read_text(encoding="utf-8")), edges="links")
        nodes, edges = graph.number_of_nodes(), graph.number_of_edges()
    return {
        "chunks": chunks,
        "nodes": nodes,
        "edges": edges,
        "exams": len(get_rules()),
    }


def ask_question(query, llm_fn=None):
    """Full Q&A pipeline: guardrail -> retrieve -> generate.

    check_input is wired here (deferred from Phase 7): a blocked
    query raises ValueError with the reason and never reaches
    retrieval or the LLM. The generation-side guardrail
    (check_output) already runs inside generate().
    """
    allowed, reason, _details = check_input(query)
    if not allowed:
        raise ValueError(f"query blocked by guardrail: {reason}")
    fn = llm_fn if llm_fn is not None else LLM_FN
    retrieval = get_retriever().retrieve(query)
    result = generate(query, retrieval, llm_fn=fn)
    chunks = retrieval.get("chunks", [])
    return {
        "answer": result.answer,
        "abstained": result.abstained,
        "model": result.model,
        "cited": result.cited,
        "citations_valid": result.citations_valid,
        "guard_flags": result.guard_flags,
        "matched_entities": retrieval.get("matched_entities", []),
        "top_score": chunks[0]["score"] if chunks else None,
        "evidence": [
            {"n": e.n, "kind": e.kind, "text": e.text, "source": e.source}
            for e in result.evidence
        ],
    }


def highest_qualification(qualifications):
    """The highest level in a list of qualification spellings.

    Each entry is normalized through LEVEL_ALIASES (same rule as
    Profile) and validated; an unknown level raises ValueError.
    Returns (highest_level, normalized_list).
    """
    from app.eligibility.profile import LEVEL_ALIASES, LEVEL_RANK
    normalized = []
    for q in qualifications:
        key = LEVEL_ALIASES.get(q.strip().lower(), q.strip().lower())
        if key not in LEVEL_RANK:
            raise ValueError(f"unknown qualification level: {q!r}")
        normalized.append(key)
    highest = max(normalized, key=lambda k: LEVEL_RANK[k])
    return highest, normalized


def _build_profile(name, date_of_birth, qualification, category,
                   qualifications, experience_years):
    """Shared profile construction for eligibility + jobs.

    Returns (profile, normalized_qualifications). Raises ValueError
    on a missing/unknown qualification or negative experience.
    """
    if qualifications:
        highest, normalized = highest_qualification(qualifications)
    elif qualification:
        highest, normalized = highest_qualification([qualification])
    else:
        raise ValueError("at least one qualification is required")
    if float(experience_years) < 0:
        raise ValueError("experience_years cannot be negative")
    profile = Profile(name=name, date_of_birth=date_of_birth,
                      qualification=highest, category=category)
    return profile, normalized

def check_eligibility(name, date_of_birth, qualification=None, category="General",
                      ref_date=None, qualifications=None, experience_years=0.0):
    """Eligibility for all exams. Raises ValueError on a bad profile.

    qualifications (a list) wins over qualification (a single level);
    matching uses the HIGHEST level in the list, and at least one
    qualification is required. experience_years is captured with the
    profile; the current corpus is entry-level notifications with no
    experience requirement in the graph, so it does not change
    verdicts yet.
    """
    ref = ref_date or date.today()
    profile, normalized = _build_profile(
        name, date_of_birth, qualification, category,
        qualifications, experience_years)
    rules = get_rules()
    results = []
    for res in check_all(profile, rules, ref):
        results.append({
            "exam": res.exam,
            "verdict": res.verdict,
            "age": res.age,
            "reasons": res.reasons,
            "age_ok": res.age_ok,
            "qual_ok": res.qual_ok,
            "is_open": res.is_open,
            "relaxation_may_apply": res.relaxation_may_apply,
            "last_date": res.last_date.isoformat() if res.last_date else None,
            "last_dates": [d.isoformat() for d in rules[res.exam].last_dates],
            "min_qual": res.min_qual,
            "posts": res.posts,
        })
    return {
        "name": profile.name,
        "age": profile.age_on(ref),
        "qualification": profile.qualification,
        "qualifications": normalized,
        "experience_years": float(experience_years),
        "category": profile.category,
        "ref_date": ref.isoformat(),
        "results": results,
    }
    
def list_jobs(name, date_of_birth, qualification=None, category="General",
              ref_date=None, qualifications=None, experience_years=0.0):
    """Jobs the profile can enroll in right now (Phase 11).

    Same profile rules as check_eligibility. Openness uses each
    exam's submission closing date, never the latest graph date.
    """
    ref = ref_date or date.today()
    profile, normalized = _build_profile(
        name, date_of_birth, qualification, category,
        qualifications, experience_years)
    matched = match_jobs(profile, get_rules(), get_closing(), ref,
                         get_summaries())
    return {
        "name": profile.name,
        "age": profile.age_on(ref),
        "qualification": profile.qualification,
        "qualifications": normalized,
        "experience_years": float(experience_years),
        "category": profile.category,
        **matched,
    }



def list_exams(ref_date=None):
    """One card's worth of facts per exam: rules + expiry + summary."""
    ref = ref_date or date.today()
    rules = get_rules()
    summaries = get_summaries()
    # expiry_report returns a list of per-exam dicts; key it by exam.
    expiry = {e["exam"]: e for e in expiry_report(ref_date=ref)}
    exams = []
    for name in sorted(rules):
        r = rules[name]
        ex = expiry.get(name, {})
        summary = summaries.get(name, {})
        exams.append({
            "exam": name,
            "org": r.org,
            "posts": r.posts,
            "age_ranges": [f"{lo}-{hi} years" for lo, hi in r.base_ranges],
            "min_qual": r.min_qual,
            "last_dates": [d.isoformat() for d in r.last_dates],
            "status": ex.get("status", "unknown"),
            "days_left": ex.get("days_left"),
            "summary": summary.get("text"),
            "fees": summary.get("fees", []),
            "documents_count": len(summary.get("documents", [])),
        })
    return exams


def latest_updates(ref_date=None):
    """The newest daily-update report plus a fresh expiry list."""
    ref = ref_date or date.today()
    updates_dir = config.PROCESSED_DIR / "updates"
    reports = sorted(updates_dir.glob("report_*.json")) if updates_dir.exists() else []
    report = None
    if reports:
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        report.pop("retrieved_at", None)
    expiry = expiry_report(ref_date=ref)
    return {
        "report": report,
        "expiry": sorted(expiry, key=lambda e: e["exam"]),
    }
