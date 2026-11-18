"""Smoke-test hybrid retrieval on fixed queries."""
from app.retrieval.retriever import HybridRetriever

QUERIES = [
    "What is the age limit for IBPS PO?",
    "What is the last date to apply for SSC CGL?",
    "What qualification is required for RBI Grade B?",
    "What posts are offered in UPSC Civil Services?",
    "What is the application fee for SBI PO?",
    "Which posts are there in RRB NTPC for graduates?",
    "What documents are needed for IBPS Clerk?",
    "What is the age limit for IAS?",
]

if __name__ == "__main__":
    retriever = HybridRetriever()
    print(f"Index: {len(retriever.meta)} chunks, "
          f"graph: {retriever.graph.number_of_nodes()} nodes / "
          f"{retriever.graph.number_of_edges()} edges")
    for q in QUERIES:
        res = retriever.retrieve(q)
        print("=" * 80)
        print("Q:", q)
        ents = [f"{e['display_name']} ({e['type']})" for e in res["matched_entities"]]
        print("Matched entities:", ents or "none")
        print(f"--- top {len(res['chunks'])} chunks ---")
        for c in res["chunks"]:
            snippet = c["text"][:150].replace("\n", " ")
            print(f"[{c['score']:.3f}] {c['source']}#{c['chunk_index']}: {snippet}...")
        print(f"--- {len(res['facts'])} graph facts (of {res['facts_total']}) ---")
        for f in res["facts"]:
            hop = " [hop 2]" if f["hop"] == 2 else ""
            print(f"  {f['head']} --{f['relation']}--> {f['tail']}{hop}")
