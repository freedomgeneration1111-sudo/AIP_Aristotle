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


async def get_mastery(
    student_id: str | None = None,
    plan_id: str | None = None,
) -> dict:
    """GET /aristotle/dashboard — mastery + struggle + due items.

    Task 20: optional plan_id scopes the concept/mastery join to one
    plan (closes the "every subject mixed together" bug on /stats and
    /map). When plan_id is None the call is unscoped — the backend
    logs a dashboard_route_unscoped_call warning so the unscoped usage
    is visible in logs.
    """
    params = {}
    if student_id:
        params["student_id"] = student_id
    if plan_id:
        params["plan_id"] = plan_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/dashboard", params=params, timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}


async def get_misconceptions(
    student_id: str | None = None,
    limit: int = 20,
    plan_id: str | None = None,
) -> list:
    """GET /aristotle/misconceptions — recent misconception log entries.

    Task 20: plan_id param added for symmetry with the other helpers.
    Note: the /misconceptions route itself is still unwired on the
    backend (see STATUS.md "Still unwired" list) — this helper will
    return [] until that route lands. The plan_id param is accepted
    now so the GUI can thread it through without another api_client
    edit when the route ships.
    """
    params = {"limit": limit}
    if plan_id:
        params["plan_id"] = plan_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{_BASE}/aristotle/misconceptions", params=params, timeout=3.0
            )
            r.raise_for_status()
            return r.json().get("misconceptions", [])
    except Exception:
        return []


async def get_struggle_patterns(
    student_id: str | None = None,
    plan_id: str | None = None,
) -> list:
    """GET struggle patterns from MENTOR synthesis.

    The dashboard route returns a single struggle_pattern string (not a list).
    We wrap it in a list for the GUI to render uniformly.

    Task 20: plan_id is forwarded to get_mastery() so the struggle
    pattern lookup honors the same scope as the rest of the page.
    """
    try:
        data = await get_mastery(student_id=student_id, plan_id=plan_id)
        pattern = data.get("struggle_pattern")
        if pattern:
            return [{"concept_name": "Overall", "pattern": pattern}]
        return []
    except Exception:
        return []


async def get_concepts(plan_id: str | None = None) -> list:
    """GET /aristotle/concepts — concept list with id, topic, subtopic.

    Task 20: optional plan_id filters server-side to one plan's
    concepts only (closes the "every subject mixed together" bug on
    /map and /stats). When plan_id is None the call is unscoped — the
    backend logs a concepts_route_unscoped_call warning.
    """
    params = {}
    if plan_id:
        params["plan_id"] = plan_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/concepts", params=params, timeout=3.0)
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def get_settings(student_id: str | None = None) -> dict:
    """GET /aristotle/settings — student settings or defaults.

    Task 20: settings are student-global, NOT per-plan — confirmed by
    reading M005_aristotle_settings.sql (PRIMARY KEY on student_id,
    no plan_id column). So this helper takes no plan_id and the
    /aristotle/settings page does NOT get a plan selector. Forcing
    per-plan scoping here would build a selector that doesn't do
    anything — flagged back per the task brief rather than faked.
    """
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


async def get_session_history(
    limit: int = 15,
    plan_id: str | None = None,
) -> list:
    """GET /aristotle/session-history — list of session dicts.

    Task 20: plan_id param added for symmetry. Note: like
    /misconceptions, the /session-history route is still unwired on
    the backend (STATUS.md "Still unwired") — this helper returns []
    until that route lands. The plan_id param is accepted now so the
    GUI can thread it through without another api_client edit when
    the route ships.
    """
    params = {"limit": limit}
    if plan_id:
        params["plan_id"] = plan_id
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{_BASE}/aristotle/session-history",
                params=params,
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


async def delete_plan(plan_id: str) -> dict:
    """DELETE /aristotle/plans/{plan_id} — cascade-delete a plan + all its rows.

    Task 20: backs the delete affordance in the /ask plan picker.
    Destructive — no soft-delete, no undo. Returns {} on failure (the
    GUI shows the user the error rather than retrying silently).

    Returns on success: {
        "deleted": True,
        "plan_id": str,
        "subject": str,
        "concepts_deleted": int,
        "cascade_rows_deleted": int,
    }
    """
    try:
        async with httpx.AsyncClient() as c:
            r = await c.delete(
                f"{_BASE}/aristotle/plans/{plan_id}",
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return {}
