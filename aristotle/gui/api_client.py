"""ARISTOTLE GUI API client — thin httpx client for ARISTOTLE API.

All functions are best-effort: catch httpx exceptions, return {} or [].
The ARISTOTLE backend runs on port 8001 (separate from Brain's :8000).
"""
from __future__ import annotations

import os

import httpx

_BASE = os.getenv("ARISTOTLE_BACKEND_URL", "http://localhost:8001")


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


async def get_misconceptions(
    student_id: str | None = None, limit: int = 20
) -> list:
    """GET /aristotle/misconceptions — recent misconception log entries."""
    params = {"limit": limit}
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{_BASE}/aristotle/misconceptions",
                            params=params, timeout=3.0)
            r.raise_for_status()
            return r.json().get("misconceptions", [])
    except Exception:
        return []


async def get_struggle_patterns(
    student_id: str | None = None
) -> list:
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


async def update_settings(
    student_id: str | None = None, settings: dict = {}
) -> dict:
    """POST /aristotle/settings — create or update settings (upsert)."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{_BASE}/aristotle/settings",
                             json=settings, timeout=3.0)
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
