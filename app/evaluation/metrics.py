"""Pure scoring functions for the eval set. No I/O, no LLM — every
function here is unit-testable with hand-built inputs.

Correctness is a contains-check, not an exact match: the LLM may
phrase the answer freely, but the key fact (a date, an age range,
a name) must appear in it. That is lenient by design — a strict
match would fail on harmless rephrasing — and the per-question
rows in the report keep the full answer, so a pass can always be
inspected.
"""
import re

ABSTAIN_TOKEN = "ABSTAIN"

# The LLM often emits Unicode dashes (gpt-oss-20b wrote U+2011
# NON-BREAKING HYPHEN in run 1) and "21 to 30" phrasing. Both must
# match the ASCII "21-30" in the eval set, or correct answers fail
# on typography. Built with chr() — never literal unusual Unicode
# in source (see the U+3010 lesson in AGENTS.md).
_DASHES = str.maketrans({
    chr(0x2010): "-",  # HYPHEN
    chr(0x2011): "-",  # NON-BREAKING HYPHEN
    chr(0x2012): "-",  # FIGURE DASH
    chr(0x2013): "-",  # EN DASH
    chr(0x2014): "-",  # EM DASH
    chr(0x2212): "-",  # MINUS SIGN
})


def normalize(text):
    """Lowercase + collapse whitespace, for robust substring checks.

    Also folds Unicode dashes to ASCII "-" and age-range phrasing
    ("21 to 30") to the hyphenated form ("21-30"), so the same fact
    matches however the model phrases it. Narrow no-break spaces
    (U+202F) are whitespace to split(), so they collapse here too.
    Dates in DD.MM.YYYY / DD/MM/YYYY / DD-MM-YYYY form are folded
    to ISO (DD-first: the corpus and the model both use Indian
    date order — "24.02.2026" can only be 24 Feb).
    """
    text = str(text).translate(_DASHES)
    text = re.sub(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})",
                  r"\3-\2-\1", text)
    text = re.sub(r"(\d+)\s+to\s+(\d+)", r"\1-\2", text)
    text = re.sub(r"(\d+)\s*-\s*(\d+)", r"\1-\2", text)
    return " ".join(text.lower().split())


def answer_matches(answer, expected):
    """True if any expected string appears in the answer."""
    ans = normalize(answer)
    return any(normalize(e) in ans for e in expected)


def gold_recall(facts, gold_facts):
    """(hits, total): how many gold facts appear in the retrieved
    facts. A spec matches on relation + tail (exact) or
    tail_contains (substring, case-insensitive)."""
    hits = 0
    for spec in gold_facts:
        for f in facts:
            if f.get("relation") != spec["relation"]:
                continue
            tail = f.get("tail", "")
            if "tail" in spec and tail == spec["tail"]:
                hits += 1
                break
            if ("tail_contains" in spec
                    and spec["tail_contains"].lower() in tail.lower()):
                hits += 1
                break
    return hits, len(gold_facts)


def score_question(item, result, gold_hits, gold_total):
    """Score one answered question. result is a GenerationResult
    (or None if generation raised — counted as incorrect)."""
    if result is None:
        return {"id": item["id"], "question": item["question"],
                "kind": item["kind"], "model": None, "abstained": False,
                "answer": "", "correct": False,
                "gold_hits": gold_hits, "gold_total": gold_total,
                "citations_valid": False, "cited": [], "guard_flags": {},
                "error": "generation failed"}
    expected = [e for e in item["expected"] if e != ABSTAIN_TOKEN]
    if item["kind"] == "abstain":
        correct = result.abstained
    elif result.abstained:
        # Abstaining on a QA question is correct only when the item
        # allows it (q05: the evidence genuinely lacks the answer).
        correct = ABSTAIN_TOKEN in item["expected"]
    else:
        correct = answer_matches(result.answer, expected)
    return {"id": item["id"], "question": item["question"],
            "kind": item["kind"], "model": result.model,
            "abstained": result.abstained, "answer": result.answer,
            "correct": correct, "gold_hits": gold_hits,
            "gold_total": gold_total,
            "citations_valid": result.citations_valid,
            "cited": result.cited, "guard_flags": result.guard_flags}


def summarize(rows):
    """Aggregate counts + rates over scored rows."""
    qa = [r for r in rows if r["kind"] == "qa"]
    ab = [r for r in rows if r["kind"] == "abstain"]
    answered = [r for r in rows if not r["abstained"] and "error" not in r]
    cited_ok = [r for r in answered
                if r["citations_valid"] and len(r["cited"]) >= 1]
    gold_hits = sum(r["gold_hits"] for r in rows)
    gold_total = sum(r["gold_total"] for r in rows)

    def rate(n, d):
        return round(n / d, 3) if d else None

    return {
        "questions": len(rows),
        "qa_total": len(qa),
        "qa_correct": sum(1 for r in qa if r["correct"]),
        "qa_accuracy": rate(sum(1 for r in qa if r["correct"]), len(qa)),
        "abstain_total": len(ab),
        "abstain_correct": sum(1 for r in ab if r["correct"]),
        "abstain_accuracy": rate(sum(1 for r in ab if r["correct"]), len(ab)),
        "gold_hits": gold_hits,
        "gold_total": gold_total,
        "gold_recall": rate(gold_hits, gold_total),
        "answered": len(answered),
        "cited_ok": len(cited_ok),
        "citation_rate": rate(len(cited_ok), len(answered)),
        "errors": sum(1 for r in rows if "error" in r),
    }
