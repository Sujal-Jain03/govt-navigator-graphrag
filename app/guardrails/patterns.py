"""Guardrail patterns and limits.

Injection patterns are reused verbatim from the Enterprise RAG project
(verified there in Nov 2026). PII patterns are new here: users must not
paste Aadhaar/PAN numbers into queries — the LLM never needs them.
"""
import re

MAX_QUERY_CHARS = 2000
GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"
GUARD_THRESHOLD = 0.5

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) instructions",
    r"disregard (all )?(the )?(previous |prior |above |your )?(instructions|prompt|rules|context)",
    r"you are now",
    r"pretend (you are|to be)",
    r"act as (if )?you (are|were)",
    r"(reveal|show|print|repeat|quote|tell) (me )?(your )?(system prompt|instructions|prompt)",
    r"what (are|is) your (system )?(instructions|prompt)",
    r"(don't|do not|dont) follow (your|the|these) (instructions|rules)",
    r"override (your )?(instructions|rules|system)",
    r"jailbreak",
    r"developer mode",
    r"sudo mode",
]
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)

# Aadhaar: 12 digits, optionally grouped in 4s. PAN: 5 letters + 4 digits + 1 letter.
PII_PATTERNS = {
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}

SECRET_RES = [
    re.compile(r"gsk_[A-Za-z0-9]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"nvapi-[A-Za-z0-9_\-]{10,}"),
]

SYSTEM_PROMPT_MARKER = "using ONLY the numbered evidence provided"

CITATION_RE = re.compile(r"\[(\d+)\]")
