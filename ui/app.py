"""Govt Navigator UI (Phase 10; cloud mode added Phase 13).

Two backends, one UI (see ui/backend.py):
  HTTP mode (default — local dev and Docker): run the API first
  (python run_api.py), then `streamlit run ui/app.py`. The UI
  talks to the API over HTTP (API_URL env, default
  http://localhost:8000) and holds no model and no keys itself.
  Cloud mode (NAVIGATOR_MODE=cloud — Streamlit Community Cloud):
  no separate API server; the UI calls the service layer
  directly, in-process. The model + graph load in the Streamlit
  process; LLM keys come from the environment.

Pages: Check Eligibility / Browse Exams / Live Updates / Jobs for
Me, plus a floating chatbot (a popover pinned to the bottom right
of the viewport, independent of page scroll) that answers
questions from the notifications. All state lives in
st.session_state so moving between pages keeps your verdicts,
job matches and chat history.
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

# Cloud mode imports the service layer in-process, so the repo
# root (parent of ui/) must be importable. Harmless in HTTP mode.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend  # noqa: E402  (needs the sys.path line above)

# ---------------------------------------------------------------- CSS
# A calm, official-services look: deep navy + saffron accent, cards
# with soft borders, colour-coded verdict/status badges.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1 { color: #0F2A43; font-weight: 700; }
h2, h3 { color: #0F2A43; }
.stButton > button {
    background-color: #E8720C; color: white; border: none;
    border-radius: 8px; padding: 0.45rem 1.2rem; font-weight: 600;
}
.stButton > button:hover { background-color: #C95F07; color: white; }
.card {
    border: 1px solid #E2E8F0; border-radius: 12px; padding: 1rem 1.2rem;
    margin-bottom: 0.9rem; background: white;
    box-shadow: 0 1px 3px rgba(15,42,67,0.06);
}
.answer-card {
    border-left: 4px solid #E8720C; background: #FFFBF5;
    border-radius: 8px; padding: 1rem 1.2rem; margin: 0.8rem 0;
}
.badge {
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
    font-size: 0.78rem; font-weight: 600; color: white;
}
.badge-green { background: #15803D; }
.badge-red { background: #B91C1C; }
.badge-amber { background: #B45309; }
.badge-grey { background: #64748B; }
.badge-navy { background: #0F2A43; }
.stat-box {
    background: #F1F5F9; border-radius: 10px; padding: 0.7rem;
    text-align: center;
}
.stat-box .num { font-size: 1.4rem; font-weight: 700; color: #0F2A43; }
.stat-box .lbl { font-size: 0.78rem; color: #64748B; }
.muted { color: #64748B; font-size: 0.88rem; }
.evidence { font-size: 0.88rem; }
.evidence .kind-fact { color: #0F2A43; font-weight: 600; }
.evidence .kind-chunk { color: #B45309; font-weight: 600; }
/* Floating chatbot: the popover WRAPPER is pinned to the bottom
   right of the viewport, so it stays put while the page scrolls.
   Pin the wrapper (not just the button): the panel anchors to
   the wrapper's in-flow position, so pinning only the button
   left the panel opening at the bottom-left of the page. The
   width override keeps the wrapper a small pill around the
   button instead of a full-width bar. The panel (a portal
   overlay) then opens directly above the button. (Testids
   verified in Streamlit's own frontend bundle.) */
div[data-testid="stPopover"] {
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 1000;
    width: auto !important;
}
div[data-testid="stPopoverBody"] {
    width: 380px;
    max-height: 70vh;
    overflow-y: auto;
}
</style>
"""

VERDICT_BADGE = {
    "ELIGIBLE": ("badge-green", "Eligible"),
    "NOT_ELIGIBLE": ("badge-red", "Not eligible"),
    "CLOSED": ("badge-grey", "Closed"),
    "UNKNOWN": ("badge-grey", "Unknown"),
    "RELAXATION_MAYBE": ("badge-amber", "Check relaxation"),
}
STATUS_BADGE = {
    "live": ("badge-green", "Live"),
    "expired": ("badge-red", "Expired"),
    "unknown": ("badge-grey", "Unknown"),
}

