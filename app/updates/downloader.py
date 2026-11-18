"""Download a PDF to the notifications folder, with safety checks.

Checks: HTTP 200, %PDF magic bytes (authoritative — Content-Type
is advisory only, some servers serve PDFs as octet-stream), and a
50 MB size cap. Filenames are date-stamped per source so two days
never collide.
"""
import hashlib
import re
from pathlib import Path

import requests

MAX_PDF_BYTES = 50 * 1024 * 1024
CHUNK = 1 << 16


class DownloadError(Exception):
    pass


def safe_filename(source_key, url, today):
    """<source>_<date>_<basename>.pdf, sanitized."""
    base = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "notification.pdf"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return f"{source_key}_{today}_{base}"


def download_pdf(url, dest_dir, source_key, today, get=None):
    """Download one PDF. Returns {path, filename, sha256, bytes}.
    get is injectable for testing."""
    get = get or requests.get
    resp = get(url, headers={"User-Agent": "GovtNavigator/1.0 (+daily-update)"},
               stream=True, timeout=60)
    if resp.status_code != 200:
        raise DownloadError(f"HTTP {resp.status_code} for {url}")
    dest = Path(dest_dir) / safe_filename(source_key, url, today)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Peek at the first chunk before creating the file: a non-PDF
    # (usually an HTML error page) leaves nothing behind.
    chunks = resp.iter_content(CHUNK)
    first = next(chunks, b"")
    if not first.startswith(b"%PDF"):
        raise DownloadError(f"not a PDF (magic bytes) for {url}")
    sha = hashlib.sha256()
    total = 0
    try:
        with open(dest, "wb") as f:
            for chunk in _chain_first(first, chunks):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise DownloadError(f"over {MAX_PDF_BYTES} bytes: {url}")
                sha.update(chunk)
                f.write(chunk)
    except DownloadError:
        dest.unlink(missing_ok=True)
        raise
    return {"path": str(dest), "filename": dest.name,
            "sha256": sha.hexdigest(), "bytes": total}


def _chain_first(first, rest):
    yield first
    yield from rest
