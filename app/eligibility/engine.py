"""Eligibility checks: profile vs exam rules.

Verdicts:
- ELIGIBLE: open, age and qualification both pass.
- NOT_ELIGIBLE: open, but age and/or qualification fails.
- CLOSED: the last date has passed (age/qualification are still
  checked, so the reasons show would-be eligibility).
- RELAXATION_MAYBE: open, qualification passes, age exceeds the base
  max but falls inside a relaxation band/max — the graph cannot say
  which category a relaxation belongs to, so this is a "verify in
  the notification" verdict, never a clean yes.
- UNKNOWN: the graph holds no age/qualification rules for the exam.

Age is computed as of the reference date (default: today). Official
notifications state their own cut-off dates ("as on ..."), which the
graph does not carry as structured data — a birthday near a cut-off
can flip a borderline age verdict, so borderline cases should be
checked against the notification.
"""
from dataclasses import dataclass, field
from datetime import date

from app.eligibility.profile import LEVEL_RANK, Profile
from app.eligibility.rules import ExamRules

VERDICTS = ("ELIGIBLE", "NOT_ELIGIBLE", "CLOSED", "UNKNOWN", "RELAXATION_MAYBE")


@dataclass
class ExamResult:
    exam: str
    verdict: str
    age: int
    reasons: list[str] = field(default_factory=list)
    age_ok: bool = False
    qual_ok: bool = False
    is_open: bool | None = None
    relaxation_may_apply: bool = False
    last_date: date | None = None
    min_qual: str | None = None
    posts: list[str] = field(default_factory=list)


def check_exam(profile: Profile, rules: ExamRules, ref_date: date) -> ExamResult:
    age = profile.age_on(ref_date)
    res = ExamResult(exam=rules.exam, verdict="UNKNOWN", age=age,
                     posts=rules.posts, min_qual=rules.min_qual)
    # --- dates / open ---
    if rules.last_dates:
        res.last_date = max(rules.last_dates)
        res.is_open = res.last_date >= ref_date
        if len(rules.last_dates) > 1:
            res.reasons.append(
                "multiple last dates in graph: "
                + ", ".join(d.isoformat() for d in rules.last_dates)
                + " (using latest)")
    # --- age ---
    matched = [r for r in rules.base_ranges if r[0] <= age <= r[1]]
    if matched:
        res.age_ok = True
        res.reasons.append(f"age {age} in base range {matched[0][0]}-{matched[0][1]} years")
    elif rules.base_ranges:
        res.reasons.append(
            "age {} outside base range(s) {}".format(
                age, ", ".join(f"{lo}-{hi}" for lo, hi in rules.base_ranges)))
    # relaxation (reserved categories only, and only when too OLD —
    # relaxation extends the max, it never lowers the min)
    base_max = max((hi for _, hi in rules.base_ranges), default=None)
    if not res.age_ok and profile.is_reserved and base_max is not None and age > base_max:
        if any(age <= s for s in rules.relaxation_singles):
            res.relaxation_may_apply = True
            res.reasons.append(
                f"age {age} within a relaxation max ({max(rules.relaxation_singles)} years) "
                "— verify your category's rules in the notification")
        else:
            band = next((b for b in rules.relaxation_bands if b[0] <= age <= b[1]), None)
            if band is not None:
                res.relaxation_may_apply = True
                res.reasons.append(
                    f"age {age} in relaxation band {band[0]}-{band[1]} years "
                    "— verify your category's rules in the notification")
    # --- qualification ---
    if rules.min_qual is not None:
        need = LEVEL_RANK[rules.min_qual]
        res.qual_ok = profile.level_rank >= need
        if res.qual_ok:
            res.reasons.append(
                f"qualification {profile.qualification} >= required {rules.min_qual}")
        else:
            res.reasons.append(
                f"qualification {profile.qualification} below required {rules.min_qual}")
    # --- verdict ---
    has_rules = bool(rules.base_ranges or rules.min_qual is not None)
    if not has_rules:
        res.verdict = "UNKNOWN"
        res.reasons.append("no age/qualification rules found in the graph for this exam")
    elif res.is_open is False:
        res.verdict = "CLOSED"
        res.reasons.append(f"last date {res.last_date.isoformat()} has passed")
    elif res.age_ok and res.qual_ok:
        res.verdict = "ELIGIBLE"
    elif res.relaxation_may_apply and res.qual_ok:
        res.verdict = "RELAXATION_MAYBE"
    else:
        res.verdict = "NOT_ELIGIBLE"
    return res


def check_all(profile: Profile, exams: dict[str, ExamRules], ref_date: date):
    return [check_exam(profile, exams[name], ref_date) for name in sorted(exams)]
