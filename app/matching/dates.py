"""Last-date semantics (Phase 11).

A notification lists many dates; only the submission closing date
decides whether a job is open. Phase 5's engine used the LATEST
graph date, which mislabels exams (RRB NTPC: the 18 Nov
modification-window end is not the 6 Nov application deadline).

This module classifies every LAST_DATE edge by reading the date
with its label in the source notification text:

- application: the context says the date closes submission /
  registration / receipt of applications.
- other: the context says the date is something else (fee payment,
  modification window, print deadline, a derived date).
- unknown: the date has no findable mention, or its context
  carries no signal either way.

Two rules the corpus forced:
- Line first, window second. IMPORTANT DATES tables pack several
  labelled dates close together, so a wide window around one date
  contains other dates' labels (RRB's fee date sits next to its
  closing date). The mention's own line is classified first; the
  +-WINDOW window is only a fallback for bare lines.
- Derived before application. CGL's 2027-06-22 sits in "one year
  from the closing date for receipt of application" — it contains
  the words "closing date" but IS a derived date, not the closing
  date. The derived check runs first.

The matcher treats unknown as NOT an application date: an exam
with no confirmed closing date is reported as "closing date
unknown", never as open.
"""
import json
import re
from datetime import date

from networkx.readwrite import json_graph

from app import config

GRAPH_PATH = config.PROCESSED_DIR / "graph" / "graph.json"
CHUNK_META_PATH = config.PROCESSED_DIR / "retrieval" / "chunk_meta.json"

WINDOW = 220  # chars around a mention, fallback context
LABEL_MAX = 200  # chars kept for the quoted label

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November",
          "December"]

# A date derived from the closing date (e.g. an ex-serviceman rule),
# not the closing date itself. Checked FIRST.
DERIVED_RES = [
    re.compile(r"one year from the closing date", re.I),
    re.compile(r"within (one|1|two|2) years? from", re.I),
    re.compile(r"within the stipulated period of one year", re.I),
]

# The context says this date closes submission / registration.
# NOTE: there is deliberately NO bare "application" alternative:
# RRB's fee date reads "Last Date for Application fee payment
# for the submitted applications" — bare "application" matches
# it and mislabels a fee date as a closing date. Every genuine
# closing line in this corpus carries submission / receipt /
# registration / online-registration / apply-online instead.
APPLICATION_RES = [
    re.compile(r"closing date.{0,60}(submission|receipt|registration)", re.I),
    re.compile(r"last date.{0,60}(submission|receipt|registration)", re.I),
    re.compile(r"dates? for submission", re.I),
    re.compile(r"submission of online applications?", re.I),
    re.compile(r"receipt of online applications?", re.I),
    re.compile(r"online registration", re.I),
    re.compile(r"on-line registration", re.I),
    re.compile(r"apply only online", re.I),
    re.compile(r"apply online only", re.I),
]

# Fee signals. A context that carries a fee signal but NO strong
# application signal is a fee date (RRB's fee date, PO's fee
# window, RBI's print-after-fee line). When BOTH appear (SBI's
# "ONLINE REGISTRATION OF APPLICATION & PAYMENT OF FEES"), the
# application signal wins — the range closes registration too.
FEE_RES = [
    re.compile(r"fee payment", re.I),
    re.compile(r"payment of (application )?fees?", re.I),
    re.compile(r"application fees", re.I),
]

# The context says this date is something else. "Modification" alone
# is NOT here: SBI's registration line contains "Editing/
# Modification of Application" and is an application date.
OTHER_RES = [
    re.compile(r"modification window", re.I),
    re.compile(r"correction window", re.I),
    re.compile(r"print(ing)? (the )?application form", re.I),
    re.compile(r"facility to print", re.I),
    re.compile(r"opening date", re.I),
]


def _normalize(text):
    """Collapse spaces inside numeric dates ("08.07 .2026")."""
    return re.sub(r"(\d)\s*([./-])\s*(\d)", r"\1\2\3", text)


def _date_patterns(d):
    """Regexes matching every written form of one date."""
    out = [re.compile(rf"\b0?{d.day}[./-]0?{d.month}[./-]{d.year}\b")]
    name = MONTHS[d.month - 1]
    for form in (name, name[:3]):
        out.append(re.compile(
            rf"\b0?{d.day}(?:st|nd|rd|th)?\s+{form}\.?\s*,?\s*{d.year}\b", re.I))
        out.append(re.compile(
            rf"\b{form}\.?\s+0?{d.day}(?:st|nd|rd|th)?\s*,?\s*{d.year}\b", re.I))
    return out


def _find_mention(text, d):
    """(start, end) of the first mention of d in text, else None."""
    for pattern in _date_patterns(d):
        m = pattern.search(text)
        if m:
            return m.start(), m.end()
    return None


