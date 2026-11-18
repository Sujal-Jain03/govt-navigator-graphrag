"""Phase 12 orchestrator: one scheduled run keeps the whole navigator fresh.

Stages, in order:
  1. update   — discover + download new official PDFs (Phase 8 runner)
  2. ingest   — chunk new PDFs (Phase 1, incremental by hash)
  3. extract  — entities/relations for new chunks (Phase 2, resumable)
  4. graph    — rebuild the graph (Phase 3)
  5. index    — rebuild the chunk embedding index (Phase 4)
  6. rebuild  — rebuild the Docker API image (its Dockerfile COPYs
                ./processed_data, so the rebuild bakes the fresh
                graph/index in) and recreate the container so it
                serves the fresh data. (A ./processed_data bind
                mount was tried and reverted 18 Nov 2026: the
                Google Drive G: virtual drive cannot serve the
                files to the container, so COPY + rebuild is the
                refresh path — a plain restart would keep serving
                the stale image copy.)

Stages are injectable callables (defaults = the real stage
functions) so the test suite can run the full pipeline logic
without network or LLM calls. Stage protocol:
  update(dry_run=..., source_keys=..., ref_date=...) -> update report
  ingest() -> {"ingested": n, "skipped": n, "new_stems": [...]}
  extract() -> {"done": n, "failed": n, "skipped": n, "orphans": n}
  graph() -> graph stats dict (nodes_total, edges_total, ...)
  index() -> {"num_chunks": n, "dim": n, ...}

The graph + index stages are the expensive ones (the index embed
takes ~7 min on 1,184 chunks), so they run ONLY when something
changed: new PDFs ingested, chunks extracted, or the graph/index
files are missing. A day with nothing new finishes in ~1 min.

Failure handling: if a stage raises, the run stops, the error is
recorded in the report, and run_pipeline returns ok=False (the CLI
exits 1). Later stages never run on a failed stage's output.
"""
import json
import subprocess
from datetime import date
from pathlib import Path

from app.config import BASE_DIR, DATA_DIR, PROCESSED_DIR, TAVILY_API_KEY


def _default_stages():
    from app.extraction.runner import run_all as extract_all
    from app.graph.runner import run as graph_run
    from app.ingestion.processor import main as ingest_main
    from app.retrieval.index import build_chunk_index
    from app.updates.runner import run_update

    return {
        "update": lambda **kw: run_update(
            DATA_DIR, PROCESSED_DIR, TAVILY_API_KEY, **kw),
        "ingest": ingest_main,
        "extract": lambda: extract_all(),
        "graph": graph_run,
        "index": build_chunk_index,
    }


