"""Aristotle API routes — mounted by the platform's FastAPI app.

These routes expose the tutoring loop to the CLI (HTTP client) and
eventually the GUI. The platform includes them via a dynamic router
mount (the extension registers its router via the host's API surface).

For Phase A pre-alpha, the platform includes these routes manually in
app.py (or via a future entry-point-based route discovery). The routes
access the container via the same Depends(get_container) pattern as
the platform's own routes.

Routes:
  GET  /aristotle/concepts       — list all ingested concepts
  POST /aristotle/ingest          — ingest concepts from YAML content
  POST /aristotle/session/start   — start a new tutoring session
  POST /aristotle/session/step    — advance a session one step
  POST /aristotle/session/run     — run a full session (non-interactive)

Layer: imports from aip.adapter.api.dependencies (get_container) — this
is the composition-root pattern (the API layer is allowed to access the
container). Also imports from aip.foundation.protocols.actors (ActorContext)
and aristotle's own modules.

Wait — the boundary test forbids aip.adapter imports outside the allowlist.
The API routes need get_container. Options:
1. Add aip.adapter.api.dependencies to the allowlist (expands the boundary).
2. The routes receive the container as a parameter (injected by the platform's
   route registration).

Option 2 is cleaner but requires the platform to know about ARISTOTLE's
routes. For pre-alpha, the simplest approach: the API routes receive the
container from the request's app.state.container (FastAPI's app state),
which doesn't require importing from aip.adapter.api.dependencies.

Actually, the cleanest pre-alpha approach: the routes are FastAPI router
functions that receive the container via `request.app.state.container`.
This is the standard FastAPI pattern and doesn't import anything from aip.*.
The platform's app.py sets `app.state.container` in the lifespan.
"""
from __future__ import annotations

import logging
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request

