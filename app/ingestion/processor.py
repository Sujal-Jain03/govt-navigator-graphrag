import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, PROCESSED_DIR
from app.ingestion.loaders.pdf import load_pdf
from app.ingestion.splitter import chunk_text


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process_file(pdf_path: Path) -> dict:
    text = load_pdf(pdf_path)
    chunks = chunk_text(text)
    return {
        "source_file": pdf_path.name,
        "file_hash": file_hash(pdf_path),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "num_chunks": len(chunks),
        "chunks": [
            {"chunk_index": i, "text": c}
            for i, c in enumerate(chunks)
        ],
    }


def main() -> dict:
    """Ingest every PDF in DATA_DIR (incremental by hash).

    Returns {"ingested": n, "skipped": n, "new_stems": [stems]}.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(DATA_DIR.glob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs in {DATA_DIR}")
    done = 0
    skipped = 0
    new_stems = []
    for pdf in pdfs:
        out = PROCESSED_DIR / (pdf.stem + ".json")
        if out.exists():
            existing = json.loads(out.read_text(encoding="utf-8"))
            if existing.get("file_hash") == file_hash(pdf):
                print(f"SKIP (already ingested, same hash): {pdf.name}")
                skipped += 1
                continue
        record = process_file(pdf)
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK: {pdf.name} -> {record['num_chunks']} chunks")
        done += 1
        new_stems.append(pdf.stem)
    print(f"Done. Ingested: {done}, skipped: {skipped}")
    return {"ingested": done, "skipped": skipped, "new_stems": new_stems}

if __name__ == "__main__":
    main()
