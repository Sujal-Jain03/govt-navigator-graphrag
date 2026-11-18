"""Data layer for the Streamlit UI — two backends, one interface.

HTTP mode (default — local dev and Docker): the UI talks to the
FastAPI server over HTTP (API_URL env, default
http://localhost:8000). The UI process holds no model and no keys.

Cloud mode (NAVIGATOR_MODE=cloud — Streamlit Community Cloud):
there is no separate API server, so the UI calls the service
layer (app.api.service) directly, in-process. The BGE model and
the graph load inside the Streamlit process; LLM keys come from
the environment (Streamlit secrets are exposed as env vars).

Every function returns exactly what the matching API endpoint
returns, so the pages don't care which mode they're in. Failures
raise BackendError with a human-readable message in both modes.
"""
import os
from datetime import date

import requests

API_URL = os.environ.get("API_URL", "http://localhost:8000")
TIMEOUT = 120  # LLM answers can take a while on the free tier
CLOUD = os.environ.get("NAVIGATOR_MODE", "").strip().lower() == "cloud"


class BackendError(Exception):
    """A backend failure with a human-readable message."""


def _service():
    """Lazy import: the service layer (and its heavy deps) loads
    only in cloud mode, never in HTTP mode."""
    from app.api import service
    return service


def _get(path):
    try:
        r = requests.get(f"{API_URL}{path}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise BackendError(f"API request failed: {exc}")


def _post(path, payload):
    try:
        r = requests.post(f"{API_URL}{path}", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        try:
            detail = exc.response.json()["detail"]
        except Exception:
            detail = str(exc)
        raise BackendError(detail)
    except requests.RequestException as exc:
        raise BackendError(f"API request failed: {exc}")


def health():
    """Liveness + index stats (the /health response shape)."""
    if CLOUD:
        try:
            return {"status": "ok", **_service().index_stats()}
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            raise BackendError(f"service failed to start: {exc}")
    try:
        return _get("/health")
    except BackendError:
        raise BackendError(
            "The API is not reachable. Start it first with "
            "`python run_api.py`, then refresh this page.")


def ask(query):
    """Q&A with citations (the /ask response shape)."""
    if CLOUD:
        try:
            return _service().ask_question(query)
        except ValueError as exc:
            raise BackendError(str(exc))
    return _post("/ask", {"query": query})


def _profile_kwargs(payload):
    """Shared payload -> service kwargs (cloud mode only).

    The UI builds the same payload dicts the API takes; the
    service layer wants real date objects, not ISO strings.
    ref_date is passed through when present (the UI never sends
    it; tests pin it for stable verdicts).
    """
    kwargs = {
        "name": payload.get("name", ""),
        "date_of_birth": date.fromisoformat(payload["date_of_birth"]),
        "qualification": payload.get("qualification"),
        "category": payload.get("category", "General"),
        "qualifications": payload.get("qualifications"),
        "experience_years": payload.get("experience_years", 0.0),
    }
    if payload.get("ref_date"):
        kwargs["ref_date"] = date.fromisoformat(payload["ref_date"])
    return kwargs


def check_eligibility(payload):
    """Per-exam verdicts for a profile (the /eligibility shape)."""
    if CLOUD:
        try:
            return _service().check_eligibility(**_profile_kwargs(payload))
        except ValueError as exc:
            raise BackendError(str(exc))
    return _post("/eligibility", payload)


def list_jobs(payload):
    """Jobs the profile can enroll in now (the /jobs shape)."""
    if CLOUD:
        try:
            return _service().list_jobs(**_profile_kwargs(payload))
        except ValueError as exc:
            raise BackendError(str(exc))
    return _post("/jobs", payload)


def list_exams():
    """One card's worth of facts per exam (the /exams shape)."""
    if CLOUD:
        return _service().list_exams()
    return _get("/exams")


def latest_updates():
    """Newest report + fresh expiry (the /updates/latest shape)."""
    if CLOUD:
        return _service().latest_updates()
    return _get("/updates/latest")
