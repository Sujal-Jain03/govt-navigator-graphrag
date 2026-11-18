"""Phase 12 tests: derived doc exams, orphan guard, orchestrator, sources.

No network, no LLM calls: the orchestrator runs on injected fake
stages, and the extraction guard runs on a fake extract function.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASSED = 0
FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name} {extra}")


# ---------------------------------------------------------------- 1. derive
from app.graph.normalize import (  # noqa: E402
    canonical_exam_for_doc,
    canonical_exam_name,
    node_id,
    set_doc_canon_overrides,
)
from app.graph.resolver import derive_doc_exams  # noqa: E402

NEW_STEM = "sbi_clerk_2026-11-20_test"

EXTRACTIONS = {
    # Known stem: must NOT get an override.
    "SBI_PO_2026_Notification": {"results": {
        "0": {"error": None,
              "entities": [{"type": "EXAM", "name": "SBI PO"}],
              "relations": []}}},
    # Unknown stem: EXAM entities vote for the canonical exam.
    NEW_STEM: {"results": {
        "0": {"error": None,
              "entities": [
                  {"type": "EXAM", "name": "SBI Clerk"},
                  {"type": "EXAM", "name": "SBI Clerk"},
                  {"type": "EXAM", "name": "Junior Associates"},
                  {"type": "EXAM", "name": "CRP"},
                  {"type": "EXAM", "name": "Tier-II"},
              ],
              "relations": []},
        "1": {"error": None,
              "entities": [
                  {"type": "EXAM", "name": "SBI Clerk"},
                  {"type": "EXAM", "name": "SBI Clerk (Junior Associates)"},
                  {"type": "EXAM", "name": "CRP"},
              ],
              "relations": []},
        # Errored chunks do not vote.
        "2": {"error": "boom",
              "entities": [{"type": "EXAM", "name": "Junk Exam"}],
              "relations": []},
    }},
    # Unknown stem with no usable EXAM entities: no override.
    "empty_doc_test": {"results": {
        "0": {"error": None,
              "entities": [{"type": "POST", "name": "Clerk"}],
              "relations": []}}},
}


def section_derive():
    print("== 1. derive_doc_exams + overrides ==")
    overrides = derive_doc_exams(EXTRACTIONS)
    check("exactly 1 override derived", len(overrides) == 1, str(overrides))
    check("unknown stem -> 'sbi clerk'",
          overrides.get(NEW_STEM) == "sbi clerk", str(overrides))
    check("known stem untouched", "SBI_PO_2026_Notification" not in overrides)
    check("stem with no EXAMs untouched", "empty_doc_test" not in overrides)
    set_doc_canon_overrides(overrides)
    check("override wins for any spelling",
          canonical_exam_for_doc("Junior Associates", NEW_STEM) == "sbi clerk")
    check("override answers the re-head path (empty name)",
          canonical_exam_for_doc("", NEW_STEM) == "sbi clerk")
    check("all spellings merge into one node id",
          node_id("EXAM", "Junior Associates", NEW_STEM)
          == f"EXAM:{NEW_STEM}:sbi clerk")
    check("DOC_EXAMS still authoritative",
          canonical_exam_for_doc("whatever", "SBI_PO_2026_Notification")
          == "sbi po")
    check("stage names derive to nothing",
          canonical_exam_name("CRP") == ""
          and canonical_exam_name("Tier-II") == "")
    check("parentheticals stripped in derivation",
          canonical_exam_name("SBI Clerk (Junior Associates)") == "sbi clerk")


# ----------------------------------------------------------- 2. orphan guard
from app.extraction.runner import run_all  # noqa: E402


def section_orphan():
    print("== 2. extraction orphan guard ==")
    tmp = Path(tempfile.mkdtemp(prefix="pipeline_orphan_"))
    proc, notif, extr = tmp / "processed", tmp / "notifications", tmp / "extractions"
    proc.mkdir()
    notif.mkdir()
    real = {"source_file": "Real_Exam_Notification.pdf", "file_hash": "abc",
            "chunks": [{"chunk_index": 0, "text": "some text"}]}
    (proc / "Real_Exam_Notification.json").write_text(json.dumps(real))
    (notif / "Real_Exam_Notification.pdf").write_bytes(b"%PDF-1.4 fake")
    orphan = {"source_file": "Orphan.pdf", "file_hash": "def",
              "chunks": [{"chunk_index": 0, "text": "orphan text"}]}
    (proc / "Orphan.pdf.json").write_text(json.dumps(orphan))

    calls = []

    def fake_extract(client, text):
        calls.append(text)
        return {"entities": [], "relations": []}

    totals = run_all(processed_dir=proc, notifications_dir=notif,
                     extractions_dir=extr, client=object(),
                     extract_fn=fake_extract)
    check("real chunk extracted", totals["done"] == 1, str(totals))
    check("orphan counted", totals["orphans"] == 1, str(totals))
    check("extract called once, on the real chunk", calls == ["some text"])
    check("extraction file only for the real stem",
          (extr / "Real_Exam_Notification.extractions.json").exists()
          and not (extr / "Orphan.pdf.extractions.json").exists())
    totals2 = run_all(processed_dir=proc, notifications_dir=notif,
                      extractions_dir=extr, client=object(),
                      extract_fn=fake_extract)
    check("second run resumes (done 0, skipped 1)",
          totals2["done"] == 0 and totals2["skipped"] == 1, str(totals2))


# ------------------------------------------------------------- 3. orchestrator
from app.pipeline.orchestrator import run_pipeline  # noqa: E402


def make_stages(calls, update=None, ingest=None, extract=None,
                graph=None, index=None):
    def fake_update(dry_run=False, source_keys=None, ref_date=None):
        calls.append(("update", dry_run))
        return update or {"downloaded": [], "would_download": [],
                          "skipped": [], "errors": [], "local_new": [],
                          "expiry": []}

    def fake_ingest():
        calls.append(("ingest",))
        if isinstance(ingest, Exception):
            raise ingest
        return ingest or {"ingested": 0, "skipped": 0, "new_stems": []}

    def fake_extract():
        calls.append(("extract",))
        if isinstance(extract, Exception):
            raise extract
        return extract or {"done": 0, "failed": 0, "skipped": 0, "orphans": 0}

    def fake_graph():
        calls.append(("graph",))
        return graph or {"nodes_total": 1500, "edges_total": 2100,
                         "derived_doc_exams": {}}

    def fake_index():
        calls.append(("index",))
        return index or {"num_chunks": 1200, "dim": 768}

    return {"update": fake_update, "ingest": fake_ingest,
            "extract": fake_extract, "graph": fake_graph,
            "index": fake_index}


def fresh_proc(with_graph=False, with_index=False):
    tmp = Path(tempfile.mkdtemp(prefix="pipeline_run_"))
    proc = tmp / "processed"
    if with_graph:
        (proc / "graph").mkdir(parents=True)
        (proc / "graph" / "graph.json").write_text("{}")
    if with_index:
        (proc / "retrieval").mkdir(parents=True)
        (proc / "retrieval" / "chunk_embeddings.npy").write_bytes(b"x")
    return proc


def section_orchestrator():
    print("== 3. orchestrator ==")
    # No-op day: nothing new, graph + index exist.
    calls = []
    proc = fresh_proc(with_graph=True, with_index=True)
    rep = run_pipeline(stages=make_stages(calls), rebuild=False,
                       processed_dir=proc)
    check("no-op: graph+index skipped",
          ("graph",) not in calls and ("index",) not in calls, str(calls))
    check("no-op: ok, unchanged",
          rep["ok"] is True and rep["changed"] is False)

    # New-PDF day: everything runs, in order, then rebuild.
    calls = []
    rebuilds = []
    proc = fresh_proc()
    stages = make_stages(
        calls,
        update={"downloaded": [{"filename": "x.pdf"}], "would_download": [],
                "skipped": [], "errors": [], "local_new": [], "expiry": []},
        ingest={"ingested": 1, "skipped": 9, "new_stems": ["x"]},
        extract={"done": 5, "failed": 0, "skipped": 100, "orphans": 0},
        graph={"nodes_total": 1500, "edges_total": 2100,
               "derived_doc_exams": {"x": "y"}})
    rep = run_pipeline(stages=stages, processed_dir=proc,
                       rebuild_fn=lambda: rebuilds.append(1) or (True, "ok"))
    order = [c[0] for c in calls]
    check("new-PDF: stages run in order",
          order == ["update", "ingest", "extract", "graph", "index"],
          str(order))
    check("new-PDF: ok, changed",
          rep["ok"] is True and rep["changed"] is True)
    check("new-PDF: graph stats recorded",
          rep["stages"]["graph"].get("nodes") == 1500
          and rep["stages"]["graph"].get("derived_doc_exams") == {"x": "y"})
    check("new-PDF: index stats recorded",
          rep["stages"]["index"].get("num_chunks") == 1200)
    check("new-PDF: API rebuild requested", len(rebuilds) == 1)
    check("new-PDF: rebuild result recorded",
          rep.get("rebuild") == {"ok": True, "message": "ok"})

    # Failure: ingest raises -> later stages never run.
    calls = []
    proc = fresh_proc()
    stages = make_stages(calls, ingest=RuntimeError("ingest boom"))
    rep = run_pipeline(stages=stages, rebuild=False, processed_dir=proc)
    check("failure: later stages never run",
          [c[0] for c in calls] == ["update", "ingest"], str(calls))
    check("failure: ok False, error recorded",
          rep["ok"] is False
          and rep["stages"]["ingest"].get("error") == "ingest boom")

    # Extraction all failed on new PDFs: graph/index skipped, warning set.
    calls = []
    proc = fresh_proc()
    stages = make_stages(
        calls,
        ingest={"ingested": 1, "skipped": 9, "new_stems": ["x"]},
        extract={"done": 0, "failed": 3, "skipped": 0, "orphans": 0})
    rep = run_pipeline(stages=stages, rebuild=False, processed_dir=proc)
    check("extract-failed: graph+index skipped",
          ("graph",) not in calls and ("index",) not in calls, str(calls))
    check("extract-failed: warning recorded",
          "warning" in rep and "extraction failed" in rep["warning"])

    # Dry run: only discovery, nothing else.
    calls = []
    proc = fresh_proc()
    rep = run_pipeline(stages=make_stages(calls), dry_run=True,
                       rebuild=False, processed_dir=proc)
    check("dry run: only update runs, with dry_run=True",
          calls == [("update", True)], str(calls))
    check("dry run: report flagged", rep["dry_run"] is True)

    # Missing graph file forces a rebuild even with nothing new.
    calls = []
    proc = fresh_proc(with_graph=False, with_index=True)
    rep = run_pipeline(stages=make_stages(calls), rebuild=False,
                       processed_dir=proc)
    check("missing graph.json forces a rebuild",
          ("graph",) in calls and rep["changed"] is True, str(calls))

    # Report file written.
    report_path = proc / "updates" / f"pipeline_report_{rep['date']}.json"
    check("pipeline report written to disk",
          report_path.exists()
          and json.loads(report_path.read_text())["date"] == rep["date"])


# ------------------------------------------------------------------ 4. sources
from app.updates.discovery import filter_candidate  # noqa: E402
from app.updates.sources import SOURCES, get_source  # noqa: E402

NEW_DOMAINS = {
    "sbi_clerk": ["sbi.bank.in"],
    "rbi_assistant": ["rbi.org.in"],
    "nabard": ["nabard.org"],
    "sebi": ["sebi.gov.in"],
    "lic": ["licindia.in"],
    "india_post": ["indiapost.gov.in", "indiapostgdsonline.gov.in"],
    "fci": ["fci.gov.in"],
}


def section_sources():
    print("== 4. sources ==")
    check("15 sources total", len(SOURCES) == 15, str(len(SOURCES)))
    ok_shape = all(
        s.get("key") and s.get("name") and s.get("exam")
        and s.get("domains") and s.get("queries", {}).get("notification")
        and s.get("queries", {}).get("corrigendum")
        and s.get("keywords")
        for s in SOURCES)
    check("every source has key/name/exam/domains/2 queries/keywords",
          ok_shape)
    daily = sum(len(s["queries"]) for s in SOURCES)
    check("Tavily budget: 30 requests/day = ~900/month <= 1000",
          daily == 30 and daily * 30 <= 1000, f"{daily}/day")
    ok_domains = all(get_source(k)["domains"] == v
                     for k, v in NEW_DOMAINS.items())
    check("new sources use the official domains", ok_domains)

    def passes(key, title, url):
        return filter_candidate({"title": title, "url": url},
                                get_source(key)) is None

    check("sbi_clerk: real notification passes",
          passes("sbi_clerk",
                 "SBI Clerk (Junior Associates) Recruitment 2026",
                 "https://sbi.bank.in/web/careers/clerk-2026.pdf"))
    check("sbi_clerk: tender rejected",
          not passes("sbi_clerk", "SBI Tender for printing services",
                     "https://sbi.bank.in/tender.pdf"))
    check("nabard: Grade A notification passes",
          passes("nabard",
                 "Recruitment of Assistant Managers (Grade A) 2026",
                 "https://www.nabard.org/careers/grade-a.pdf"))
    check("nabard: annual report rejected",
          not passes("nabard", "NABARD Annual Report 2025-26",
                     "https://www.nabard.org/annual-report.pdf"))
    check("fci: recruitment notification passes",
          passes("fci", "FCI Recruitment 2026 Notification",
                 "https://fci.gov.in/recruitment-2026.pdf"))
    check("fci: annual report rejected",
          not passes("fci", "FCI Annual Report 2025",
                     "https://fci.gov.in/annual-report.pdf"))
    check("sebi: Grade A notification passes",
          passes("sebi",
                 "Recruitment of Officer Grade A (Assistant Manager) 2026",
                 "https://www.sebi.gov.in/careers/grade-a.pdf"))
    check("lic: AAO notification passes",
          passes("lic", "Recruitment of AAO (Generalist) 2026",
                 "https://licindia.in/careers/aao-2026.pdf"))
    check("india_post: GDS notification passes",
          passes("india_post",
                 "India Post GDS Schedule-II Notification 2026",
                 "https://indiapost.gov.in/gds.pdf"))
    check("rbi_assistant: Assistants notification passes",
          passes("rbi_assistant", "Recruitment of Assistants 2026",
                 "https://www.rbi.org.in/assistants.pdf"))


def main():
    section_derive()
    section_orphan()
    section_orchestrator()
    section_sources()
    print("=" * 80)
    print(f"summary: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
