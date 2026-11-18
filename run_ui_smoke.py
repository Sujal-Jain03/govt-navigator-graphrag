"""Phase 10 UI smoke test — runs the Streamlit app headlessly
(AppTest) against the live API. Start the API first in another
terminal (python run_api.py), then run: python run_ui_smoke.py
Phase 11 added the Jobs for Me page check.
Expected: 9 passed, 0 failed.
"""


import sys

from streamlit.testing.v1 import AppTest

PASSED = 0
FAILED = 0


def check(name, ok):
    global PASSED, FAILED
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    if ok:
        PASSED += 1
    else:
        FAILED += 1


def main():
    at = AppTest.from_file("ui/app.py", default_timeout=120)
    at.run()
    check("app runs with no exception", len(at.exception) == 0)
    check("top nav present",
          len(at.segmented_control) == 1
          and "Check Eligibility" in at.segmented_control[0].options
          and "Jobs for Me" in at.segmented_control[0].options)

    at.segmented_control[0].set_value("Browse Exams")
    at.run()
    check("exams page renders exam names",
          len(at.exception) == 0
          and any("IBPS PO" in m.value for m in at.markdown))

    at.segmented_control[0].set_value("Live Updates")
    at.run()
    check("updates page renders the deadlines table",
          len(at.exception) == 0 and len(at.dataframe) == 1)

    at.segmented_control[0].set_value("Check Eligibility")
    at.run()
    check("eligibility page renders the sections",
          len(at.exception) == 0
          and any(b.label == "Add education" for b in at.button)
          and len(at.number_input) == 1
          and {s.value for s in at.subheader}
          >= {"Personal Information", "Education", "Experience"})

    # The chatbot is a popover (AppTest has no popover API, so the
    # open/close interaction itself is verified in the browser).
    # What is verifiable here: the greeting renders inside it.
    check("chat popover renders the greeting",
          len(at.exception) == 0
          and len(at.chat_message) == 1
          and any("I am here to help" in x.value
                  for m in at.chat_message for x in m.markdown))
    # NOTE: AppTest in Streamlit 1.64 (Sujal's laptop) has no
    # `at.form` accessor — it only exists in newer versions
    # (1.67 on this VM). The chat form's submit button proves
    # the form rendered, so check for that instead.
    check("chat form present (Send button renders)",
          len(at.exception) == 0
          and any(b.label == "Send" for b in at.button))
    # DOB starts empty (YYYY/MM/DD placeholder, no pre-filled date).
    check("date of birth starts empty",
          len(at.exception) == 0
          and len(at.date_input) == 1
          and at.date_input[0].value is None)

    at.segmented_control[0].set_value("Jobs for Me")
    at.run()
    check("jobs page renders the profile form",
          len(at.exception) == 0
          and any(b.label == "Find jobs for me" for b in at.button)
          and len(at.date_input) == 1
          and at.date_input[0].value is None
          and len(at.multiselect) == 1)

    print("=" * 80)
    print(f"summary: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
