"""Daily update: discover new official notifications, download, dedupe, report.

Run once a day (e.g. Windows Task Scheduler). Needs TAVILY_API_KEY in .env
(free at tavily.com, no card, 1,000 credits/month).

Usage:
  python run_daily_update.py                 # full run
  python run_daily_update.py --dry-run       # discover only, download nothing
  python run_daily_update.py --source ssc_cgl  # one source only
"""
import argparse
import sys

from app.config import DATA_DIR, PROCESSED_DIR, TAVILY_API_KEY
from app.updates.runner import run_update


def main():
    ap = argparse.ArgumentParser(description="Daily notification update")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover only, download nothing")
    ap.add_argument("--source", action="append", dest="sources",
                    help="only this source key (repeatable)")
    args = ap.parse_args()
    if not TAVILY_API_KEY:
        print("TAVILY_API_KEY is not set in .env — create a free key at "
              "tavily.com and add it.")
        return 1
    report = run_update(DATA_DIR, PROCESSED_DIR, TAVILY_API_KEY,
                        dry_run=args.dry_run, source_keys=args.sources)
    print(f"Update {report['date']}{' (dry run)' if report['dry_run'] else ''}: "
          f"{len(report['downloaded'])} downloaded, "
          f"{len(report['would_download'])} would download, "
          f"{len(report['skipped'])} skipped, {len(report['errors'])} errors, "
          f"{len(report['local_new'])} new local files")
    for d in report["downloaded"]:
        print(f"  + {d['filename']} ({d['kind']}, {d['source']})")
    for w in report["would_download"]:
        print(f"  ? {w['url']} ({w['kind']}, {w['source']})")
    for s in report["skipped"]:
        print(f"  - {s['url']} [{s['reason']}]")
    for e in report["errors"]:
        print(f"  ! {e['source']}/{e.get('stage')}: {e['error']}")
    for name in report["local_new"]:
        print(f"  local: {name} (manual drop — run ingestion to process)")
    live = [x for x in report["expiry"] if x["status"] == "live"]
    expired = [x for x in report["expiry"] if x["status"] == "expired"]
    unknown = [x for x in report["expiry"] if x["status"] == "unknown"]
    print(f"Expiry: {len(live)} live, {len(expired)} expired, {len(unknown)} unknown")
    for x in live:
        print(f"  live: {x['exam']} — last date {x['last_date']} "
              f"({x['days_left']} days left)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
