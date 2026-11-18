"""Job matching (Phase 11): the "Jobs for Me" engine.

For one profile it answers: which govt jobs can I enroll in right
now? Openness comes ONLY from the Phase 11 closing dates (the
submission closing date per exam), never from Phase 5's
max-date heuristic — that heuristic is exactly what labelled RRB
NTPC "ELIGIBLE, last date 18 Nov" when applications closed 6 Nov.
So the verdict/is_open from check_all are deliberately NOT used
here; only its age_ok / qual_ok / relaxation_may_apply (which do
not depend on dates) are reused.

Three lists come back:
- open_jobs: eligible AND a confirmed closing date on/after the
  reference date, soonest deadline first.
- closing_unknown: eligible but no confirmed closing date — the
  user must check the notification. Never presented as open.
- needs_check: qualification passes and age fits only via a
  relaxation (reserved categories) with an open closing date —
  verify the category's rules in the notification.
"""
from datetime import date

from app.eligibility.engine import check_all


def _job_card(res, rules, closing, summaries, ref_date):
    """One job's full details for the UI / API."""
    summary = summaries.get(res.exam, {})
    closing_date = closing["closing_date"]
    return {
        "exam": res.exam,
        "org": rules.org,
        "closing_date": (closing_date.isoformat() if closing_date else None),
        "days_left": ((closing_date - ref_date).days
                      if closing_date else None),
        "closing_label": next(
            (e["label"] for e in closing["dates"]
             if e["date"] == closing_date and e["kind"] == "application"),
            None),
        "age": res.age,
        "age_ranges": [f"{lo}-{hi} years" for lo, hi in rules.base_ranges],
        "min_qual": res.min_qual,
        "posts": res.posts,
        "fees": summary.get("fees", []),
        "documents_count": len(summary.get("documents", [])),
        "summary": summary.get("text"),
        "all_dates": [
            {"date": e["date"].isoformat(), "kind": e["kind"],
             "label": e["label"]}
            for e in closing["dates"]
        ],
    }


def match_jobs(profile, rules, closing, ref_date=None, summaries=None):
    """Match one profile against every exam's rules + closing dates.

    rules: load_exam_rules() output. closing: classify_last_dates()
    output. summaries: get_summaries() output (may be {}).
    """
    ref = ref_date or date.today()
    summaries = summaries or {}
    results = check_all(profile, rules, ref)
    open_jobs = []
    closing_unknown = []
    needs_check = []
    for res in results:
        r = rules[res.exam]
        c = closing.get(res.exam, {"closing_date": None, "dates": []})
        closing_date = c["closing_date"]
        is_open = closing_date is not None and closing_date >= ref
        would_be_eligible = res.age_ok and res.qual_ok
        if would_be_eligible and is_open:
            open_jobs.append(_job_card(res, r, c, summaries, ref))
        elif would_be_eligible and closing_date is None:
            closing_unknown.append(_job_card(res, r, c, summaries, ref))
        elif res.relaxation_may_apply and res.qual_ok and is_open:
            card = _job_card(res, r, c, summaries, ref)
            card["relaxation_note"] = (
                "Age fits only via a relaxation — verify your category's "
                "rules in the notification.")
            needs_check.append(card)
    open_jobs.sort(key=lambda j: (j["closing_date"], j["exam"]))
    needs_check.sort(key=lambda j: (j["closing_date"], j["exam"]))
    closing_unknown.sort(key=lambda j: j["exam"])
    checked = len(results)
    return {
        "as_of": ref.isoformat(),
        "open_jobs": open_jobs,
        "closing_unknown": closing_unknown,
        "needs_check": needs_check,
        "checked_exams": checked,
        "counts": {
            "open": len(open_jobs),
            "closing_unknown": len(closing_unknown),
            "needs_check": len(needs_check),
            "other": checked - len(open_jobs) - len(closing_unknown)
                    - len(needs_check),
        },
    }
