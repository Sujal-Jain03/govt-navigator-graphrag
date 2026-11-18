# Govt Navigator — GraphRAG over official Indian govt notifications

**Live demo:** Streamlit Community Cloud link coming after the first deploy.

A from-scratch GraphRAG app that answers a simple question for a job
seeker: *which government jobs can I enroll in right now, and what do
I need to know about them?* Everything it says comes from official
notifications (IBPS, SBI, RBI, SSC, RRB, UPSC) — never from search
snippets or the model's training data.

## What it does

- **Jobs for Me** — enter your date of birth, education and category;
  it lists the govt jobs you are eligible for **and** still open,
  soonest deadline first. Openness uses each notification's
  *submission closing date* — fee dates and modification windows
  never count as deadlines.
- **Check Eligibility** — per-exam verdicts (eligible / not eligible /
  closed / relaxation may apply) over all 9 exams in the corpus.
- **Ask** — a chatbot that answers from the notifications with `[n]`
  citations to graph facts and document chunks. When the evidence
  doesn't cover a question it says so instead of guessing.
- **Live Updates** — a daily pipeline (GitHub Actions, 08:00 IST)
  discovers new notifications on official sites, ingests them, and
  rebuilds the graph, so the corpus stays fresh without a laptop.

## How it works

```
official PDFs -> chunks (1,184) -> LLM entity/relation extraction
  -> canonicalized graph (1,434 nodes / 2,064 edges)
  -> hybrid retrieval (BGE-base dense + 2-hop graph facts)
  -> cited generation (guardrailed, abstains when unsure)
```

- **Extraction** runs on free-tier LLMs with automatic rotation
  (Groq gpt-oss-20b / qwen3-27b -> z.ai GLM-4.7-Flash -> NVIDIA NIM)
  when a daily token budget runs out.
- **The graph** is built with document-level attachment: qualifications
  and age limits attach to a notification's posts even when the
  sections that state them sit pages apart.
- **Last dates are classified, not assumed**: every date in the graph
  is read with its label (closing / fee / modification / derived), so
  the matcher never shows a closed job as live.

## Verified numbers (measured, not claimed)

| Check | Result |
|---|---|
| Q&A eval (16 labelled questions, 4 runs) | 15/16, 13/16, 13/16, 15/16 |
| Abstention probes (off-topic / out-of-corpus) | 4/4 on every run |
| Gold-fact recall (retrieval) | 15/15 |
| Citation validity | 15/15, 15/15, 14/15, 14/14 |
| Guardrail test suite | 49/49 |
| Pipeline self-test | 45/45 |

Corpus: 9 notifications, ~610 pages, 1,184 chunks, 6,658 entities,
2,375 relations.

## Project structure

```
app/
  ingestion/    PDF parsing + chunking (incremental, hash-based)
  extraction/   LLM entity/relation extraction with model rotation
  graph/        canonicalization, pruning, graph build
  retrieval/    BGE embeddings + 2-hop graph search
  eligibility/  age / qualification / last-date engine
  generation/   cited answers + abstention gate
  guardrails/   injection + PII guards, citation repair
  matching/     last-date classifier + live job matcher
  updates/      Tavily discovery + official-PDF download + expiry
  pipeline/     orchestrator: update -> ingest -> extract -> graph -> index
  api/          FastAPI service layer (also used in-process by the UI)
ui/             Streamlit app (HTTP mode locally, in-process on the cloud)
.github/workflows/daily-pipeline.yml   daily run on GitHub Actions
```

## Run it locally

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
# .env: GROQ_API_KEY, ZHIPU_API_KEY, NVIDIA_API_KEY, TAVILY_API_KEY
python run_api.py            # terminal 1 — FastAPI on :8000
streamlit run ui/app.py      # terminal 2 — UI on :8501
```

Or with Docker (builds the API + UI from this repo):

```bash
docker compose up --build
```

## Run the pipeline once

```bash
python run_pipeline.py --dry-run   # discover only, change nothing
python run_pipeline.py             # full run: update -> graph -> index
```

## Deploy (free, no card)

- **UI:** Streamlit Community Cloud, from this repo
  (`ui/app.py`, requirements from `requirements-cloud.txt`).
  Set `NAVIGATOR_MODE=cloud` and the LLM keys in Streamlit secrets.
- **Pipeline:** GitHub Actions (`.github/workflows/daily-pipeline.yml`),
  08:00 IST daily. Add the four API keys as repository secrets.

## Everything is free-tier

Groq, z.ai GLM-4.7-Flash and NVIDIA NIM (LLMs), Tavily (1,000
credits/month), BGE embeddings (local), Streamlit Community Cloud,
GitHub Actions on public repos. No card anywhere.

## Known limitations (honest)

- The corpus is 9 notifications; most are closed, so **Jobs for Me**
  shows 0 open jobs until the daily pipeline adds new ones.
- One extracted fact is a hallucination: the graph says IBPS PO's
  age limit is "21-30 years" — the notification says **20-30**.
  (The eval set was corrected; the graph cleanup is pending.)
- The SBI PO fee (Rs. 750) is in the corpus but unreachable: its
  relations were headed by a section title and dropped in Phase 3.
- Extraction recall is imperfect: RRB's Station Master post was
  never extracted, so the RRB summary lists 4 of 5 Graduate posts.
