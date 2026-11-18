"""Run the eval set end to end: retrieve -> generate -> score -> report.

One generation failure never kills the run (the row is recorded with
an error). The report (summary + full per-question rows, including
answers) is written to processed_data/evaluation/report_<date>.json
so runs can be compared over time.
"""
import json
from datetime import date
from pathlib import Path

from app.config import PROCESSED_DIR
from app.evaluation.eval_set import EVAL_SET
from app.evaluation.metrics import gold_recall, score_question, summarize
from app.generation.generator import generate
from app.retrieval.retriever import HybridRetriever


def run_evaluation(processed_dir=None, llm_fn=None, question_ids=None,
                   ref_date=None, verbose=True):
    """Run the eval set. llm_fn is dependency injection for testing
    (same pattern as generate); None uses the real rotation client.
    Returns (summary, rows)."""
    proc = Path(processed_dir) if processed_dir else PROCESSED_DIR
    ref = ref_date or date.today()
    retriever = HybridRetriever()
    items = [i for i in EVAL_SET
             if question_ids is None or i["id"] in question_ids]
    rows = []
    for item in items:
        retrieval = retriever.retrieve(item["question"])
        hits, total = gold_recall(retrieval.get("facts", []),
                                  item["gold_facts"])
        try:
            result = generate(item["question"], retrieval, llm_fn=llm_fn)
        except Exception as e:  # noqa: BLE001 - recorded, run continues
            row = score_question(item, None, hits, total)
            row["error"] = f"{type(e).__name__}: {e}"
            rows.append(row)
            if verbose:
                print(f"{item['id']} [{item['kind']}] ERROR: {row['error']}")
            continue
        row = score_question(item, result, hits, total)
        rows.append(row)
        if verbose:
            mark = "OK  " if row["correct"] else "MISS"
            print(f"{item['id']} [{item['kind']}] {mark} | "
                  f"model={row['model']} | gold {hits}/{total} | "
                  f"cited {row['cited']}")
    summary = summarize(rows)
    report = {"date": ref.isoformat(), "summary": summary, "rows": rows}
    report_path = proc / "evaluation" / f"report_{ref.isoformat()}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if verbose:
        print(f"report: {report_path}")
        print(f"QA accuracy: {summary['qa_correct']}/{summary['qa_total']} "
              f"({summary['qa_accuracy']}) | abstain: "
              f"{summary['abstain_correct']}/{summary['abstain_total']} "
              f"({summary['abstain_accuracy']}) | gold recall: "
              f"{summary['gold_hits']}/{summary['gold_total']} "
              f"({summary['gold_recall']}) | citations: "
              f"{summary['cited_ok']}/{summary['answered']} "
              f"({summary['citation_rate']}) | errors: {summary['errors']}")
    return summary, rows
