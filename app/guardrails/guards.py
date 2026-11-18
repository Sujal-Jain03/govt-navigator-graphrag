"""Input + output guardrails.

Input (check_input): empty / too long / PII / regex injection /
Prompt Guard 2 (optional, fail-open).
Output (check_output): bracket normalization, citation repair,
leakage + PII flags.

Note: check_output flags a missing-citations answer. The abstention
text ("I don't know...") has no citations by design — callers must
not run it through check_output (generate() skips it).
"""
from app.guardrails.patterns import (
    CITATION_RE,
    INJECTION_RE,
    MAX_QUERY_CHARS,
    PII_PATTERNS,
    SECRET_RES,
    SYSTEM_PROMPT_MARKER,
)

# Full-width brackets (U+3010/U+3011) and digits (U+FF10-U+FF19) ->
# ASCII. gpt-oss-20b emitted bracketed citations in the Phase 6 test;
# the validator must see plain [17]. Built with chr() (not literals)
# so the characters survive every editor and transport intact.
_FULLWIDTH_MAP = {chr(0x3010): "[", chr(0x3011): "]"}
_FULLWIDTH_MAP.update({chr(0xFF10 + i): str(i) for i in range(10)})
_FULLWIDTH = str.maketrans(_FULLWIDTH_MAP)


def normalize_citations(text):
    """Full-width brackets/digits -> ASCII. Returns (text, changed)."""
    cleaned = text.translate(_FULLWIDTH)
    return cleaned, cleaned != text


def _regex_hit(query):
    m = INJECTION_RE.search(query)
    return m.group(0) if m else None


def _pii_hit(query):
    for name, rx in PII_PATTERNS.items():
        if rx.search(query):
            return name
    return None


def check_input(query, use_prompt_guard=True):
    """Check a user query. Returns (allowed, reason, details)."""
    details = {"length_ok": True, "regex_hit": None, "pii_hit": None,
               "guard_score": None, "guard_error": None}
    if not query or not query.strip():
        return False, "empty query", details
    if len(query) > MAX_QUERY_CHARS:
        details["length_ok"] = False
        return False, f"query too long ({len(query)} > {MAX_QUERY_CHARS} chars)", details
    pii = _pii_hit(query)
    details["pii_hit"] = pii
    if pii:
        return False, f"do not share ID numbers ({pii} detected) — the navigator never needs them", details
    hit = _regex_hit(query)
    details["regex_hit"] = hit
    if hit:
        return False, f"injection pattern matched: {hit!r}", details
    if use_prompt_guard:
        from app.guardrails.prompt_guard import check as guard_check
        blocked, score, error = guard_check(query)
        details["guard_score"] = score
        details["guard_error"] = error
        if blocked:
            return False, f"prompt guard flagged (score {score})", details
    return True, None, details


def check_output(answer, num_evidence):
    """Validate an LLM answer. Returns (cleaned_answer, flags)."""
    flags = {"normalized": False, "invalid_citations": [],
             "citations_missing": False, "leaked": False, "redacted_pii": False}
    if not answer:
        return answer, flags
    # Leakage: system-prompt echo or a secret-looking string.
    if SYSTEM_PROMPT_MARKER in answer or any(rx.search(answer) for rx in SECRET_RES):
        flags["leaked"] = True
        return answer, flags
    # PII: redact Aadhaar/PAN-shaped strings if the model emitted any.
    cleaned = answer
    for rx in PII_PATTERNS.values():
        if rx.search(cleaned):
            cleaned = rx.sub("[REDACTED]", cleaned)
            flags["redacted_pii"] = True
    # Bracket normalization, then citation repair.
    cleaned, changed = normalize_citations(cleaned)
    flags["normalized"] = changed

    def _fix(m):
        n = int(m.group(1))
        if 1 <= n <= num_evidence:
            return m.group(0)
        flags["invalid_citations"].append(n)
        return ""

    cleaned = CITATION_RE.sub(_fix, cleaned)
    if not CITATION_RE.search(cleaned):
        flags["citations_missing"] = True
    return cleaned, flags
