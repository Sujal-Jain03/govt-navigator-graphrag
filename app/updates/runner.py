"""Daily update loop: discover -> download -> dedupe -> report.

One source failing (search error, download error) never kills the
run — it is recorded and the loop continues. A dry run discovers
and reports but downloads nothing and writes no state.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

from app.updates import discovery, downloader
from app.updates.expiry import expiry_report
from app.updates.sources import SOURCES, get_source
from app.updates.state import load_state, save_state, seen_hash, seen_url


def scan_local(notifications_dir, state):
    """PDFs dropped manually into the notifications folder that the
    state file does not know yet. Files this updater downloaded are
    excluded — they are tracked under downloads, not local."""
    notes = Path(notifications_dir)
    known = set(state.get("local", {}))
    known |= {r.get("file") for r in state.get("downloads", {}).values()
              if r.get("file")}
    return sorted(p.name for p in notes.glob("*")
                  if p.is_file() and p.suffix.lower() == ".pdf"
                  and p.name not in known)


def _sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _latest_file_for_source(state, source_key, notifications_dir):
    """Most recent downloaded file for a source (corrigendum parent).
    Only files that still exist on disk count — a record whose file
    was deleted (e.g. junk removed after download) is skipped."""
    best = None
    for record in state.get("downloads", {}).values():
        if (record.get("source") == source_key
                and record.get("status") == "downloaded"
                and record.get("file")
                and (Path(notifications_dir) / record["file"]).exists()):
            if best is None or record.get("discovered_at", "") > best.get("discovered_at", ""):
                best = record
    return best["file"] if best else None



def run_update(notifications_dir, processed_dir, api_key, dry_run=False,
               source_keys=None, ref_date=None, search_fn=None, download_fn=None):
    """Run one update. Returns the report dict (also written to
    processed_data/updates/report_<date>.json). search_fn/download_fn
    are injectable for testing."""
    ref = ref_date or date.today()
    today = ref.isoformat()
    state = load_state(processed_dir)
    sources = [get_source(k) for k in source_keys] if source_keys else SOURCES
    download = download_fn or downloader.download_pdf
    report = {"date": today, "dry_run": dry_run, "downloaded": [],
              "would_download": [], "skipped": [], "errors": [],
              "local_new": [], "expiry": []}
    for source in sources:
        try:
            candidates = discovery.discover(source, api_key, search_fn=search_fn)
        except Exception as e:  # noqa: BLE001 - one source must not kill the run
            report["errors"].append({"source": source["key"], "stage": "discover",
                                     "error": str(e)})
            continue
        for cand in candidates:
            url = cand["url"]
            if not cand["trusted"]:
                report["skipped"].append({**cand, "reason": "untrusted domain"})
                continue
            if not cand["is_pdf"]:
                report["skipped"].append({**cand, "reason": "not a PDF link"})
                continue
            if cand.get("filter_reason"):
                report["skipped"].append({**cand, "reason": cand["filter_reason"]})
                continue
            if seen_url(state, url):
                report["skipped"].append({**cand, "reason": "URL already seen"})
                continue
            if dry_run:
                report["would_download"].append(cand)
                continue
            try:
                dl = download(url, notifications_dir, source["key"], today)
            except Exception as e:  # noqa: BLE001 - recorded, loop continues
                report["errors"].append({"source": source["key"], "stage": "download",
                                         "url": url, "error": str(e)})
                continue
            if seen_hash(state, dl["sha256"]):
                # Same content under a new URL: drop the duplicate file,
                # keep the URL record so it is never fetched again.
                Path(dl["path"]).unlink(missing_ok=True)
                state["downloads"][url] = {
                    "sha256": dl["sha256"], "file": None, "source": source["key"],
                    "kind": cand["kind"], "discovered_at": today, "status": "duplicate"}
                report["skipped"].append({**cand, "reason": "content hash already seen"})
                continue
            record = {"sha256": dl["sha256"], "file": dl["filename"],
                      "source": source["key"], "kind": cand["kind"],
                      "discovered_at": today, "status": "downloaded"}
            if cand["kind"] == "corrigendum":
                record["amends_exam"] = source["exam"]
                record["amends_file"] = _latest_file_for_source(state, source["key"], notifications_dir)
            state["downloads"][url] = record
            report["downloaded"].append({**cand, "filename": dl["filename"],
                                         "sha256": dl["sha256"], "bytes": dl["bytes"]})
    report["local_new"] = scan_local(notifications_dir, state)
    if not dry_run:
        for name in report["local_new"]:
            state["local"][name] = {
                "sha256": _sha256_file(Path(notifications_dir) / name),
                "first_seen": today}
        save_state(processed_dir, state)
    try:
        report["expiry"] = expiry_report(ref)
    except Exception as e:  # noqa: BLE001 - a missing graph must not kill the run
        report["errors"].append({"source": "-", "stage": "expiry", "error": str(e)})
    report_path = Path(processed_dir) / "updates" / f"report_{today}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
