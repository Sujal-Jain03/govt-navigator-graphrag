"""Answer generation with citations, over Phase 4 hybrid retrieval.

Evidence is numbered: graph facts first [1..m], then dense chunks
[m+1..m+k]. The LLM cites evidence numbers; citations are validated
against the evidence list (a citation to a number that does not exist
is reported, not silently trusted).

The LLM client + auto-rotation live in app.extraction.extractor
(battle-tested in Phase 2) and are reused here rather than duplicated
— generation and extraction share one rotation position in a process.
The import is lazy (inside _chat) so prompt-building and the
abstention gate stay testable without any LLM dependency.

Abstention happens twice: a no-LLM gate (no matched entities AND the
best chunk scores below MIN_SCORE) and the system prompt's own
instruction when the evidence lacks the answer.
"""
import re
import time
from dataclasses import dataclass, field

from app.generation.prompts import SYSTEM_PROMPT, build_user_prompt
from app.guardrails.guards import check_output


TEMPERATURE = 0.2
MAX_TOKENS = 2048
# Measured on the real index (no-entity queries): answerable vague
# queries score 0.596+ ("When is the exam?" 0.596, "What is the
# application fee?" 0.653, "What is the age limit?" 0.701);
# clearly off-topic queries score 0.508 or below (weather 0.508,
# cricket 0.474, cake 0.468). 0.55 sits between the two. Near-domain
# queries ("Tell me about the Indian economy", 0.676) pass the gate —
# the LLM's own abstention is the second line of defence for those.
MIN_SCORE = 0.55
MAX_ATTEMPTS = 2
RETRY_WAIT_SECONDS = 10
ABSTAIN_TEXT = "I don't know from the provided documents."

CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class EvidenceItem:
    n: int
    kind: str  # "fact" or "chunk"
    text: str
    source: str


@dataclass
class GenerationResult:
    answer: str
    abstained: bool
    model: str | None = None
    cited: list[int] = field(default_factory=list)
    citations_valid: bool = True
    evidence: list[EvidenceItem] = field(default_factory=list)
    
@dataclass
class GenerationResult:
    answer: str
    abstained: bool
    model: str | None = None
    cited: list[int] = field(default_factory=list)
    citations_valid: bool = True
    evidence: list[EvidenceItem] = field(default_factory=list)
    guard_flags: dict = field(default_factory=dict)



def build_evidence(retrieval):
    """Numbered evidence: graph facts first, then dense chunks."""
    evidence = []
    n = 1
    for f in retrieval.get("facts", []):
        text = f"{f['head']} --{f['relation']}--> {f['tail']}"
        sources = f.get("sources") or []
        evidence.append(EvidenceItem(
            n=n, kind="fact", text=text,
            source=", ".join(sources[:2]) if sources else "graph"))
        n += 1
    for c in retrieval.get("chunks", []):
        evidence.append(EvidenceItem(
            n=n, kind="chunk", text=c["text"],
            source=f"{c['source']}#{c['chunk_index']} (score {c['score']:.3f})"))
        n += 1
    return evidence


def build_context(evidence):
    lines = []
    for item in evidence:
        if item.kind == "fact":
            lines.append(f"[{item.n}] {item.text}")
        else:
            lines.append(f"[{item.n}] [chunk {item.source}] {item.text}")
    return "\n".join(lines)


def build_messages(query, evidence):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(query, build_context(evidence))},
    ]


def _chat(messages):
    """One chat completion with auto-rotation across the Phase 2 models.

    Mirrors extract_chunk's rotation loop: on a daily-limit (TPD) 429
    switch to the next model immediately, with no wait, and stay on it
    for following calls. Other failures retry briefly, then raise.
    Returns (text, model_name).
    """
    from app.extraction import extractor as _llm

    last_error = None
    while _llm._MODEL_INDEX < len(_llm.MODELS):
        model = _llm.MODELS[_llm._MODEL_INDEX]
        client = _llm._get_client_for(_llm._MODEL_INDEX)
        exhausted = False
        for attempt in range(MAX_ATTEMPTS):
            try:
                kwargs = {}
                if model["extra_body"] is not None:
                    kwargs["extra_body"] = model["extra_body"]
                resp = client.chat.completions.create(
                    model=model["name"],
                    messages=messages,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                    **kwargs,
                )
                text = resp.choices[0].message.content or ""
                if text.strip():
                    return text, model["name"]
                last_error = RuntimeError("Empty response from model")
            except Exception as e:  # noqa: BLE001 - retry then surface
                last_error = e
                if _llm._is_tpd_error(e):
                    exhausted = True
                    break
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_WAIT_SECONDS)
        if exhausted:
            nxt = (
                _llm.MODELS[_llm._MODEL_INDEX + 1]["name"]
                if _llm._MODEL_INDEX + 1 < len(_llm.MODELS)
                else "none - all models exhausted"
            )
            print(f"[rotation] {model['name']} daily limit reached, switching to {nxt}")
            _llm._MODEL_INDEX += 1
            continue
        break
    raise RuntimeError(f"Generation failed: {last_error}")


def generate(query, retrieval, llm_fn=None):
    """Generate a cited answer for one query.

    llm_fn is dependency injection for testing: it takes the messages
    list and returns (text, model_name). When None, the real rotation
    client (_chat) is used.
    """
    chunks = retrieval.get("chunks", [])
    matched = retrieval.get("matched_entities", [])
    top_score = chunks[0]["score"] if chunks else 0.0
    # No-LLM abstention gate: nothing matched and no chunk is close.
    if not matched and top_score < MIN_SCORE:
        return GenerationResult(answer=ABSTAIN_TEXT, abstained=True)
    evidence = build_evidence(retrieval)
    messages = build_messages(query, evidence)
    fn = llm_fn or _chat
    text, model = fn(messages)
    text = text.strip()
    abstained = text == ABSTAIN_TEXT
    guard_flags = {}
    if not abstained:
        # Output guard: normalize brackets, repair citations, flag leaks.
        text, guard_flags = check_output(text, len(evidence))
        if guard_flags.get("leaked"):
            # Never return an answer that echoes the system prompt or
            # a secret-looking string.
            return GenerationResult(
                answer=ABSTAIN_TEXT, abstained=True, model=model,
                guard_flags=guard_flags, evidence=evidence)
    cited = sorted({int(m) for m in CITATION_RE.findall(text)})
    valid = all(1 <= c <= len(evidence) for c in cited)
    return GenerationResult(
        answer=text, abstained=abstained, model=model,
        cited=cited, citations_valid=valid, evidence=evidence,
        guard_flags=guard_flags)
