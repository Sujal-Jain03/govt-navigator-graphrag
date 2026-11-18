"""Test the Phase 8 live-updates loop.

Fully deterministic — no network, no keys. Tavily + download are
stubbed; expiry runs against the real Phase 3 graph. All state and
downloads live in temp dirs, so the test is re-runnable.
"""
import json
import tempfile
from datetime import date
from pathlib import Path

from app.updates import discovery, downloader
from app.updates.discovery import (
    TavilyError, classify, discover, filter_candidate, is_pdf_url,
    is_trusted, tavily_search)
from app.updates.downloader import DownloadError, download_pdf
from app.updates.expiry import expiry_report
from app.updates.runner import run_update
from app.updates.sources import get_source
from app.updates.state import load_state, save_state, seen_hash, seen_url

PASSED = 0
FAILED = 0
REF = date(2026, 11, 18)

FAKE_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n")


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name} {extra}")


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeDownload:
    def __init__(self, status_code=200, body=FAKE_PDF,
                 content_type="application/pdf"):
        self.status_code = status_code
        self._body = body
        self.headers = {"Content-Type": content_type}

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def stub_search(api_key, query, domains):
    if "RBI" in query:
        raise RuntimeError("tavily down")
    if "IBPS PO" not in query:
        return []
    if "corrigendum" in query.lower():
        return [{"url": "https://ibps.in/po-corrigendum.pdf",
                 "title": "IBPS PO Corrigendum", "score": 0.9}]
    return [
        {"url": "https://ibps.in/po-notification.pdf",
         "title": "IBPS PO Notification 2026", "score": 0.95},
        {"url": "https://ibps.in/po-duplicate.pdf",
         "title": "IBPS PO Notification mirror", "score": 0.9},
        {"url": "https://randomblog.com/ibps-po.pdf",
         "title": "IBPS PO PDF download", "score": 0.8},
        {"url": "https://ibps.in/po-notice-page",
         "title": "IBPS PO notice page", "score": 0.7},
        {"url": "https://ibps.in/tender-notice.pdf",
         "title": "IBPS tender notice for printers", "score": 0.5},
    ]


def stub_get(url, headers=None, stream=None, timeout=None):
    # Distinct content per URL — except the deliberate duplicate,
    # which carries the notification's exact bytes.
    if "duplicate" in url:
        body = FAKE_PDF + b"\n% https://ibps.in/po-notification.pdf"
    else:
        body = FAKE_PDF + b"\n% " + url.encode()
    return _FakeDownload(body=body)


def stub_download(url, dest_dir, source_key, today):
    return download_pdf(url, dest_dir, source_key, today, get=stub_get)


