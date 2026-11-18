"""Chunk embedding index: build once, load fast.

Storage is plain numpy + JSON — no vector DB needed for 1,184 vectors:
brute-force cosine over normalized vectors is a single matmul.
"""
import json

import numpy as np

from app.config import PROCESSED_DIR
from app.retrieval.embedder import embed_passages

RETRIEVAL_DIR = PROCESSED_DIR / "retrieval"
EMBEDDINGS_PATH = RETRIEVAL_DIR / "chunk_embeddings.npy"
META_PATH = RETRIEVAL_DIR / "chunk_meta.json"


def _extraction_stems():
    """Stems of the extraction files — the chunk index covers exactly
    the extracted corpus. This also skips any orphaned processed JSON,
    e.g. the old UPSC_CSE_2026_Notification.pdf.json."""
    ext_dir = PROCESSED_DIR / "extractions"
    return sorted(p.name[: -len(".extractions.json")]
                  for p in ext_dir.glob("*.extractions.json"))


def load_chunks():
    """All chunks of the extracted corpus, in stable (stem, index) order."""
    chunks = []
    for stem in _extraction_stems():
        data = json.loads((PROCESSED_DIR / f"{stem}.json").read_text(encoding="utf-8"))
        for c in data["chunks"]:
            chunks.append({
                "id": f"{stem}:{c['chunk_index']}",
                "source": stem,
                "chunk_index": c["chunk_index"],
                "text": c["text"],
            })
    return chunks


def build_chunk_index(batch_size=32):
    """Embed every chunk and save embeddings + metadata. Returns stats."""
    RETRIEVAL_DIR.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks()
    texts = [c["text"] for c in chunks]
    embeddings = embed_passages(texts, batch_size=batch_size)
    np.save(EMBEDDINGS_PATH, embeddings)
    META_PATH.write_text(json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "num_chunks": len(chunks),
        "dim": int(embeddings.shape[1]),
        "embeddings_path": str(EMBEDDINGS_PATH),
        "meta_path": str(META_PATH),
    }


def load_chunk_index():
    embeddings = np.load(EMBEDDINGS_PATH)
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    return embeddings, meta


def search_chunks(query_emb, embeddings, meta, top_k=5):
    """Cosine similarity = dot product (vectors are L2-normalized)."""
    scores = embeddings @ query_emb
    idx = np.argsort(-scores)[:top_k]
    return [
        {"score": float(scores[i]), **meta[i]}
        for i in idx
    ]
