import json
import re
import time

from openai import OpenAI

from app.config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
    ZHIPU_MODEL,
)
from app.extraction.prompts import SYSTEM_PROMPT, build_user_prompt

MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = [10, 30, 60]
# Temperature rises on retry: breaks deterministic temp-0 repeats
# (e.g. the model returning an empty response twice in a row).
RETRY_TEMPERATURES = [0, 0.3, 0.7]

# Auto-rotation order. First entry follows GROQ_MODEL from .env (currently
# openai/gpt-oss-20b); then Groq qwen; then z.ai GLM-4.7-Flash;
# then NVIDIA NIM gpt-oss-20b (slow, ~100s/chunk measured 17 Nov,
# last resort only). Deduped by name, so if GROQ_MODEL is already
# qwen the list is just [qwen, z.ai, NVIDIA].
# extra_body (thinking off) is z.ai-only: Groq 400s on it, so it is only
# attached to the z.ai entry.
MODELS = []
for _name, _key, _base, _body in [
    (GROQ_MODEL, GROQ_API_KEY, GROQ_BASE_URL, None),
    ("qwen/qwen3.8-27b", GROQ_API_KEY, GROQ_BASE_URL, None),
    (ZHIPU_MODEL, ZHIPU_API_KEY, ZHIPU_BASE_URL, {"thinking": {"type": "disabled"}}),
    (NVIDIA_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL, None),
]:
    if _name not in [m["name"] for m in MODELS]:
        MODELS.append(
            {"name": _name, "key": _key, "base_url": _base, "extra_body": _body}
        )

_MODEL_INDEX = 0  # persists across chunks within one process run
_CLIENTS = {}


def _get_client_for(index: int) -> OpenAI:
    if index not in _CLIENTS:
        model = MODELS[index]
        _CLIENTS[index] = OpenAI(api_key=model["key"], base_url=model["base_url"])
    return _CLIENTS[index]


def get_client() -> OpenAI:
    return _get_client_for(_MODEL_INDEX)


def _is_tpd_error(exc: Exception) -> bool:
    """True only for a daily token-budget exhaustion (Groq TPD 429).

    Per-minute 429s (TPM) and z.ai overload (1305) are transient and must
    keep the normal retry-with-wait path, not rotate the model.
    """
    status = getattr(exc, "status_code", None)
    if status is not None and status != 429:
        return False
    msg = str(exc).lower()
    return "tokens per day" in msg or "tpd" in msg


def _parse_json(raw: str) -> dict:
    """Parse model output as JSON, with repairs for common model mistakes."""
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    braced = re.search(r"\{.*\}", raw, re.DOTALL)
    if braced:
        candidates.append(braced.group(0))
    for text in candidates:
        for attempt in (text, re.sub(r",(\s*[}\]])", r"\1", text)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON in model output")


def extract_chunk(client: OpenAI, chunk_text: str) -> dict:
    """Extract entities + relations for one chunk.
    Returns {"entities": [...], "relations": [...]}.
    Raises on API failure after retries or unparseable output.

    `client` is kept for compatibility with the runner; the active client
    actually comes from the rotation state below. On a daily-limit (TPD)
    429 the extractor switches to the next model immediately, with no
    wait, and stays on it for all following chunks.
    """
    global _MODEL_INDEX
    last_error = None
    raw = None
    finish_reason = None
    while _MODEL_INDEX < len(MODELS):
        model = MODELS[_MODEL_INDEX]
        active_client = _get_client_for(_MODEL_INDEX)
        exhausted = False
        for attempt in range(MAX_ATTEMPTS):
            try:
                kwargs = {}
                if model["extra_body"] is not None:
                    kwargs["extra_body"] = model["extra_body"]
                resp = active_client.chat.completions.create(
                    model=model["name"],
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(chunk_text)},
                    ],
                    temperature=RETRY_TEMPERATURES[attempt],
                    max_tokens=16384,
                    **kwargs,
                )
                candidate = resp.choices[0].message.content or ""
                finish_reason = resp.choices[0].finish_reason
                if candidate.strip():
                    raw = candidate
                    break
                last_error = RuntimeError("Empty response from model")
            except Exception as e:  # noqa: BLE001 - retry then surface
                last_error = e
                if _is_tpd_error(e):
                    exhausted = True
                    break
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_WAIT_SECONDS[attempt])
        if raw is not None:
            break
        if exhausted:
            next_name = (
                MODELS[_MODEL_INDEX + 1]["name"]
                if _MODEL_INDEX + 1 < len(MODELS)
                else "none - all models exhausted"
            )
            print(f"[rotation] {model['name']} daily limit reached, switching to {next_name}")
            _MODEL_INDEX += 1
            continue
        break  # failed on this model without exhaustion -> raise below
    if raw is None:
        raise RuntimeError(f"Extraction failed after {MAX_ATTEMPTS} attempts: {last_error}")
    try:
        data = _parse_json(raw)
    except ValueError as e:
        raise ValueError(
            f"{e}. finish_reason={finish_reason}. "
            f"Raw output (first 800 chars): {raw[:800]!r}"
        )
    return {
        "entities": data.get("entities", []),
        "relations": data.get("relations", []),
    }