def rebuild_api(timeout=300):
    """Rebuild the Docker API image and recreate its container.

    The Dockerfile COPYs ./processed_data, so the rebuild bakes the
    fresh graph/index into the image. Only the api service is
    rebuilt (the ui image never changes via the pipeline).

    Non-fatal: returns (ok, message). If Docker is not running, the
    data stays stale in the container until the next manual
    `docker compose up --build` — the pipeline itself still
    succeeds, and the failure is recorded in the report.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "--build", "-d", "api"],
            cwd=str(BASE_DIR), capture_output=True, text=True,
            timeout=timeout)
    except FileNotFoundError:
        return False, "docker CLI not found — rebuild the API manually"
    except subprocess.TimeoutExpired:
        return False, f"docker compose up --build timed out after {timeout}s"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return False, f"docker compose up --build failed: {err[:200]}"
    return True, "API image rebuilt, container recreated"


def _finish(report, processed_dir):
    """Write the pipeline report and print a one-line summary."""
    updates_dir = processed_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    path = updates_dir / f"pipeline_report_{report['date']}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    stages = report["stages"]
    print(f"Pipeline {report['date']}: ok={report['ok']} "
          f"changed={report['changed']}")
    for name, info in stages.items():
        print(f"  {name}: {info}")
    if report.get("warning"):
        print(f"  warning: {report['warning']}")
    if report.get("rebuild"):
        print(f"  rebuild: {report['rebuild']}")
    print(f"Report: {path}")
    return report


def run_pipeline(dry_run=False, source_keys=None, stages=None,
                 ref_date=None, rebuild=True, rebuild_fn=None,
                 processed_dir=None):
    """Run the full pipeline. Returns the pipeline report dict."""
    stages = stages or _default_stages()
    processed_dir = processed_dir or PROCESSED_DIR
    ref = ref_date or date.today()
    today = ref.isoformat()
    report = {"date": today, "dry_run": dry_run, "ok": True,
              "stages": {}, "changed": False, "rebuild": None}

    # 1. update
    try:
        upd = stages["update"](dry_run=dry_run, source_keys=source_keys,
                               ref_date=ref)
        report["stages"]["update"] = {
            "downloaded": len(upd["downloaded"]),
            "would_download": len(upd["would_download"]),
            "skipped": len(upd["skipped"]),
            "errors": len(upd["errors"]),
            "local_new": list(upd["local_new"]),
        }
    except Exception as e:  # noqa: BLE001 - recorded, run stops
        report["ok"] = False
        report["stages"]["update"] = {"error": str(e)}
        return _finish(report, processed_dir)

    if dry_run:
        # Discover only: report what WOULD happen, change nothing.
        report["stages"]["ingest"] = {"skipped": "dry run"}
        report["stages"]["extract"] = {"skipped": "dry run"}
        report["stages"]["graph"] = {"skipped": "dry run"}
        report["stages"]["index"] = {"skipped": "dry run"}
        return _finish(report, processed_dir)

    # 2. ingest
    try:
        ing = stages["ingest"]()
        report["stages"]["ingest"] = {
            "ingested": ing["ingested"], "skipped": ing["skipped"],
            "new_stems": ing["new_stems"],
        }
    except Exception as e:  # noqa: BLE001 - recorded, run stops
        report["ok"] = False
        report["stages"]["ingest"] = {"error": str(e)}
        return _finish(report, processed_dir)

    # 3. extract
    try:
        ext = stages["extract"]()
        report["stages"]["extract"] = {
            "done": ext["done"], "failed": ext["failed"],
            "skipped": ext["skipped"], "orphans": ext.get("orphans", 0),
        }
    except Exception as e:  # noqa: BLE001 - recorded, run stops
        report["ok"] = False
        report["stages"]["extract"] = {"error": str(e)}
        return _finish(report, processed_dir)

    # 4-5. graph + index only if something changed.
    graph_path = processed_dir / "graph" / "graph.json"
    index_path = processed_dir / "retrieval" / "chunk_embeddings.npy"
    changed = (
        ing["ingested"] > 0
        or ext["done"] > 0
        or not graph_path.exists()
        or not index_path.exists()
    )
    report["changed"] = changed
    if not changed:
        report["stages"]["graph"] = {"skipped": "nothing changed"}
        report["stages"]["index"] = {"skipped": "nothing changed"}
        return _finish(report, processed_dir)
    if ing["ingested"] > 0 and ext["done"] == 0 and ext["failed"] > 0:
        # New PDFs arrived but NOTHING extracted (all failed) — the
        # graph would gain nothing; skip the rebuild, retry tomorrow.
        report["stages"]["graph"] = {
            "skipped": "new PDFs but extraction produced nothing"}
        report["stages"]["index"] = {
            "skipped": "new PDFs but extraction produced nothing"}
        report["warning"] = ("extraction failed on all new chunks; "
                             "graph/index left stale")
        return _finish(report, processed_dir)

    try:
        graph_stats = stages["graph"]()
        report["stages"]["graph"] = {
            "nodes": graph_stats["nodes_total"],
            "edges": graph_stats["edges_total"],
            "derived_doc_exams": graph_stats.get("derived_doc_exams", {}),
        }
    except Exception as e:  # noqa: BLE001 - recorded, run stops
        report["ok"] = False
        report["stages"]["graph"] = {"error": str(e)}
        return _finish(report, processed_dir)

    try:
        index_stats = stages["index"]()
        report["stages"]["index"] = {
            "num_chunks": index_stats["num_chunks"],
            "dim": index_stats["dim"],
        }
    except Exception as e:  # noqa: BLE001 - recorded, run stops
        report["ok"] = False
        report["stages"]["index"] = {"error": str(e)}
        return _finish(report, processed_dir)

    # 6. rebuild the API image so the container serves fresh data.
    if rebuild:
        fn = rebuild_fn or rebuild_api
        ok, msg = fn()
        report["rebuild"] = {"ok": ok, "message": msg}
    return _finish(report, processed_dir)
