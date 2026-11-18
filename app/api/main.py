"""Govt Navigator API (Phase 10).

FastAPI front-end over app.api.service. Run with:
    python run_api.py
then open http://localhost:8000/docs for the interactive docs.

Endpoints:
    GET  /health          - liveness + index stats
    POST /ask             - Q&A with citations (guardrailed)
    POST /eligibility     - per-exam verdicts for a profile
    POST /jobs            - jobs the profile can enroll in now
    GET  /exams           - one card of facts per exam
    GET  /updates/latest  - newest daily-update report + expiry

Local-only by design: CORS is wide open because the Streamlit UI
runs on the same machine. Do not expose this port publicly.
"""
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api import service


@asynccontextmanager
async def lifespan(_app):
    # Warm the heavy singletons at startup so the first request
    # is fast (BGE model + index load takes ~30-60s).
    service.get_retriever()
    service.get_rules()
    service.get_summaries()
    yield


app = FastAPI(title="Govt Navigator API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    # No min_length here: an empty query must reach check_input,
    # which blocks it with a clear reason (400), not a bare 422.
    query: str = Field(..., description="Question to answer")


class EligibilityRequest(BaseModel):
    name: str = ""
    date_of_birth: date
    qualification: str | None = Field(
        default=None,
        description="Single level; ignored when qualifications is given")
    qualifications: list[str] | None = Field(
        default=None,
        description="All education levels held; matching uses the highest")
    experience_years: float = Field(
        default=0.0, ge=0.0, le=60.0,
        description="Total work experience in years")
    category: str = "General"
    ref_date: date | None = Field(
        default=None, description="Age/last-date reference; default today")


@app.get("/health")
def health():
    return {"status": "ok", **service.index_stats()}


@app.post("/ask")
def ask(req: AskRequest):
    try:
        return service.ask_question(req.query)
    except ValueError as exc:
        # Guardrail block (injection / PII / empty / overlong).
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/eligibility")
def eligibility(req: EligibilityRequest):
    try:
        return service.check_eligibility(
            name=req.name,
            date_of_birth=req.date_of_birth,
            qualification=req.qualification,
            category=req.category,
            ref_date=req.ref_date,
            qualifications=req.qualifications,
            experience_years=req.experience_years,
        )
    except ValueError as exc:
        # Unknown qualification level or category, or no qualification.
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/jobs")
def jobs(req: EligibilityRequest):
    # Jobs for Me (Phase 11): the profile shape is identical to
    # /eligibility, so the same request model is reused.
    try:
        return service.list_jobs(
            name=req.name,
            date_of_birth=req.date_of_birth,
            qualification=req.qualification,
            category=req.category,
            ref_date=req.ref_date,
            qualifications=req.qualifications,
            experience_years=req.experience_years,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/exams")
def exams():
    return service.list_exams()


@app.get("/updates/latest")
def updates_latest():
    return service.latest_updates()
