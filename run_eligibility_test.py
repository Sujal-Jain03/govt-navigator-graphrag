"""Test the eligibility engine on fixed profiles."""
from datetime import date

from app.eligibility.engine import check_all
from app.eligibility.profile import Profile
from app.eligibility.rules import load_exam_rules

REF_DATE = date(2026, 11, 18)

PROFILES = [
    Profile(name="Sujal", date_of_birth=date(2003, 12, 14),
            qualification="bachelor's degree", category="General"),
    Profile(name="17yo, XII", date_of_birth=date(2009, 6, 1),
            qualification="class xii", category="General"),
    Profile(name="28yo, Master's", date_of_birth=date(1998, 3, 10),
            qualification="master's degree", category="General"),
    Profile(name="36yo, OBC", date_of_birth=date(1990, 5, 20),
            qualification="bachelor's degree", category="OBC"),
    Profile(name="20yo, Class X", date_of_birth=date(2006, 1, 15),
            qualification="class x", category="General"),
]

if __name__ == "__main__":
    exams = load_exam_rules()
    print(f"Loaded rules for {len(exams)} exams (ref date {REF_DATE.isoformat()})")
    for profile in PROFILES:
        print("=" * 80)
        print(f"{profile.name}: age {profile.age_on(REF_DATE)}, "
              f"{profile.qualification}, {profile.category}")
        results = check_all(profile, exams, REF_DATE)
        for r in results:
            last = f", last date {r.last_date.isoformat()}" if r.last_date else ", last date unknown"
            today = " (TODAY)" if r.last_date == REF_DATE else ""
            print(f"  {r.exam}: {r.verdict}{last}{today}")
            for reason in r.reasons:
                print(f"      - {reason}")
        counts = {}
        for r in results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
        print("  summary:", ", ".join(f"{v} {n}" for v, n in sorted(counts.items())))
