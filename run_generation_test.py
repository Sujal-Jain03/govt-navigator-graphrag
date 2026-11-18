"""Test generation with citations on the 8 Phase 4 queries + 2 probes.

Q9 (weather) should abstain WITHOUT an LLM call (the gate fires).
Q10 (UPSC NDA) is the conflation probe: 'UPSC' matches CSE/IFoS, so the
gate does not fire — the right answer is that NDA is not in the
evidence, not CSE's age limit presented as NDA's.
Q5 (SBI fee) is the hallucination probe: Rs 750 is NOT in the evidence
(no fee fact in the graph, no SBI chunk in the dense top-5). If the
answer states the fee, that is training-data leakage, not retrieval.
"""
from app.generation.generator import build_messages, generate
from app.retrieval.retriever import HybridRetriever

QUERIES = [
    "What is the age limit for IBPS PO?",
    "What is the last date to apply for SSC CGL?",
    "What qualification is needed for RBI Grade B?",
    "Which posts does UPSC offer?",
    "What is the application fee for SBI PO?",
    "What posts are available in RRB NTPC?",
    "What documents are needed for IBPS Clerk?",
    "What is the age limit for IAS?",
    "What is the weather in Delhi tomorrow?",
    "What is the age limit for UPSC NDA?",
]

if __name__ == "__main__":
    retriever = HybridRetriever()
    print(f"Index: {len(retriever.meta)} chunks, graph: "
          f"{retriever.graph.number_of_nodes()} nodes / {retriever.graph.number_of_edges()} edges")
    n_answered = n_abstained = n_failed = n_bad_cites = 0
    for i, q in enumerate(QUERIES):
        print("=" * 80)
        print(f"Q{i + 1}: {q}")
        ret = retriever.retrieve(q)
        matched = [e["display_name"] for e in ret["matched_entities"]]
        top = ret["chunks"][0]["score"] if ret["chunks"] else 0.0
        print(f"  matched: {matched} | top chunk score: {top:.3f} | "
              f"facts: {ret['facts_total']} (showing {len(ret['facts'])})")
        try:
            res = generate(q, ret)
        except Exception as e:  # noqa: BLE001 - report, continue
            print(f"  FAILED: {e}")
            n_failed += 1
            continue
        if i == 0 and res.evidence:
            msgs = build_messages(q, res.evidence)
            print("  --- user prompt (first 1200 chars) ---")
            print("  " + msgs[1]["content"][:1200].replace("\n", "\n  "))
            print(f"  --- prompt chars total: {len(msgs[1]['content'])} ---")
        print(f"  model: {res.model} | abstained: {res.abstained} | "
              f"cited: {res.cited} | citations_valid: {res.citations_valid}")
        print(f"  answer: {res.guard_flags}")
        if res.cited and not res.citations_valid:
            n_bad_cites += 1
        if res.abstained:
            n_abstained += 1
        else:
            n_answered += 1
            for c in res.cited:
                if 1 <= c <= len(res.evidence):
                    item = res.evidence[c - 1]
                    print(f"    [{c}] ({item.kind}) {item.text[:140]}")
    print("=" * 80)
    print(f"summary: answered {n_answered}, abstained {n_abstained}, "
          f"failed {n_failed}, bad citations {n_bad_cites}")
