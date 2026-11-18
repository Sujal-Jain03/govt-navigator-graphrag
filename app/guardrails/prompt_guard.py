"""Llama Prompt Guard 2 (via Groq) — model-based injection classifier.

Groq-only: no other provider in the rotation serves this model, so
there is no rotation here. Any failure (missing key, rate limit,
non-numeric output) fails OPEN — the regex layer still protects the
query, and a guard outage must not kill the app.
"""
from app.guardrails.patterns import GUARD_MODEL, GUARD_THRESHOLD


def _get_groq_client():
    from app.extraction import extractor as _llm
    # MODELS[0] is always the Groq entry; the client is the Groq endpoint.
    return _llm._get_client_for(0)


def check(query, client=None):
    """Ask Prompt Guard 2. Returns (blocked, score_or_None, error_or_None)."""
    try:
        active = client or _get_groq_client()
        completion = active.chat.completions.create(
            model=GUARD_MODEL,
            messages=[{"role": "user", "content": query}],
            temperature=0,
            max_tokens=16,
        )
        raw = (completion.choices[0].message.content or "").strip()
        try:
            score = float(raw)
        except ValueError:
            print(f"[prompt-guard] non-numeric output: {raw!r}")
            return False, None, None
        return score >= GUARD_THRESHOLD, score, None
    except Exception as e:  # noqa: BLE001 - fail open, always
        print(f"[prompt-guard] call failed, rules-only mode: {e}")
        return False, None, str(e)