if __name__ == "__main__":
    print("== tavily client (stubbed HTTP) ==")
    captured = {}

    def stub_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _FakeResp(200, {"results": [
            {"url": "https://ibps.in/a.pdf", "title": "A", "score": 0.9},
            {"url": "https://ibps.in/b.pdf", "title": "B", "score": 0.8}]})

    results = tavily_search("test-key", "IBPS PO", ["ibps.in"], post=stub_post)
    check("parses results", len(results) == 2
          and results[0]["url"] == "https://ibps.in/a.pdf")
    check("sends auth header + body",
          captured["url"] == discovery.TAVILY_URL
          and captured["headers"]["Authorization"] == "Bearer test-key"
          and captured["json"]["query"] == "IBPS PO"
          and captured["json"]["search_depth"] == "basic"
          and captured["json"]["max_results"] == 5
          and captured["json"]["time_range"] == "month"
          and captured["json"]["include_domains"] == ["ibps.in"])

    def stub_401(url, headers=None, json=None, timeout=None):
        return _FakeResp(401, text="invalid key")

    try:
        tavily_search("bad-key", "q", ["ibps.in"], post=stub_401)
        check("401 raises TavilyError", False)
    except TavilyError as e:
        check("401 raises TavilyError", "401" in str(e))

    print("== URL classification ==")
    check("is_pdf_url",
          is_pdf_url("https://ibps.in/a.pdf")
          and is_pdf_url("https://ibps.in/A.PDF")
          and is_pdf_url("https://ibps.in/a.pdf?download=1")
          and not is_pdf_url("https://ibps.in/notice")
          and not is_pdf_url("https://ibps.in/a.html"))
    check("is_trusted",
          is_trusted("https://ibps.in/a.pdf", ["ibps.in"])
          and is_trusted("https://www.ibps.in/a.pdf", ["ibps.in"])
          and not is_trusted("https://ibps.in.evil.com/a.pdf", ["ibps.in"])
          and not is_trusted("https://evil-ibps.in/a.pdf", ["ibps.in"])
          and not is_trusted("https://ibps.com/a.pdf", ["ibps.in"]))
    check("classify",
          classify("Corrigendum to the notice", "https://x.in/a.pdf") == "corrigendum"
          and classify("Notice", "https://x.in/addendum.pdf") == "corrigendum"
          and classify("Notice", "https://x.in/a.pdf") == "notification")

    def dup_search(api_key, query, domains):
        return [{"url": "https://ibps.in/same.pdf", "title": "Same", "score": 0.9}]

    cands = discover(get_source("ibps_po"), "k", search_fn=dup_search)
    check("discover dedupes URLs across queries", len(cands) == 1)

    print("== precision filter (unit + real live-run fixtures) ==")
    check("clean IBPS PO notification passes",
          filter_candidate({"title": "IBPS PO Notification 2026",
                            "url": "https://ibps.in/po.pdf"},
                           get_source("ibps_po")) is None)
    check("answer-key title excluded",
          filter_candidate({"title": "Tentative Answer Keys CGL Tier-I",
                            "url": "https://ssc.gov.in/a.pdf"},
                           get_source("ssc_cgl")) == "excluded keyword: answer key")
    check("CGL phrase title passes",
          filter_candidate({"title": "Combined Graduate Level Examination 2026",
                            "url": "https://ssc.gov.in/a.pdf"},
                           get_source("ssc_cgl")) is None)
    check("RBI Grade 'B' title passes",
          filter_candidate({"title": "Recruitment of Officers in Grade 'B' (DR)",
                            "url": "https://rbi.org.in/a.pdf"},
                           get_source("rbi_grade_b")) is None)
    check("CSE notification passes",
          filter_candidate({"title": "Civil Services (Preliminary) Examination, 2026",
                            "url": "https://upsc.gov.in/a.pdf"},
                           get_source("upsc_cse")) is None)
    # The 4 PDFs the first live run (2026-11-18) actually downloaded —
    # all junk for the corpus; the filter must reject every one.
    live_junk = [
        ("sbi_po",
         "state bank of india - thiruvananthapuram circle tender notice",
         "https://sbi.bank.in/documents/39129/51516783/NOTICE+INVITING+TENDER20261027061919722.pdf",
         "excluded keyword: tender"),
        ("ssc_cgl", "writeup_04112026.pdf",
         "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/writeup_04112026.pdf",
         "no exam keyword in title/URL"),
        ("upsc_cse",
         "05th November, 2026 On the basis of the results of the Civil Services ...",
         "https://www.upsc.gov.in/sites/default/files/WR-CSM-2026-Engl-RollList-05112026.pdf",
         "excluded keyword: rolllist"),
        ("upsc_cse",
         "special advertisement no. 51/2026 union public service commission ...",
         "https://www.upsc.gov.in/sites/default/files/AdvtNo-51-2026-Special-Engl-240726.pdf",
         "no exam keyword in title/URL"),
    ]
    for key, title, url, expected in live_junk:
        got = filter_candidate({"title": title, "url": url}, get_source(key))
        check(f"live junk rejected: {url.split('/')[-1][:40]}",
              got == expected, f"(got {got!r}, want {expected!r})")

    print("== downloader (stubbed HTTP) ==")
    with tempfile.TemporaryDirectory() as tmp:
        dl = download_pdf("https://ibps.in/po-notification.pdf", tmp,
                          "ibps_po", "2026-11-18", get=stub_get)
        check("saves bytes + sha256 + filename",
              Path(dl["path"]).read_bytes() == FAKE_PDF + b"\n% https://ibps.in/po-notification.pdf"
              and dl["filename"] == "ibps_po_2026-11-18_po-notification.pdf"
              and len(dl["sha256"]) == 64 and dl["bytes"] > 0)
        dl2 = download_pdf("https://x.in/a b.pdf?v=1", tmp, "s", "2026-11-18",
                           get=stub_get)
        check("sanitizes filename",
              dl2["filename"] == "s_2026-11-18_a_b.pdf")

        def stub_html(url, headers=None, stream=None, timeout=None):
            return _FakeDownload(body=b"<html>not a pdf</html>", content_type="text/html")

        try:
            download_pdf("https://x.in/fake.pdf", tmp, "s", "2026-11-18", get=stub_html)
            check("non-PDF magic bytes rejected", False)
        except DownloadError:
            check("non-PDF magic bytes rejected",
                  not (Path(tmp) / "s_2026-11-18_fake.pdf").exists())

        def stub_404(url, headers=None, stream=None, timeout=None):
            return _FakeDownload(status_code=404, body=b"not found")

        try:
            download_pdf("https://x.in/missing.pdf", tmp, "s", "2026-11-18", get=stub_404)
            check("404 rejected", False)
        except DownloadError:
            check("404 rejected", True)

        old_cap = downloader.MAX_PDF_BYTES
        downloader.MAX_PDF_BYTES = 100
        try:
            download_pdf("https://x.in/big.pdf", tmp, "s", "2026-11-18", get=stub_get)
            check("size cap enforced", False)
        except DownloadError:
            check("size cap enforced",
                  not (Path(tmp) / "s_2026-11-18_big.pdf").exists())
        finally:
            downloader.MAX_PDF_BYTES = old_cap

    print("== state ==")
    with tempfile.TemporaryDirectory() as tmp:
        st = load_state(tmp)
        check("fresh state is empty", st == {"downloads": {}, "local": {}})
        st["downloads"]["https://x.in/a.pdf"] = {"sha256": "abc", "file": "a.pdf"}
        save_state(tmp, st)
        st2 = load_state(tmp)
        check("save/load round-trip",
              st2["downloads"]["https://x.in/a.pdf"]["sha256"] == "abc")
        check("seen_url / seen_hash",
              seen_url(st2, "https://x.in/a.pdf")
              and seen_hash(st2, "abc")
              and not seen_url(st2, "https://x.in/other.pdf")
              and not seen_hash(st2, "zzz"))

    print("== expiry (real graph, ref 2026-11-18) ==")
    exp = expiry_report(REF)
    by_exam = {r["exam"]: r for r in exp}
    live = [r for r in exp if r["status"] == "live"]
    expired = [r for r in exp if r["status"] == "expired"]
    unknown = [r for r in exp if r["status"] == "unknown"]
    check("6 expired / 2 live / 1 unknown",
          len(expired) == 6 and len(live) == 2 and len(unknown) == 1,
          f"(got {len(expired)}/{len(live)}/{len(unknown)})")
    check("RRB live, 0 days left",
          by_exam["RRB NTPC"]["status"] == "live"
          and by_exam["RRB NTPC"]["days_left"] == 0
          and by_exam["RRB NTPC"]["last_date"] == "2026-11-18")
    check("CGL live via 2027-06-22",
          by_exam["SSC CGL"]["status"] == "live"
          and by_exam["SSC CGL"]["last_date"] == "2027-06-22"
          and by_exam["SSC CGL"]["days_left"] == (date(2027, 6, 22) - REF).days)
    check("CSE expired 2026-02-24",
          by_exam["Civil Services Examination"]["status"] == "expired"
          and by_exam["Civil Services Examination"]["last_date"] == "2026-02-24")
    check("IFoS unknown",
          by_exam["Indian Forest Service Examination"]["status"] == "unknown")

    print("== runner end-to-end (stubbed search + download) ==")
    with tempfile.TemporaryDirectory() as tmp:
        notes = Path(tmp) / "notifications"
        notes.mkdir()
        (notes / "manual_drop.pdf").write_bytes(FAKE_PDF)
        proc = Path(tmp) / "processed_data"
        rep1 = run_update(notes, proc, "test-key", ref_date=REF,
                          search_fn=stub_search, download_fn=stub_download)
        check("run 1: 2 downloaded",
              len(rep1["downloaded"]) == 2, f"(got {len(rep1['downloaded'])})")
        check("run 1: 4 skipped (hash dup, tender, untrusted, non-PDF)",
              len(rep1["skipped"]) == 4
              and sorted(s["reason"] for s in rep1["skipped"]) == [
                  "content hash already seen", "excluded keyword: tender",
                  "not a PDF link", "untrusted domain"],
              f"(got {[s['reason'] for s in rep1['skipped']]})")
        check("run 1: 1 error (RBI discover), run continued",
              len(rep1["errors"]) == 1 and rep1["errors"][0]["source"] == "rbi_grade_b")
        check("run 1: manual drop reported",
              rep1["local_new"] == ["manual_drop.pdf"])
        files = sorted(p.name for p in notes.glob("*.pdf"))
        check("run 1: 3 files on disk (dup file deleted)",
              files == ["ibps_po_2026-11-18_po-corrigendum.pdf",
                        "ibps_po_2026-11-18_po-notification.pdf",
                        "manual_drop.pdf"],
              f"(got {files})")
        st = load_state(proc)
        corr = st["downloads"]["https://ibps.in/po-corrigendum.pdf"]
        check("corrigendum linked to parent",
              corr["amends_exam"] == "IBPS PO"
              and corr["amends_file"] == "ibps_po_2026-11-18_po-notification.pdf")
        report_file = proc / "updates" / "report_2026-11-18.json"
        check("report JSON written",
              report_file.exists()
              and json.loads(report_file.read_text())["date"] == "2026-11-18")

        rep2 = run_update(notes, proc, "test-key", ref_date=REF,
                          search_fn=stub_search, download_fn=stub_download)
        check("run 2: idempotent (0 downloaded, 0 new local)",
              len(rep2["downloaded"]) == 0 and rep2["local_new"] == [])
        check("run 2: seen URLs skipped",
              sorted(s["reason"] for s in rep2["skipped"]).count("URL already seen") == 3)

    print("== runner dry run ==")
    with tempfile.TemporaryDirectory() as tmp:
        notes = Path(tmp) / "notifications"
        notes.mkdir()
        proc = Path(tmp) / "processed_data"
        rep = run_update(notes, proc, "test-key", dry_run=True, ref_date=REF,
                         search_fn=stub_search, download_fn=stub_download)
        check("dry run: 3 would download, 0 downloaded",
              len(rep["would_download"]) == 3 and len(rep["downloaded"]) == 0)
        check("dry run: no files, no state",
              list(notes.glob("*.pdf")) == []
              and not (proc / "updates" / "state.json").exists()
              and (proc / "updates" / "report_2026-11-18.json").exists())

    print("=" * 80)
    print(f"summary: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