def _classify_context(ctx):
    """Kind for one context string, or None when it says nothing.

    Order: derived -> fee-without-application -> application ->
    other. The fee check sits before the application check so a
    fee line is never misread as a closing date, but it only
    fires when NO strong application signal is present.
    """
    for pattern in DERIVED_RES:
        if pattern.search(ctx):
            return "other"
    has_fee = any(p.search(ctx) for p in FEE_RES)
    has_app = any(p.search(ctx) for p in APPLICATION_RES)
    if has_fee and not has_app:
        return "other"
    if has_app:
        return "application"
    for pattern in OTHER_RES:
        if pattern.search(ctx):
            return "other"
    return None


def _classify_mention(text, start, end):
    """Classify one mention: its line first, window as fallback.

    Returns (kind, label, from_line). from_line tells the caller
    whether the verdict came from the mention's own line (strong
    evidence) or the surrounding window (weak — a bare table cell
    borrows its neighbour's words, which can contaminate).
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = " ".join(text[line_start:line_end].split())
    # Range-start rule: the FIRST date of a "X to Y" / "X - Y" range
    # on its line is the window's OPENING date, not a closing date
    # ("On-line registration 01.07.2026 to 21.07.2026" — 01.07
    # opens registration, 21.07 closes it). Without this, the
    # aggregation below would promote an opening date to a closing
    # date because its line carries a registration signal.
    after = text[end:line_end]
    if re.match(r"\s*(to|-|–|—)\s*\d{1,2}", after):
        return "other", line[:LABEL_MAX], True
    kind = _classify_context(line)
    if kind is not None:
        return kind, line[:LABEL_MAX], True
    window = " ".join(
        text[max(0, start - WINDOW):min(len(text), end + 60)].split())
    kind = _classify_context(window)
    if kind is not None:
        return kind, window[:LABEL_MAX], False
    return "unknown", line[:LABEL_MAX] if line else window[:LABEL_MAX], bool(line)


def _load_chunks():
    """{(stem, chunk_index): normalized text} from chunk_meta.json."""
    meta = json.loads(CHUNK_META_PATH.read_text(encoding="utf-8"))
    return {(c["source"], c["chunk_index"]): _normalize(c["text"])
            for c in meta}


def classify_last_dates():
    """Every LAST_DATE edge, classified, grouped by exam.

    Returns {exam display name: {"closing_date": date | None,
    "dates": [{"date", "kind", "label", "source"}]}}. closing_date
    is the earliest application-classified date, or None when the
    exam has no confirmed closing date. Dates within an exam are
    sorted; duplicate (exam, date) edges are merged.
    """
    data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    graph = json_graph.node_link_graph(
        data, directed=True, multigraph=True, edges="links")
    chunks = _load_chunks()
    by_stem = {}
    for (stem, idx) in chunks:
        by_stem.setdefault(stem, []).append(idx)
    for stem in by_stem:
        by_stem[stem].sort()

    exams = {}
    seen = set()
    for head, tail, ed in graph.edges(data=True):
        if ed.get("relation") != "LAST_DATE":
            continue
        exam = graph.nodes[head].get("display_name", head)
        raw = graph.nodes[tail].get("display_name", "")
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        if (exam, d) in seen:
            continue
        seen.add((exam, d))
        # Search the edge's own chunks first, then the whole doc.
        refs = [tuple(r) for r in (ed.get("chunk_refs") or [])]
        order = list(refs)
        for stem in sorted({s for s, _ in refs}):
            for idx in by_stem.get(stem, []):
                if (stem, idx) not in order:
                    order.append((stem, idx))
        kind, label, source = "unknown", "", None
        # A date may be mentioned in several chunks with different
        # labels (e.g. SBI's closing date is also its fee deadline).
        # Check EVERY mention: a date IS a closing date if any
        # mention says so — the fee signal on another mention does
        # not un-close it (same principle as the per-context rule
        # in _classify_context). Within one kind, a line verdict
        # beats a window verdict for the label. Rank: line-level
        # application 4, window application 3, line other 2,
        # window other 1, unknown 0.
        # (The old code stopped at the first mention, so the
        # verdict depended on chunk_ref order — which a JSON
        # re-serialization can scramble.)
        best_rank = 0
        for stem, idx in order:
            text = chunks.get((stem, idx))
            if text is None:
                continue
            found = _find_mention(text, d)
            if found is None:
                continue
            k, lab, from_line = _classify_mention(text, *found)
            if source is None:
                # First mention found: the fallback label.
                label, source = lab, f"{stem}:{idx}"
            if k == "unknown":
                rank = 0
            elif k == "application":
                rank = 4 if from_line else 3
            else:  # other
                rank = 2 if from_line else 1
            if rank > best_rank:
                best_rank = rank
                kind, label = k, lab
                source = f"{stem}:{idx}"
            if best_rank == 4:
                break  # line-level application can't be beaten
        entry = exams.setdefault(exam, {"closing_date": None, "dates": []})
        entry["dates"].append({
            "date": d, "kind": kind, "label": label, "source": source,
        })
    for exam, entry in exams.items():
        entry["dates"].sort(key=lambda e: e["date"])
        app_dates = [e["date"] for e in entry["dates"]
                     if e["kind"] == "application"]
        entry["closing_date"] = min(app_dates) if app_dates else None
    return exams
