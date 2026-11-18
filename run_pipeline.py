"""Phase 12: the daily pipeline — update, ingest, extract, graph, index.

Replaces run_daily_update.py as the scheduled job: one run keeps the
whole navigator fresh. Extraction is the slow stage (~15s/chunk with
the rotating free LLMs); a day with one new notification takes
roughly 30-60 min. Days with nothing new finish in ~1 min (the
update + hash checks only; graph/index are skipped).

Usage:
  python run_pipeline.py                    # full run
  python run_pipeline.py --dry-run          # discover only, change nothing
  python run_pipeline.py --source ssc_cgl   # one source only (update stage)
  python run_pipeline.py --no-rebuild       # skip the Docker API rebuild
"""
import argparse
import sys

from app.config import TAVILY_API_KEY
from app.pipeline.orchestrator import run_pipeline


def main():
    ap = argparse.ArgumentParser(description="Daily pipeline: update + ingest + extract + graph + index")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover only, change nothing")
    ap.add_argument("--source", action="append", dest="sources",
                    help="only this source key (repeatable)")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="skip the Docker API rebuild")
    args = ap.parse_args()
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY is not set in .env — create a free key at "
              "tavily.com and add it.")
        return 1
    report = run_pipeline(dry_run=args.dry_run, source_keys=args.sources,
                          rebuild=not args.no_rebuild)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
