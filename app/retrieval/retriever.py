"""Hybrid retrieval: dense chunk search + graph facts, one call."""
from app.retrieval.embedder import embed_query
from app.retrieval.graph_search import expand, load_graph, match_entities
from app.retrieval.index import load_chunk_index, search_chunks


class HybridRetriever:
    def __init__(self):
        self.embeddings, self.meta = load_chunk_index()
        self.graph = load_graph()

    def retrieve(self, query, top_k_chunks=5, max_facts=50):
        q_emb = embed_query(query)
        chunks = search_chunks(q_emb, self.embeddings, self.meta, top_k=top_k_chunks)
        matched = match_entities(self.graph, query)
        all_facts = expand(self.graph, matched)
        return {
            "query": query,
            "matched_entities": [
                {"id": n,
                 "display_name": self.graph.nodes[n].get("display_name", n),
                 "type": self.graph.nodes[n].get("type")}
                for n in sorted(matched)
            ],
            "chunks": chunks,
            "facts": all_facts[:max_facts],
            "facts_total": len(all_facts),
        }
