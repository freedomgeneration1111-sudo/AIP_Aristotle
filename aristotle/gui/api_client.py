"""ARISTOTLE GUI API client — thin httpx client for ARISTOTLE API.

All functions are best-effort: catch httpx exceptions, return {} or [].

The ARISTOTLE API is mounted on the SAME backend as Brain's core API —
the extension host mounts the aristotle router at stage 6 of the
ExtensionHost lifecycle (extension_api_router_mounted ext='aristotle'),
so /aristotle/* routes live on Brain's :8000 backend, NOT a separate
port. The AIP_BACKEND_URL env var (same one Brain's gui/pages/ask.py
reads) selects the backend; default http://127.0.0.1:8000 matches the
default in start.sh.

(Task 19 / bug fix: this previously pointed at
ARISTOTLE_BACKEND_URL=http://localhost:8001 — a port nothing was
listening on. Every dashboard / stats / map / settings / session-history
call silently failed into {} / [] and the GUI rendered empty. The
ARISTOTLE_BACKEND_URL env var is still respected if set, for the rare
case where someone runs Aristotle's API on a separate port — but the
default now matches reality.)
"""

from __future__ import annotations

import os

import httpx

# Same env var + default as Brain's gui/pages/ask.py::_BACKEND_URL — the
# Aristotle router is mounted on Brain's backend, not a separate one.
_BASE = os.getenv("ARISTOTLE_BACKEND_URL",
                  os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000"))


async def get_mastery(student_id: str | None = None) -> dict:
    """GET /aristotle/dashboard — mastery + struggle + due items."""
    params = {}
    if student_id:
        params["student_id"] = student_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/dashboard", params=params, timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


async def get_misconceptions(student_id: str | None = None, limit: int = 20) -> list:
    """GET /aristotle/misconceptions — recent misconception log entries."""
    params = {"limit": limit}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{_BASE}/aristotle/misconceptions", params=params, timeout=3.0
            )
            r.raise_for_status()
            return r.json().get("misconceptions", [])
    except Exception:
        return []


async def get_struggle_patterns(student_id: str | None = None) -> list:
    """GET struggle patterns from MENTOR synthesis.

    The dashboard route returns a single struggle_pattern string (not a list).
    We wrap it in a list for the GUI to render uniformly.
    """
    try:
        data = await get_mastery(student_id)
        pattern = data.get("struggle_pattern")
        if pattern:
            return [{"concept_name": "Overall", "pattern": pattern}]
        return []
    except Exception:
        return []


async def get_concepts() -> list:
    """GET /aristotle/concepts — concept list with id, topic, subtopic."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/concepts", timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def get_settings(student_id: str | None = None) -> dict:
    """GET /aristotle/settings — student settings or defaults."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/settings", timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


async def update_settings(student_id: str | None = None, settings: dict = {}) -> dict:
    """POST /aristotle/settings — create or update settings (upsert)."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{_BASE}/aristotle/settings", json=settings, timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


async def get_session_history(limit: int = 15) -> list:
    """GET /aristotle/session-history — list of session dicts."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{_BASE}/aristotle/session-history",
                params={"limit": limit},
                timeout=3.0,
            )
            r.raise_for_status()
            return r.json().get("sessions", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Task 19: student + plan scoping (ADR-004 GUI half — picker surface).
# These back the "resume an existing subject / start a new one" picker on
# the /ask page. Without them, every page load kicked off a fresh intake
# and existing plans were undiscoverable from the UI.
# ---------------------------------------------------------------------------


async def get_students() -> list:
    """GET /aristotle/students — list of {id, name, created_at}.

    Used by the student picker (planned: full ADR-004 GUI; current:
    picker not yet wired, this helper exists so the GUI can call it
    when it lands).
    """
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/students", timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def create_student(name: str) -> dict:
    """POST /aristotle/students — create a new student identity.

    Returns {id, name} on success, {} on failure.
    """
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{_BASE}/aristotle/students",
                json={"name": name},
                timeout=3.0,
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


async def get_plans(student_id: str | None = None) -> list:
    """GET /aristotle/plans?student_id=X — list of learning plans.

    Returns a list of {id, subject, status, current_concept_idx,
    total_concepts, created_at, last_session_at, material_id} dicts.
    Defaults student_id to 'definer' when None (preserves pre-Task-18
    single-tenant behavior on the API side).

    This is the endpoint the /ask page picker calls to populate its
    "Resume <subject>" list. Each plan represents one subject's worth
    of tutoring — what the user called a "database" in the bug report.
    """
    params = {}
    if student_id:
        params["student_id"] = student_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{_BASE}/aristotle/plans",
                params=params,
                timeout=3.0,
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return []