QUALIFICATIONS = [
    "bachelor's degree", "master's degree", "phd",
    "class xii", "class x", "diploma", "certificate",
]
CATEGORIES = ["General", "OBC", "SC", "ST", "EWS", "PwBD"]


# ------------------------------------------------------------ helpers
def badge(kind_map, key):
    cls, label = kind_map.get(key, ("badge-grey", key))
    return f'<span class="badge {cls}">{label}</span>'


def pretty_date(iso):
    if not iso:
        return "—"
    try:
        return date.fromisoformat(iso).strftime("%d %b %Y")
    except ValueError:
        return iso


# -------------------------------------------------------- chat panel
def render_chat_panel():
    """Chatbot content, rendered inside the floating popover.
    History lives in session state, so it survives page switches.
    The input is a form with clear_on_submit so the box empties
    after each question. (Streamlit keeps an open popover open
    across reruns triggered from inside it — documented
    behaviour — so asking a question does not close the panel.)"""
    st.subheader("Ask Govt Navigator")
    st.markdown('<p class="muted">Answers come only from the official '
                "notifications in the corpus, with citations. If the "
                "evidence doesn't cover it, the app says so instead of "
                "guessing.</p>", unsafe_allow_html=True)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            {"role": "assistant", "content": "I am here to help"}]
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    with st.form("chat_form", clear_on_submit=True):
        q = st.text_input("Your question",
                          placeholder="e.g. What is the age limit for SBI PO?")
        send = st.form_submit_button("Send", type="primary")
    if send and q.strip():
        st.session_state.chat_history.append({"role": "user", "content": q})
        with st.spinner("Searching the graph + documents…"):
            try:
                res = backend.ask(q)
                if res["abstained"]:
                    answer = "I don't know from the provided documents."
                else:
                    answer = res["answer"]
                    if not res["citations_valid"]:
                        answer += "\n\n⚠️ Some citations were out of range."
                st.session_state.chat_history.append(
                    {"role": "assistant", "content": answer})
                st.session_state.chat_last = res
            except backend.BackendError as exc:
                st.session_state.chat_history.append(
                    {"role": "assistant",
                     "content": f"Couldn't answer: {exc}"})
                st.session_state.chat_last = None
        st.rerun()
    last = st.session_state.get("chat_last")
    if last and last.get("cited"):
        cited = {e["n"]: e for e in last["evidence"] if e["n"] in last["cited"]}
        with st.expander(f"Sources ({len(cited)} cited)"):
            for n in sorted(cited):
                e = cited[n]
                kind_cls = "kind-fact" if e["kind"] == "fact" else "kind-chunk"
                st.markdown(
                    f'<p class="evidence">[{n}] '
                    f'<span class="{kind_cls}">{e["kind"]}</span> — '
                    f'{e["text"]} '
                    f'<span class="muted">({e["source"]})</span></p>',
                    unsafe_allow_html=True)