from aip.foundation.protocols.actors import ActorContext
from aristotle.ingestor import ingest_concepts_from_yaml, list_concepts
from aristotle.session import (
    SessionContext,
    SessionState,
    run_session_step,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aristotle", tags=["aristotle"])


def _get_container(request: Request) -> Any:
    """Get the AipContainer from the FastAPI app state."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=503, detail="Container not available")
    return container


def _make_ctx(container: Any, config: Any = None) -> ActorContext:
    """Build an ActorContext from the container."""
    import asyncio
    return ActorContext(
        container=container,
        config=config,
        logger=logging.getLogger("aristotle.api"),
        cancel_event=asyncio.Event(),
    )


# ------------------------------------------------------------------
# Concept routes
# ------------------------------------------------------------------


@router.get("/concepts")
async def list_concepts_route(request: Request):
    """List all ingested concepts in the textbook corpus."""
    container = _get_container(request)
    ctx = _make_ctx(container)
    concepts = await list_concepts(ctx)
    return concepts


@router.post("/ingest")
async def ingest_route(request: Request):
    """Ingest concepts from YAML content.

    Request body: {"yaml_content": "<YAML string>"}
    """
    container = _get_container(request)
    body = await request.json()
    yaml_content = body.get("yaml_content", "")
    if not yaml_content:
        raise HTTPException(status_code=400, detail="yaml_content required")

    # Write to a temp file (the ingestor takes a path)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        ctx = _make_ctx(container)
        result = await ingest_concepts_from_yaml(ctx, temp_path)
        return result
    finally:
        import os
        os.unlink(temp_path)


# ------------------------------------------------------------------
# Session routes
# ------------------------------------------------------------------


@router.post("/session/start")
async def session_start_route(request: Request):
    """Start a new tutoring session.

    Request body: {"concept_id": "newton_first_law"}
    Returns: the initial SessionContext (state=TEACH).
    """
    container = _get_container(request)
    body = await request.json()
    concept_id = body.get("concept_id", "")
    if not concept_id:
        raise HTTPException(status_code=400, detail="concept_id required")

    session = SessionContext(concept_id=concept_id, state=SessionState.TEACH)
    return _session_to_dict(session)


@router.post("/session/step")
async def session_step_route(request: Request):
    """Advance a tutoring session one step.

    Request body: {"session": <SessionContext dict>, "student_input": "..."}
    Returns: {"session": <updated SessionContext>, "output": "...", "ok": true}
    """
    container = _get_container(request)
    body = await request.json()
    session_dict = body.get("session", {})
    student_input = body.get("student_input", "")

    session = _session_from_dict(session_dict)
    ctx = _make_ctx(container)

    result = await run_session_step(ctx, session, student_input)
    return {
        "session": _session_to_dict(session),
        "output": result.error if result.ok else "",
        "ok": result.ok,
        "error": result.error if not result.ok else None,
    }


@router.post("/session/run")
async def session_run_route(request: Request):
    """Run a full tutoring session non-interactively.

    Request body: {"concept_id": "newton_first_law", "answers": ["answer1", "answer2"]}
    Returns: {"concept_id": ..., "mastered": ..., "last_score": ..., "steps": [...]}

    The answers are used for PROBE and QUIZ steps in order. If fewer answers
    are provided than steps require, the session stops at the first step
    without an answer.
    """
    container = _get_container(request)
    body = await request.json()
    concept_id = body.get("concept_id", "")
    answers = body.get("answers", [])

    if not concept_id:
        raise HTTPException(status_code=400, detail="concept_id required")

    session = SessionContext(concept_id=concept_id, state=SessionState.TEACH)
    ctx = _make_ctx(container)
    answer_idx = 0
    steps: list[dict] = []
    max_steps = 20  # safety limit

    for _ in range(max_steps):
        if session.state.value == "SESSION_COMPLETE":
            break

        student_input = ""
        if session.state == SessionState.QUIZ and session.quiz_generated and answer_idx < len(answers):
            student_input = answers[answer_idx]
            answer_idx += 1

        result = await run_session_step(ctx, session, student_input)
        steps.append({
            "state": session.state.value,
            "output": result.error if result.ok else "",
            "ok": result.ok,
        })

        if not result.ok:
            break

    return {
        "concept_id": concept_id,
        "mastered": session.mastered,
        "last_score": session.last_score,
        "steps": steps,
    }


# ------------------------------------------------------------------
# Dashboard route (Phase B — teacher view, ADR-001 §8)
# ------------------------------------------------------------------


@router.get("/dashboard")
async def dashboard_route(request: Request):
    """Teacher dashboard — per-student mastery + struggle + due items (ADR-001 §8).

    Returns:
        {
            "student_id": "definer",
            "total_concepts": int,
            "mastered_count": int,
            "due_count": int,
            "struggle_pattern": str | null,
            "mastery_by_concept": [
                {
                    "concept_id": str,
                    "topic": str,
                    "mastered": bool,
                    "last_score": float | null,
                    "repetitions": int,
                    "next_review_at": str | null,
                    "is_due": bool,
                    "updated_at": str | null
                }
            ]
        }

    Uses a LEFT JOIN so ALL concepts appear — including ones with no
    mastery record yet (never studied). Unstarted concepts show
    mastered=false, last_score=null, repetitions=0, next_review_at=null,
    is_due=false.

    Sort order: due items first (next_review_at past or null with
    repetitions>0), then unstarted (repetitions=0), then mastered.

    Pulls from aristotle_mastery + aristotle_concept +
    aristotle_struggle_pattern via
    corpus_registry.get_stores("aristotle:textbook"). student_id is
    always "definer" (single-tenant pre-alpha).
    """
    container = _get_container(request)
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="corpus_registry not available")

    try:
        stores = await registry.get_stores("aristotle:textbook")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"corpus access failed: {exc}")

    conn = stores.connection_manager.write_conn
    student_id = "definer"

    # 1. Read struggle_pattern
    cur = await conn.execute(
        "SELECT pattern_text FROM aristotle_struggle_pattern WHERE student_id = ?",
        (student_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    struggle_pattern = row[0] if row is not None else None

    # 2. LEFT JOIN all concepts with mastery records.
    #    Concepts with no mastery row get NULLs (never studied).
    cur = await conn.execute(
        "SELECT c.id, c.topic, "
        "  COALESCE(m.mastered, 0) AS mastered, "
        "  m.last_score, "
        "  COALESCE(m.repetitions, 0) AS repetitions, "
        "  m.next_review_at, "
        "  m.updated_at "
        "FROM aristotle_concept c "
        "LEFT JOIN aristotle_mastery m "
        "  ON c.id = m.concept_id AND m.student_id = ? "
        "ORDER BY c.id",
        (student_id,),
    )
    rows = await cur.fetchall()
    await cur.close()

    # 3. Build mastery_by_concept with sort key
    from datetime import datetime, timezone

    mastery_by_concept: list[dict] = []
    mastered_count = 0
    due_count = 0

    for r in rows:
        concept_id = r[0]
        topic = r[1]
        mastered = bool(r[2])
        last_score = r[3]
        repetitions = r[4]
        next_review_at = r[5]
        updated_at = r[6]

        if mastered:
            mastered_count += 1

        # Determine is_due:
        # - If repetitions == 0 and next_review_at IS NULL → unstarted, NOT due
        # - If next_review_at IS NULL and repetitions > 0 → due (started but no schedule)
        # - If next_review_at is in the past → due
        # - If next_review_at is in the future → not due
        is_due = False
        if repetitions > 0:
            if next_review_at is None:
                is_due = True
            else:
                try:
                    next_review = datetime.fromisoformat(next_review_at)
                    if next_review.tzinfo is None:
                        next_review = next_review.replace(tzinfo=timezone.utc)
                    is_due = datetime.now(timezone.utc) >= next_review
                except (ValueError, TypeError):
                    is_due = True
        # Unstarted (repetitions == 0) → is_due stays False

        if is_due:
            due_count += 1

        # Sort key: 0 = due (needs attention), 1 = unstarted, 2 = mastered, 3 = not due
        if is_due:
            sort_priority = 0
        elif repetitions == 0:
            sort_priority = 1
        elif mastered:
            sort_priority = 2
        else:
            sort_priority = 3

        mastery_by_concept.append({
            "concept_id": concept_id,
            "topic": topic,
            "mastered": mastered,
            "last_score": last_score,
            "repetitions": repetitions,
            "next_review_at": next_review_at,
            "is_due": is_due,
            "updated_at": updated_at,
            "_sort_priority": sort_priority,
        })

    # Sort: due first, then unstarted, then mastered, then not-due.
    # Within each priority, sort by next_review_at ascending (nulls first), then concept_id.
    mastery_by_concept.sort(
        key=lambda m: (m["_sort_priority"], m["next_review_at"] or "0000", m["concept_id"])
    )

    # Strip the internal sort key before returning
    for m in mastery_by_concept:
        del m["_sort_priority"]

    return {
        "student_id": student_id,
        "total_concepts": len(rows),
        "mastered_count": mastered_count,
        "due_count": due_count,
        "struggle_pattern": struggle_pattern,
        "mastery_by_concept": mastery_by_concept,
    }


# ------------------------------------------------------------------
# Serialization helpers
# ------------------------------------------------------------------


def _session_to_dict(session: SessionContext) -> dict:
    """Serialize a SessionContext to a JSON-safe dict."""
    return {
        "student_id": session.student_id,
        "concept_id": session.concept_id,
        "state": session.state.value,
        "last_explanation": session.last_explanation,
        "last_probe_question": session.last_probe_question,
        "last_quiz_question": session.last_quiz_question,
        "last_student_answer": session.last_student_answer,
        "last_evaluation": session.last_evaluation,
        "last_score": session.last_score,
        "mastered": session.mastered,
        "retry_count": session.retry_count,
        "max_retries": session.max_retries,
        "quiz_generated": session.quiz_generated,
        "probe_generated": session.probe_generated,
    }


def _session_from_dict(d: dict) -> SessionContext:
    """Deserialize a SessionContext from a dict."""
    return SessionContext(
        student_id=d.get("student_id", "definer"),
        concept_id=d.get("concept_id", ""),
        state=SessionState(d.get("state", "TEACH")),
        last_explanation=d.get("last_explanation", ""),
        last_probe_question=d.get("last_probe_question", ""),
        last_quiz_question=d.get("last_quiz_question", ""),
        last_student_answer=d.get("last_student_answer", ""),
        last_evaluation=d.get("last_evaluation", ""),
        last_score=d.get("last_score", 0.0),
        mastered=d.get("mastered", False),
        retry_count=d.get("retry_count", 0),
        max_retries=d.get("max_retries", 2),
        quiz_generated=d.get("quiz_generated", False),
        probe_generated=d.get("probe_generated", False),
    )
