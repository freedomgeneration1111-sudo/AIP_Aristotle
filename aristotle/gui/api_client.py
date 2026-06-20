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
    """GET misconception log.

    TODO: wire /aristotle/misconceptions route — does not exist yet.
    The dashboard route returns struggle_pattern (a single string),
    not the misconception log. For now, return [].
    """
    # TODO: add GET /aristotle/misconceptions route to api.py
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
    """GET ARISTOTLE settings/profile.

    TODO: wire /aristotle/settings route — does not exist yet.
    """
    # TODO: add GET /aristotle/settings route to api.py
    return {}


async def update_settings(
    student_id: str | None = None, settings: dict = {}
) -> dict:
    """POST/PATCH ARISTOTLE settings.

    TODO: wire /aristotle/settings route — does not exist yet.
    """
    # TODO: add POST /aristotle/settings route to api.py
    return {}