# -------------------------------------------------------------- pages
def render_page(page):
    if page == "Check Eligibility":
        st.header("Check Eligibility")
        st.markdown('<p class="muted">Enter your profile once; the engine '
                    "checks every exam's age, qualification and last date. "
                    "Where a notification lists several dates, all of them "
                    "are shown — always confirm the application deadline in "
                    "the notification itself.</p>", unsafe_allow_html=True)

        # --- Personal Information ---
        st.subheader("Personal Information")
        name = st.text_input("Name (optional)", key="prof_name")
        # No pre-filled date: empty shows the YYYY/MM/DD placeholder.
        dob = st.date_input("Date of birth", value=None,
                            format="YYYY/MM/DD",
                            min_value=date(1950, 1, 1),
                            max_value=date.today(), key="prof_dob")
        category = st.selectbox("Category", CATEGORIES, key="prof_category")

        # --- Education: one or more entries, at least one required ---
        # Entries are tracked by stable ids (not positions) so removing
        # one entry never shifts another entry's selected value.
        st.subheader("Education")
        st.markdown('<p class="muted">Add every qualification you hold — '
                    "matching uses your highest one.</p>", unsafe_allow_html=True)
        if "edu_ids" not in st.session_state:
            st.session_state.edu_ids = [0]
            st.session_state.edu_next_id = 1
        for pos, eid in enumerate(list(st.session_state.edu_ids)):
            col_sel, col_del = st.columns([0.85, 0.15])
            with col_sel:
                st.selectbox(f"Education {pos + 1}", QUALIFICATIONS,
                             index=QUALIFICATIONS.index("bachelor's degree"),
                             key=f"edu_{eid}")
            with col_del:
                if len(st.session_state.edu_ids) > 1:
                    if st.button("Remove", key=f"edu_del_{eid}"):
                        st.session_state.edu_ids.remove(eid)
                        st.rerun()
        if st.button("Add education"):
            st.session_state.edu_ids.append(st.session_state.edu_next_id)
            st.session_state.edu_next_id += 1
            st.rerun()

        # --- Experience ---
        st.subheader("Experience")
        exp_years = st.number_input("Total work experience (years)",
                                    min_value=0.0, max_value=60.0, value=0.0,
                                    step=0.5, key="prof_exp")
        st.markdown('<p class="muted">Experience is saved with your profile. '
                    "The notifications in the corpus are entry-level, so it "
                    "doesn't change verdicts yet.</p>", unsafe_allow_html=True)

        if st.button("Check all exams", type="primary"):
            quals = [st.session_state.get(f"edu_{eid}", "bachelor's degree")
                     for eid in st.session_state.edu_ids]
            if dob is None:
                st.session_state.elig_result = None
                st.session_state.elig_error = ("Please enter your date of "
                                               "birth.")
            elif not quals:
                st.session_state.elig_result = None
                st.session_state.elig_error = "Add at least one education."
            else:
                with st.spinner("Checking 9 exams…"):
                    try:
                        st.session_state.elig_result = backend.check_eligibility({
                            "name": name,
                            "date_of_birth": dob.isoformat(),
                            "qualifications": quals,
                            "experience_years": exp_years,
                            "category": category,
                        })
                        st.session_state.elig_error = None
                    except backend.BackendError as exc:
                        st.session_state.elig_result = None
                        st.session_state.elig_error = str(exc)

        if st.session_state.get("elig_error"):
            st.error(st.session_state["elig_error"])
        elig = st.session_state.get("elig_result")
        if elig:
            st.subheader(f"Results for {elig['name'] or 'you'} "
                         f"(age {elig['age']})")
            st.markdown(f'<p class="muted">Qualifications: '
                        f'{", ".join(elig["qualifications"])} (matching uses '
                        f'highest: {elig["qualification"]}) · Category: '
                        f'{elig["category"]} · Experience: '
                        f'{elig["experience_years"]} yrs</p>',
                        unsafe_allow_html=True)
            cols = st.columns(2)
            for i, r in enumerate(elig["results"]):
                with cols[i % 2]:
                    dates = ", ".join(pretty_date(d) for d in r["last_dates"]) \
                        or "no date in graph"
                    reasons = "<br>".join("• " + x for x in r["reasons"])
                    st.markdown(
                        f'<div class="card"><b>{r["exam"]}</b> '
                        f'{badge(VERDICT_BADGE, r["verdict"])}<br>'
                        f'<span class="muted">last date(s): {dates} · '
                        f'min qualification: {r["min_qual"] or "—"}</span><br>'
                        f'{reasons}</div>',
                        unsafe_allow_html=True)

    elif page == "Browse Exams":
        st.header("Browse Exams")
        with st.spinner("Loading exams…"):
            exams = backend.list_exams()
        cols = st.columns(2)
        for i, e in enumerate(exams):
            with cols[i % 2]:
                dates = ", ".join(pretty_date(d) for d in e["last_dates"]) or "—"
                days = (f"{e['days_left']} days left" if e["days_left"] is not None
                        and e["days_left"] >= 0 else "")
                ages = ", ".join(e["age_ranges"]) or "—"
                st.markdown(
                    f'<div class="card"><b>{e["exam"]}</b> '
                    f'{badge(STATUS_BADGE, e["status"])}<br>'
                    f'<span class="muted">{e["org"] or ""} · {len(e["posts"])} '
                    f'post(s) · {e["documents_count"]} document types</span><br>'
                    f'Last date(s): {dates} {days}<br>'
                    f'Age: {ages} · Min qualification: '
                    f'{e["min_qual"] or "—"}</div>',
                    unsafe_allow_html=True)
                with st.expander(f"About {e['exam']}"):
                    if e["summary"]:
                        st.markdown(e["summary"])
                    if e["fees"]:
                        st.markdown("**Fees:**")
                        for f in e["fees"]:
                            st.markdown(f"- {f}")
                    if e["posts"]:
                        st.markdown("**Posts:** " + ", ".join(e["posts"]))

    elif page == "Jobs for Me":
        st.header("Jobs for Me")
        st.markdown('<p class="muted">Enter your profile once; the matcher '
                    "lists the govt jobs you can enroll in <b>right "
                    "now</b> — eligible <b>and</b> still open, soonest "
                    "deadline first. Openness uses each notification's "
                    "submission closing date, never a fee or modification "
                    "date.</p>", unsafe_allow_html=True)
        job_dob = st.date_input("Date of birth", value=None,
                                format="YYYY/MM/DD",
                                min_value=date(1950, 1, 1),
                                max_value=date.today(), key="job_dob")
        job_category = st.selectbox("Category", CATEGORIES, key="job_category")
        job_quals = st.multiselect("Your qualifications", QUALIFICATIONS,
                                   default=["bachelor's degree"],
                                   key="job_quals")
        if st.button("Find jobs for me", type="primary"):
            if job_dob is None:
                st.session_state.job_results = None
                st.session_state.job_error = ("Please enter your date of "
                                              "birth.")
            elif not job_quals:
                st.session_state.job_results = None
                st.session_state.job_error = ("Select at least one "
                                              "qualification.")
            else:
                with st.spinner("Matching jobs…"):
                    try:
                        st.session_state.job_results = backend.list_jobs({
                            "date_of_birth": job_dob.isoformat(),
                            "qualifications": job_quals,
                            "category": job_category,
                        })
                        st.session_state.job_error = None
                    except backend.BackendError as exc:
                        st.session_state.job_results = None
                        st.session_state.job_error = str(exc)

        if st.session_state.get("job_error"):
            st.error(st.session_state["job_error"])
        jobs = st.session_state.get("job_results")
        if jobs:
            counts = jobs["counts"]
            st.markdown(f'<p class="muted">As of {jobs["as_of"]} · '
                        f'{jobs["checked_exams"]} exams checked · '
                        f'{counts["open"]} open · '
                        f'{counts["closing_unknown"]} closing date unknown · '
                        f'{counts["needs_check"]} need a check</p>',
                        unsafe_allow_html=True)
            if jobs["open_jobs"]:
                st.subheader("Open for you")
                cols = st.columns(2)
                for i, j in enumerate(jobs["open_jobs"]):
                    with cols[i % 2]:
                        days = j["days_left"]
                        if days is not None and days <= 7:
                            dl = f'<span class="badge badge-red">{days} days left</span>'
                        elif days is not None and days <= 30:
                            dl = f'<span class="badge badge-amber">{days} days left</span>'
                        elif days is not None:
                            dl = f'<span class="badge badge-green">{days} days left</span>'
                        else:
                            dl = ""
                        ages = ", ".join(j["age_ranges"]) or "—"
                        label = (f'<br><span class="muted">“{j["closing_label"]}”</span>'
                                 if j.get("closing_label") else "")
                        st.markdown(
                            f'<div class="card"><b>{j["exam"]}</b> {dl}<br>'
                            f'<span class="muted">{j["org"] or ""} · '
                            f'{len(j["posts"])} post(s) · '
                            f'{j["documents_count"]} document types</span><br>'
                            f'Closing date: {pretty_date(j["closing_date"])}{label}<br>'
                            f'Age: {ages} · Min qualification: '
                            f'{j["min_qual"] or "—"}</div>',
                            unsafe_allow_html=True)
                        with st.expander(f"Details — {j['exam']}"):
                            if j["summary"]:
                                st.markdown(j["summary"])
                            if j["fees"]:
                                st.markdown("**Fees:**")
                                for f in j["fees"]:
                                    st.markdown(f"- {f}")
                            if j["posts"]:
                                st.markdown("**Posts:** " + ", ".join(j["posts"]))
                            if j["all_dates"]:
                                st.markdown("**All dates in the notification:**")
                                for d in j["all_dates"]:
                                    st.markdown(f"- {pretty_date(d['date'])} "
                                                f"({d['kind']})")
            else:
                st.info("No open jobs for your profile right now — every "
                        "notification in the corpus has closed. New "
                        "notifications from the daily update appear here "
                        "once their closing dates are known.")
            if jobs["closing_unknown"]:
                st.subheader("Closing date unknown — check the notification")
                for j in jobs["closing_unknown"]:
                    st.markdown(f"- **{j['exam']}** ({j['org'] or ''}): you "
                                f"look eligible, but no submission closing "
                                f"date could be confirmed in its notification.")
            if jobs["needs_check"]:
                st.subheader("Needs a check — relaxation only")
                for j in jobs["needs_check"]:
                    st.markdown(f"- **{j['exam']}** (closes "
                                f"{pretty_date(j['closing_date'])}): "
                                f"{j.get('relaxation_note', '')}")

    else:  # Live Updates
        st.header("Live Updates")
        with st.spinner("Loading update status…"):
            data = backend.latest_updates()
        st.subheader("Deadlines")
        rows = [{
            "Exam": e["exam"],
            "Last date": pretty_date(e.get("last_date")),
            "Status": e["status"],
            # All strings: mixing ints with "—" made pyarrow fail
            # the Arrow conversion (noisy auto-fix warning).
            "Days left": (str(e["days_left"])
                          if e["days_left"] is not None else "—"),
        } for e in data["expiry"]]
        st.dataframe(rows, width="stretch", hide_index=True)
        st.subheader("Latest daily check")
        report = data.get("report")
        if not report:
            st.info("No daily-update report found yet. Run "
                    "`python run_daily_update.py` once to create one.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Downloaded", len(report.get("downloaded", [])))
            c2.metric("Skipped", len(report.get("skipped", [])))
            c3.metric("Errors", len(report.get("errors", [])))
            st.markdown(f'<p class="muted">Report date: '
                        f'{report.get("date")} · dry run: '
                        f'{report.get("dry_run")}</p>', unsafe_allow_html=True)
            if report.get("downloaded"):
                with st.expander("Downloaded files"):
                    for d in report["downloaded"]:
                        st.markdown(f"- `{d.get('file')}` — {d.get('source')} "
                                    f"({d.get('kind')})")
            if report.get("errors"):
                with st.expander("Errors"):
                    for err in report["errors"]:
                        st.markdown(f"- {err}")


