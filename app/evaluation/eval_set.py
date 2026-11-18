"""The labeled eval set: 20 fixed questions with known answers.

Each item:
  id         stable id (q01.. / a01..)
  question   the question, as a user would ask it
  kind       "qa" (answerable from the corpus) or "abstain" (the
             correct behaviour is to abstain)
  expected   acceptable answer strings, matched as case-insensitive
             substrings of the generated answer. The special token
             "ABSTAIN" means abstaining counts as correct — used for
             q05, an in-scope question whose evidence is missing
             (the SBI fee fact was dropped in Phase 3 and the fee
             chunk is not in the top-5 dense chunks, verified).
  gold_facts facts that MUST be in the retrieved evidence for the
             question to be answerable. Each spec is {"relation",
             "tail"} (exact tail match) or {"relation",
             "tail_contains"} (substring, case-insensitive).
             Tails are matched on DISPLAY text — the graph stores
             qualification levels as node ids but displays the full
             qualification text, so REQUIRES specs use tail_contains.

Every gold fact and expected string was verified against the real
graph + index before this set was frozen (probe, 2026-11-18):
all 15 gold facts are retrieved, and every expected string is in
the evidence (dates in ISO form; the LLM converts them, as Phase 6
showed for q02). The abstain probes split across the two lines of
defence: a02/a03 fall below the 0.55 gate (top scores 0.507/0.474),
a01/a04 pass it (0.635/0.629) so the LLM itself must abstain.
"""
EVAL_SET = [
    # q01 CORRECTED 2026-11-18 (run 3): the notification itself says
    # "Minimum: 20 years Maximum: 30 years" (chunk 10). The graph's
    # "21-30 years" fact is an extraction hallucination — no PO chunk
    # contains "21-30" or even "21 years". "20 years" is the true
    # minimum; the old expected/gold trusted the hallucinated fact.
    {"id": "q01", "question": "What is the age limit for IBPS PO?",
     "kind": "qa", "expected": ["20-30", "20 years"],
     "gold_facts": [{"relation": "HAS_AGE_LIMIT", "tail": "20 years"}]},
    {"id": "q02", "question": "What is the last date to apply for SSC CGL?",
     "kind": "qa", "expected": ["22 June 2026", "2026-06-22"],
     "gold_facts": [{"relation": "LAST_DATE", "tail": "2026-06-22"}]},
    {"id": "q03", "question": "What qualification is needed for RBI Grade B?",
     "kind": "qa", "expected": ["graduation"],
     "gold_facts": [{"relation": "REQUIRES", "tail_contains": "Graduation"}]},
    {"id": "q04", "question": "Which posts does UPSC offer?",
     "kind": "qa", "expected": ["Indian Administrative Service"],
     "gold_facts": [{"relation": "OFFERS",
                     "tail": "Indian Administrative Service"}]},
    {"id": "q05", "question": "What is the application fee for SBI PO?",
     "kind": "qa", "expected": ["ABSTAIN"], "gold_facts": []},
    # q06 CORRECTED 2026-11-18 (run 3): the RRB notification's
    # IMPORTANT DATES say application closing is 06.11.2026; 08.11
    # is the fee-payment last date and 18.11 is the MODIFICATION
    # window end — not an application deadline. The old expected
    # (18 Nov) used the max-date heuristic, which is wrong here.
    {"id": "q06", "question": "What is the last date to apply for RRB NTPC?",
     "kind": "qa", "expected": ["6 November 2026", "2026-11-06"],
     "gold_facts": [{"relation": "LAST_DATE", "tail": "2026-11-06"}]},
    {"id": "q07", "question": "What is the age limit for IBPS Clerk?",
     "kind": "qa", "expected": ["20-28"],
     "gold_facts": [{"relation": "HAS_AGE_LIMIT", "tail": "20-28 years"}]},
    {"id": "q08", "question": "What is the age limit for SBI PO?",
     "kind": "qa", "expected": ["21-30"],
     "gold_facts": [{"relation": "HAS_AGE_LIMIT", "tail": "21-30 years"}]},
    {"id": "q09", "question": "Which organisation conducts SSC CGL?",
     "kind": "qa", "expected": ["Staff Selection Commission"],
     "gold_facts": [{"relation": "CONDUCTED_BY",
                     "tail": "Staff Selection Commission"}]},
    {"id": "q10", "question": "Which organisation conducts IBPS PO?",
     "kind": "qa", "expected": ["IBPS"],
     "gold_facts": [{"relation": "CONDUCTED_BY", "tail": "IBPS"}]},
    {"id": "q11", "question": "What is the application fee for IBPS PO?",
     "kind": "qa", "expected": ["850"],
     "gold_facts": [{"relation": "APPLICATION_FEE", "tail_contains": "850"}]},
    {"id": "q12", "question": "What qualification is needed for SSC CHSL?",
     "kind": "qa", "expected": ["12th"],
     "gold_facts": [{"relation": "REQUIRES", "tail_contains": "12th Standard"}]},
    {"id": "q13", "question": "What is the age limit for SSC CHSL?",
     "kind": "qa", "expected": ["18-27"],
     "gold_facts": [{"relation": "HAS_AGE_LIMIT", "tail": "18-27 years"}]},
    {"id": "q14", "question": "What is the age limit for RBI Grade B?",
     "kind": "qa", "expected": ["21-30"],
     "gold_facts": [{"relation": "HAS_AGE_LIMIT", "tail": "21-30 years"}]},
    {"id": "q15", "question": "What is the last date to apply for UPSC Civil Services?",
     "kind": "qa", "expected": ["24 February 2026", "2026-02-24"],
     "gold_facts": [{"relation": "LAST_DATE", "tail": "2026-02-24"}]},
    {"id": "q16", "question": "What is the last date to apply for IBPS Clerk?",
     "kind": "qa", "expected": ["21 August 2026", "2026-08-21"],
     "gold_facts": [{"relation": "LAST_DATE", "tail": "2026-08-21"}]},
    {"id": "a01", "question": "What is the age limit for NDA?",
     "kind": "abstain", "expected": ["ABSTAIN"], "gold_facts": []},
    {"id": "a02", "question": "What will the weather be tomorrow in Delhi?",
     "kind": "abstain", "expected": ["ABSTAIN"], "gold_facts": []},
    {"id": "a03", "question": "Who won the cricket match yesterday?",
     "kind": "abstain", "expected": ["ABSTAIN"], "gold_facts": []},
    {"id": "a04", "question": "What is the application fee for the NDA exam?",
     "kind": "abstain", "expected": ["ABSTAIN"], "gold_facts": []},
]
