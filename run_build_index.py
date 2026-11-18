"""Build the chunk embedding index (one-time; re-run after re-ingestion
or after new notifications are extracted)."""
import time

from app.retrieval.index import build_chunk_index

if __name__ == "__main__":
    t0 = time.time()
    stats = build_chunk_index()
    print(f"Indexed {stats['num_chunks']} chunks, dim={stats['dim']}")
    print(f"Saved {stats['embeddings_path']}")
    print(f"Saved {stats['meta_path']}")
    print(f"Took {time.time() - t0:.1f}s")