# ------------------------------------------------------------- setup
st.set_page_config(page_title="Govt Navigator", page_icon=":classical_building:",
                   layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

try:
    health = backend.health()
except backend.BackendError as exc:
    st.error(str(exc))
    st.stop()

# --------------------------------------------------------- top nav bar
st.markdown("## :classical_building: Govt Navigator")
st.markdown('<p class="muted">Eligibility + answers from <b>official '
            "notifications only</b> — never search snippets.</p>",
            unsafe_allow_html=True)
page = st.segmented_control(
    "Go to",
    ["Check Eligibility", "Browse Exams", "Live Updates", "Jobs for Me"],
    default="Check Eligibility",
    label_visibility="collapsed",
)
c1, c2, c3 = st.columns(3)
c1.markdown(f'<div class="stat-box"><div class="num">{health["exams"]}'
            f'</div><div class="lbl">exams</div></div>',
            unsafe_allow_html=True)
c2.markdown(f'<div class="stat-box"><div class="num">{health["chunks"]}'
            f'</div><div class="lbl">chunks</div></div>',
            unsafe_allow_html=True)
c3.markdown(f'<div class="stat-box"><div class="num">{health["nodes"]}'
            f'</div><div class="lbl">graph nodes</div></div>',
            unsafe_allow_html=True)
st.markdown("---")

# ------------------------------------------------------------ layout
render_page(page)

# --------------------------------------------------- floating chatbot
# The popover trigger is pinned bottom-right by CSS, independent
# of page scroll; the panel opens above it. Clicking the button
# toggles it; clicking outside closes it.
with st.popover("💬 Ask", key="chat_popover"):
    render_chat_panel()

# -------------------------------------------------------------- footer
st.markdown("---")
st.markdown('<p class="muted">Sources: IBPS, SBI, RBI, SSC, RRB, UPSC '
            "notifications. Daily update checks official sites for new "
            "notices.</p>", unsafe_allow_html=True)
