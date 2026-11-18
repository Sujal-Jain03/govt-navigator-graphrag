import argparse
import json
import time
from pathlib import Path

from app.config import EXTRACTIONS_DIR, PROCESSED_DIR
from app.extraction.extractor import extract_chunk, get_client

PACE_SECONDS = 5.0


def load_existing(out_path: Path) -> dict:
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    return {}


def run_source(client, src_path: Path, limit: int | None,
               extractions_dir: Path | None = None, extract_fn=None) -> dict:
    """Extract one processed JSON. Returns stats. Skips already-extracted chunks.

    extractions_dir / extract_fn are injectable for testing; the
    defaults are the real extractions dir and extract_chunk.
    """
    data = json.loads(src_path.read_text(encoding="utf-8"))
    extractions_dir = extractions_dir or EXTRACTIONS_DIR
    extract = extract_fn or extract_chunk
    out_path = extractions_dir / (src_path.stem + ".extractions.json")
    existing = load_existing(out_path)
    if existing.get("file_hash") != data["file_hash"]:
        results = {}
    else:
        results = existing.get("results", {})
    stats = {"done": 0, "skipped": 0, "failed": 0}
    chunks = data["chunks"]
    if limit is not None:
        chunks = chunks[:limit]
    for ch in chunks:
        key = str(ch["chunk_index"])
        prev = results.get(key)
        if prev and not prev.get("error"):
            stats["skipped"] += 1
            continue
        try:
            ext = extract(client, ch["text"])
            results[key] = {
                "chunk_index": ch["chunk_index"],
                "entities": ext["entities"],
                "relations": ext["relations"],
                "error": None,
            }
            stats["done"] += 1
        except Exception as e:  # noqa: BLE001 - record and continue
            results[key] = {
                "chunk_index": ch["chunk_index"],
                "entities": [],
                "relations": [],
                "error": str(e),
            }
            stats["failed"] += 1
        out_path.write_text(
            json.dumps(
                {
                    "source_file": data["source_file"],
                    "file_hash": data["file_hash"],
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        time.sleep(PACE_SECONDS)
    return stats


def _has_pdf(src_path: Path, notifications_dir: Path) -> bool:
    """True if a processed JSON corresponds to a real notification PDF.

    Guards against orphan processed files: UPSC_CSE_2026_Notification
    .pdf.json (left over from the double-.pdf filename) has no matching
    PDF, so extracting it would create a duplicate extraction file and
    double-count the document in the graph and the index.
    """
    return (notifications_dir / (src_path.stem + ".pdf")).exists()


def run_all(limit: int | None = None, source: str | None = None,
            processed_dir: Path | None = None,
            notifications_dir: Path | None = None,
            extractions_dir: Path | None = None,
            client=None, extract_fn=None) -> dict:
    """Extract every source (or one). Returns totals.

    Dirs / client / extract_fn are injectable for testing; the
    defaults are the real dirs, get_client() and extract_chunk.
    Processed JSONs with no matching PDF are skipped as orphans.
    """
    from app.config import DATA_DIR

    processed_dir = processed_dir or PROCESSED_DIR
    notifications_dir = notifications_dir or DATA_DIR
    extractions_dir = extractions_dir or EXTRACTIONS_DIR
    extractions_dir.mkdir(parents=True, exist_ok=True)
    if client is None:
        client = get_client()
    sources = sorted(processed_dir.glob("*.json"))
    total = {"done": 0, "skipped": 0, "failed": 0, "orphans": 0}
    for src in sources:
        if source and src.stem != source:
            continue
        if not _has_pdf(src, notifications_dir):
            print(f"SKIP (no matching PDF — orphan): {src.name}")
            total["orphans"] += 1
            continue
        stats = run_source(client, src, limit,
                           extractions_dir=extractions_dir,
                           extract_fn=extract_fn)
        for k in ("done", "skipped", "failed"):
            total[k] += stats[k]
        print(f"{src.stem}: done={stats['done']} skipped={stats['skipped']} failed={stats['failed']}")
    print(f"TOTAL: done={total['done']} skipped={total['skipped']} failed={total['failed']} orphans={total['orphans']}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max chunks per source (smoke test)")
    parser.add_argument("--source", type=str, default=None, help="Only this source stem, e.g. SBI_PO_2026_Notification")
    args = parser.parse_args()
    run_all(limit=args.limit, source=args.source)


if __name__ == "__main__":
    main()
