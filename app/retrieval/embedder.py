"""Local embeddings with BAAI/bge-base-en-v1.5 (768-dim).

Same model as the Enterprise RAG project: free, local, no API calls.
BGE models want an instruction prefix on QUERIES (not on passages):
"Represent this sentence for searching relevant passages: "
"""
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-base-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(MODEL_NAME)
    return _embedder


def embed_passages(texts, batch_size=32):
    """Embed document chunks. Returns L2-normalized float32 array."""
    model = get_embedder()
    return model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )


def embed_query(text):
    """Embed one search query (with the BGE instruction prefix)."""
    model = get_embedder()
    return model.encode(
        [QUERY_INSTRUCTION + text],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )[0]
