"""Phase 11 test: last-date classifier + job matcher + /jobs endpoint.

No LLM calls anywhere: the classifier and matcher are
deterministic, and the endpoint test runs the real service over
TestClient (/jobs never touches the retriever, though the app's
lifespan still warms it — same as run_api_test.py).

Sections:
1. Classifier: all 14 LAST_DATE edges + per-exam closing dates.
2. Matcher: Sujal's profile at historical + current ref dates,
   a relaxation profile, and the closing-unknown mechanism.
3. Endpoint: POST /jobs via TestClient.

Run: python run_jobs_test.py
Expected: 42 passed, 0 failed.
"""
import copy
import sys
from datetime import date

from fastapi.testclient import TestClient

from app.api.main import app
from app.eligibility.profile import Profile
from app.eligibility.rules import load_exam_rules
from app.matching.dates import classify_last_dates
from app.matching.matcher import match_jobs

PASSED = 0
FAILED = 0


def check(name, ok):
    global PASSED, FAILED
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    if ok:
        PASSED += 1
    else:
        FAILED += 1


# (exam, date) -> expected kind. Verified against the notification
# text in the Phase 11 probe (see GOAL.md).
EXPECTED_KINDS = {
    ("Civil Services Examination", "2026-02-24"): "application",
    ("IBPS Clerk (CSA)", "2026-08-21"): "application",
    ("IBPS PO", "2026-07-01"): "other",       # fee window opening
    ("IBPS PO", "2026-07-21"): "application",
    ("IBPS PO", "2026-08-07"): "unknown",     # PwBD notice signature
    ("RBI Grade B", "2026-05-20"): "application",
    ("RBI Grade B", "2026-06-05"): "other",   # print deadline
    ("RRB NTPC", "2026-11-06"): "application",
    ("RRB NTPC", "2026-11-08"): "other",      # fee payment date
    ("RRB NTPC", "2026-11-18"): "other",      # modification window
    ("SBI PO", "2026-07-08"): "application",
    ("SSC CGL", "2026-06-22"): "application",
    ("SSC CGL", "2027-06-22"): "other",       # derived (+1 year) date
    ("SSC CHSL", "2026-10-07"): "application",
}

EXPECTED_CLOSING = {
    "Civil Services Examination": "2026-02-24",
    "IBPS Clerk (CSA)": "2026-08-21",
    "IBPS PO": "2026-07-21",
    "RBI Grade B": "2026-05-20",
    "RRB NTPC": "2026-11-06",
    "SBI PO": "2026-07-08",
    "SSC CGL": "2026-06-22",
    "SSC CHSL": "2026-10-07",
}

SUJAL = Profile(name="Sujal", date_of_birth=date(2003, 12, 14),
                qualification="bachelor's degree", category="General")
OBC36 = Profile(name="X", date_of_birth=date(1990, 6, 1),
                qualification="master's degree", category="OBC")


def main():
    rules = load_exam_rules()
    closing = classify_last_dates()

    print("== 1. classifier ==")
    got = {}
    for exam, entry in closing.items():
        for d in entry["dates"]:
            got[(exam, d["date"].isoformat())] = d["kind"]
    check("14 LAST_DATE edges classified", len(got) == 14)
    for key, want in sorted(EXPECTED_KINDS.items()):
        check(f"{key[0]} {key[1]} -> {want}", got.get(key) == want)
    check("8 exams have a closing date",
          len(closing) == 8 and all(
              e["closing_date"] is not None for e in closing.values()))
    for exam, want in sorted(EXPECTED_CLOSING.items()):
        got_date = closing[exam]["closing_date"]
        check(f"{exam} closing {want}",
              got_date is not None and got_date.isoformat() == want)
    sbi_label = next(
        e["label"] for e in closing["SBI PO"]["dates"]
        if e["date"].isoformat() == "2026-07-08")
    check("SBI closing label quotes the registration line",
          "registration" in sbi_label.lower())

    print("== 2. matcher ==")
    m = match_jobs(SUJAL, rules, closing, date(2026, 8, 1))
    open_names = [j["exam"] for j in m["open_jobs"]]
    check("Sujal @ 2026-08-01: open = Clerk, CHSL, RRB (deadline order)",
          open_names == ["IBPS Clerk (CSA)", "SSC CHSL", "RRB NTPC"])
    check("Sujal @ 2026-08-01: days left 20 / 67 / 97",
          [j["days_left"] for j in m["open_jobs"]] == [20, 67, 97])
    check("Sujal @ 2026-08-01: nothing closing-unknown",
          m["closing_unknown"] == [])
    check("Sujal @ 2026-08-01: nothing needs a check",
          m["needs_check"] == [])
    check("Sujal @ 2026-08-01: counts 3 open / 6 other",
          m["counts"]["open"] == 3 and m["counts"]["other"] == 6)
    m = match_jobs(SUJAL, rules, closing, date(2026, 11, 18))
    check("Sujal @ 2026-11-18: nothing open (RRB closed 6 Nov, "
          "not the 18 Nov modification date)",
          m["open_jobs"] == [])
    check("Sujal @ 2026-11-18: counts 0 open / 9 other",
          m["counts"]["open"] == 0 and m["counts"]["other"] == 9)
    m = match_jobs(OBC36, rules, closing, date(2026, 8, 1))
    check("36yo OBC @ 2026-08-01: needs_check = Clerk, RRB",
          [j["exam"] for j in m["needs_check"]]
          == ["IBPS Clerk (CSA)", "RRB NTPC"])
    check("36yo OBC @ 2026-08-01: nothing cleanly open",
          m["open_jobs"] == [])
    dropped = copy.deepcopy(closing)
    dropped["RRB NTPC"] = {"closing_date": None, "dates": []}
    m = match_jobs(SUJAL, rules, dropped, date(2026, 8, 1))
    check("RRB closing dropped: RRB lands in closing_unknown",
          [j["exam"] for j in m["closing_unknown"]] == ["RRB NTPC"])
    check("RRB closing dropped: open = Clerk, CHSL only",
          [j["exam"] for j in m["open_jobs"]]
          == ["IBPS Clerk (CSA)", "SSC CHSL"])

    print("== 3. /jobs endpoint ==")
    with TestClient(app) as client:
        r = client.post("/jobs", json={
            "name": "Sujal", "date_of_birth": "2003-12-14",
            "qualifications": ["bachelor's degree"],
            "category": "General", "ref_date": "2026-08-01",
        })
        body = r.json()
        check("/jobs 200", r.status_code == 200)
        check("/jobs open = 3, first is Clerk",
              body.get("counts", {}).get("open") == 3
              and body["open_jobs"][0]["exam"] == "IBPS Clerk (CSA)")
        check("/jobs first job carries its closing label",
              bool(body["open_jobs"][0].get("closing_label")))
        r = client.post("/jobs", json={
            "date_of_birth": "2003-12-14",
            "qualifications": ["wizardry"], "category": "General",
        })
        check("/jobs bad qualification -> 422", r.status_code == 422)
        r = client.post("/jobs", json={
            "date_of_birth": "2003-12-14", "category": "General",
        })
        check("/jobs no qualification -> 422", r.status_code == 422)
        r = client.post("/jobs", json={
            "qualifications": ["bachelor's degree"],
            "category": "General",
        })
        check("/jobs missing date_of_birth -> 422", r.status_code == 422)

    print("=" * 80)
    print(f"summary: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
