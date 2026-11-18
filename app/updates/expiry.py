"""Expiry report: which exams in the graph are past their last date.

Reuses the Phase 5 rules (last dates come from the graph's
LAST_DATE edges; the max is used, as in the eligibility engine).
Expired exams are REPORTED, not deleted — people still ask about
past exams, and the eligibility engine already returns CLOSED
for them.
"""
from datetime import date

from app.eligibility.rules import load_exam_rules


def expiry_report(ref_date=None):
    """One row per exam: {exam, last_date, status, days_left}.
    status is live / expired / unknown (no last date in the graph)."""
    ref = ref_date or date.today()
    report = []
    for exam, rules in sorted(load_exam_rules().items()):
        if not rules.last_dates:
            report.append({"exam": exam, "last_date": None,
                           "status": "unknown", "days_left": None})
            continue
        last = max(rules.last_dates)
        days = (last - ref).days
        report.append({"exam": exam, "last_date": last.isoformat(),
                       "status": "live" if days >= 0 else "expired",
                       "days_left": days})
    return report
