"""Test the Phase 9 evaluation harness.

Sections 1 (metrics) and 2 (runner with a stub LLM) are fully
deterministic — no network, no keys. Section 3 runs the REAL
retriever over all 20 questions (real index + graph, no LLM) and
asserts the gold-fact recall and abstention-gate behaviour the
probe measured. The only untested piece is the real LLM's answers
— those come from Sujal's run of run_evaluation.py.
"""
import json
import tempfile
from datetime import date
from pathlib import Path

from app.evaluation.eval_set import EVAL_SET
from app.evaluation.metrics import (
    answer_matches, gold_recall, score_question, summarize)
from app.evaluation.runner import run_evaluation
from app.generation.generator import ABSTAIN_TEXT, GenerationResult
from app.retrieval.retriever import HybridRetriever

PASSED = 0
FAILED = 0
REF = date(2026, 11, 18)


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS: {name}")
    else:
        FAILED += 1
        print(f"  FAIL: {name} {extra}")


def fake_result(answer, abstained=False, cited=None, valid=True):
    return GenerationResult(answer=answer, abstained=abstained,
                            model="stub", cited=cited or [],
                            citations_valid=valid)


if __name__ == "__main__":
    print("== metrics: answer matching ==")
    check("case-insensitive",
          answer_matches("The age limit is 21-30 Years.", ["21-30"]))
    check("whitespace-tolerant",
          answer_matches("Last date:  22   June 2026.", ["22 june 2026"]))
    check("miss",
          not answer_matches("The age limit is 30 years.", ["21-30"]))
    check("any-of-list",
          answer_matches("Apply by 2026-06-22.",
                         ["22 June 2026", "2026-06-22"]))
    check("non-breaking hyphen (U+2011) matches",
          answer_matches("Age 21" + chr(0x2011) + "30 years.", ["21-30"]))
    check("en dash (U+2013) matches",
          answer_matches("Age 21" + chr(0x2013) + "30 years.", ["21-30"]))
    check("'to' range phrasing matches",
          answer_matches("Age 21 to 30 years.", ["21-30"]))
    check("narrow no-break space (U+202F) matches",
          answer_matches("Age 21-30" + chr(0x202F) + "years.", ["21-30"]))
    check("DD.MM.YYYY date matches ISO",
          answer_matches("Last date is 24.02.2026.", ["2026-02-24"]))
    check("DD/MM/YYYY date matches ISO",
          answer_matches("Last date is 06/11/2026.", ["2026-11-06"]))
    check("ISO date is left alone",
          answer_matches("Last date is 2026-02-24.", ["2026-02-24"])
          and not answer_matches("Last date is 2026-02-24.",
                                 ["2024-02-26"]))

    print("== metrics: gold recall ==")
    facts = [
        {"head": "IBPS PO", "relation": "LAST_DATE", "tail": "2026-07-21"},
        {"head": "PO", "relation": "REQUIRES",
         "tail": "Degree (Graduation) in any discipline"},
    ]
    check("exact tail hit",
          gold_recall(facts, [{"relation": "LAST_DATE",
                               "tail": "2026-07-21"}]) == (1, 1))
    check("tail_contains hit",
          gold_recall(facts, [{"relation": "REQUIRES",
                               "tail_contains": "graduation"}]) == (1, 1))
    check("relation mismatch misses",
          gold_recall(facts, [{"relation": "EXAM_DATE",
                               "tail": "2026-07-21"}]) == (0, 1))
    check("empty gold list",
          gold_recall(facts, []) == (0, 0))

    print("== metrics: scoring ==")
    qa = {"id": "qX", "question": "Q?", "kind": "qa",
          "expected": ["21-30"], "gold_facts": []}
    check("qa correct",
          score_question(qa, fake_result("Age 21-30 years. [1]", cited=[1]),
                         1, 1)["correct"])
    check("qa wrong answer",
          not score_question(qa, fake_result("Age 30 years. [1]", cited=[1]),
                             1, 1)["correct"])
    check("qa abstained counts wrong",
          not score_question(qa, fake_result(ABSTAIN_TEXT, abstained=True),
                             1, 1)["correct"])
    qa_ab = {"id": "qY", "question": "Q?", "kind": "qa",
             "expected": ["ABSTAIN"], "gold_facts": []}
    check("qa abstain allowed counts right",
          score_question(qa_ab, fake_result(ABSTAIN_TEXT, abstained=True),
                         0, 0)["correct"])
    ab = {"id": "aX", "question": "Q?", "kind": "abstain",
          "expected": ["ABSTAIN"], "gold_facts": []}
    check("abstain probe abstained counts right",
          score_question(ab, fake_result(ABSTAIN_TEXT, abstained=True),
                         0, 0)["correct"])
    check("abstain probe answered counts wrong",
          not score_question(ab, fake_result("Age 21-30. [1]", cited=[1]),
                             0, 0)["correct"])
    row = score_question(qa, None, 1, 1)
    check("generation failure row",
          row["correct"] is False and "error" in row)
    rows = [
        {"kind": "qa", "correct": True, "abstained": False,
         "citations_valid": True, "cited": [1], "gold_hits": 1,
         "gold_total": 1},
        {"kind": "qa", "correct": False, "abstained": False,
         "citations_valid": True, "cited": [], "gold_hits": 0,
         "gold_total": 1},
        {"kind": "abstain", "correct": True, "abstained": True,
         "citations_valid": True, "cited": [], "gold_hits": 0,
         "gold_total": 0},
    ]
    s = summarize(rows)
    check("summarize counts",
          s["qa_accuracy"] == 0.5 and s["abstain_accuracy"] == 1.0
          and s["gold_recall"] == 0.5 and s["citation_rate"] == 0.5
          and s["errors"] == 0, f"(got {s})")

    print("== runner with stub LLM (4-question subset) ==")
    calls = {"n": 0}

    def stub_llm(messages):
        calls["n"] += 1
        user_text = " ".join(m.get("content", "") for m in messages
                             if m.get("role") == "user")
        if "age limit for IBPS PO" in user_text:
            return ("The age limit for IBPS PO is 20-30 years. [23]", "stub")
        if "last date to apply for SSC CGL" in user_text:
            return ("The last date to apply for SSC CGL is 22 June 2026. [16]",
                    "stub")
        return (ABSTAIN_TEXT, "stub")

    with tempfile.TemporaryDirectory() as tmp:
        summary, rows = run_evaluation(
            processed_dir=tmp, llm_fn=stub_llm,
            question_ids=["q01", "q02", "a01", "a02"],
            ref_date=REF, verbose=False)
        check("stub: 2/2 qa correct",
              summary["qa_correct"] == 2 and summary["qa_total"] == 2)
        check("stub: 2/2 abstain correct",
              summary["abstain_correct"] == 2
              and summary["abstain_total"] == 2)
        check("stub: gold 2/2",
              summary["gold_hits"] == 2 and summary["gold_total"] == 2)
        check("stub: citations 2/2",
              summary["cited_ok"] == 2 and summary["answered"] == 2)
        check("stub: gate skipped the LLM for a02",
              calls["n"] == 3, f"(got {calls['n']} calls)")
        report_file = Path(tmp) / "evaluation" / "report_2026-11-18.json"
        check("stub: report JSON written",
              report_file.exists()
              and json.loads(report_file.read_text())["summary"]["questions"] == 4)

    print("== real retrieval over all 20 questions ==")
    retriever = HybridRetriever()
    total_hits = total_gold = 0
    tops = {}
    ev_ok = True
    for item in EVAL_SET:
        res = retriever.retrieve(item["question"])
        hits, total = gold_recall(res["facts"], item["gold_facts"])
        total_hits += hits
        total_gold += total
        tops[item["id"]] = res["chunks"][0]["score"] if res["chunks"] else 0.0
        if item["kind"] == "qa" and item["gold_facts"]:
            ev = " ".join(f"{f['head']} {f['relation']} {f['tail']}"
                          for f in res["facts"])
            ev += " " + " ".join(c["text"] for c in res["chunks"])
            want = [e for e in item["expected"] if e != "ABSTAIN"]
            if not answer_matches(ev, want):
                ev_ok = False
                print(f"    evidence lacks expected for {item['id']}: {want}")
    check("gold recall 15/15 on real retrieval",
          (total_hits, total_gold) == (15, 15),
          f"(got {total_hits}/{total_gold})")
    check("every QA expected string is in the evidence", ev_ok)
    check("a02/a03 fall below the 0.55 gate",
          tops["a02"] < 0.55 and tops["a03"] < 0.55,
          f"(got a02={tops['a02']:.3f} a03={tops['a03']:.3f})")
    check("a01/a04 pass the gate (LLM must abstain)",
          tops["a01"] >= 0.55 and tops["a04"] >= 0.55,
          f"(got a01={tops['a01']:.3f} a04={tops['a04']:.3f})")

    print("=" * 80)
    print(f"summary: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
