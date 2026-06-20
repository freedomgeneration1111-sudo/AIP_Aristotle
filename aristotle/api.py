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
from datetime import datetime, timezone
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


# ------------------------------------------------------------------
# Health route (liveness probe for the GUI sidebar health poller)
# ------------------------------------------------------------------


@router.get("/health")
async def health_route():
    """Liveness probe.

    Returns HTTP 200 + a minimal JSON payload. Used by Brain's GUI
    sidebar (_poll_extension_health in gui/components/layout.py) to
    decide whether to show this extension's nav items. Does NOT
    touch the container or DB — a healthy process is enough.
    """
    return {"status": "ok", "extension": "aristotle"}


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
    Returns: the initial SessionContext (state=PREDICT) with the
    interleaved concept_queue populated.

    Phase B.5 (interleaving, B.5 item 5): the session builds a concept
    queue at start — slot 0 is the primary concept (the one the caller
    passed), slots 1-2 are due review concepts (selected by
    _build_concept_queue from aristotle_mastery where next_review_at
    <= now). When the primary concept is mastered (NEXT_CONCEPT), the
    session advances to the next concept in the queue. If the queue
    has only the primary, the session degrades to single-concept
    (Phase A behavior).

    Phase B.5 (cold-start, B.5 item 9): review concepts whose SM-2
    interval >= 7 days AND cold_start_passed == 0 are added to
    cold_start_pending. When the session reaches such a concept, it
    skips PREDICT/TEACH and goes directly to PROBE (unassisted
    retrieval) — the cold-start check catches overreliance on hints.
    """
    container = _get_container(request)
    body = await request.json()
    concept_id = body.get("concept_id", "")
    plan_id = body.get("plan_id", "")
    if not concept_id and not plan_id:
        raise HTTPException(status_code=400, detail="concept_id or plan_id required")

    # Build the interleaved concept queue + cold-start pending set.
    # Phase D: if plan_id is provided, the primary concept comes from the
    # plan (concept_ids_json[current_concept_idx]), not the caller's concept_id.
    from aristotle.session import _build_concept_queue
    queue, cold_start_pending = await _build_concept_queue(
        _make_ctx(container), concept_id, plan_id=plan_id,
    )

    # Determine the actual concept_id (from the queue if available).
    actual_concept_id = queue[0] if queue else concept_id

    session = SessionContext(
        concept_id=actual_concept_id,
        state=SessionState.PREDICT,
        concept_queue=queue,
        cold_start_pending=cold_start_pending,
        plan_id=plan_id,
    )

    # Notify Brain GUI that an ARISTOTLE session is active.
    try:
        from gui.components.layout import set_active_extension
        set_active_extension("aristotle", "Tutoring")
    except ImportError:
        pass  # Running outside Brain GUI — no-op

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

    # Notify Brain GUI when session completes.
    if session.state.value == "SESSION_COMPLETE":
        try:
            from gui.components.layout import clear_active_extension
            clear_active_extension()
        except ImportError:
            pass  # Running outside Brain GUI — no-op

    # ADR-002 Amendment A1: include intent_class in response when available
    # (curiosity/chat paths set it on result.data).
    intent_class = None
    if result.ok and result.data is not None and isinstance(result.data, dict):
        intent_class = result.data.get("intent_class")

    response = {
        "session": _session_to_dict(session),
        "output": result.error if result.ok else "",
        "ok": result.ok,
        "error": result.error if not result.ok else None,
    }
    if intent_class:
        response["intent_class"] = intent_class

    return response


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

    # Phase B.5: start at PREDICT. The first answer (if provided) is the
    # learner's prediction; subsequent answers are for QUIZ as before.
    session = SessionContext(concept_id=concept_id, state=SessionState.PREDICT)
    ctx = _make_ctx(container)
    answer_idx = 0
    steps: list[dict] = []
    max_steps = 25  # safety limit (was 20 — +5 for PREDICT two-phase + HINT ladder headroom)

    for _ in range(max_steps):
        if session.state.value == "SESSION_COMPLETE":
            break

        student_input = ""
        # PREDICT phase: consume the first answer as the prediction.
        # The predict step is two-phase: first call generates the prompt
        # (no input), second call records the prediction (needs input).
        if session.state == SessionState.PREDICT and session.predict_generated and answer_idx < len(answers):
            student_input = answers[answer_idx]
            answer_idx += 1
        # QUIZ phase: consume an answer for the quiz (same as Phase A).
        elif session.state == SessionState.QUIZ and session.quiz_generated and answer_idx < len(answers):
            student_input = answers[answer_idx]
            answer_idx += 1
        # HINT_1/HINT_2 phase 2: consume an answer for the re-evaluation
        # after the learner sees the hint. Phase B.5 HINT ladder.
        elif (session.state in (SessionState.HINT_1, SessionState.HINT_2)
              and session.hint_generated
              and answer_idx < len(answers)):
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
# Intake routes (Phase D — onboarding, ADR-002 Rev 2 §9)
# ------------------------------------------------------------------


@router.post("/intake/start")
async def intake_start_route(request: Request):
    """Start a new intake session or resume from a trigger.

    Request body: {"plan_id": str | None, "student_input": str | None}
    Returns: {"session": dict, "prompt": str | None, "trigger": str | None}

    Phase D (ADR-002 §9): calls check_intake_triggers(ctx, plan_id). If a
    trigger exists, builds an IntakeSession with the trigger's entry_state.
    If the trigger has a pre-built prompt (checkin level), returns it
    directly. Otherwise calls run_intake_step to generate the first prompt.
    """
    container = _get_container(request)
    body = await request.json()
    plan_id = body.get("plan_id")
    student_input = body.get("student_input", "")

    ctx = _make_ctx(container)

    from aristotle.actors.intake import (
        IntakeSession,
        IntakeState,
        check_intake_triggers,
        run_intake_step,
        intake_session_to_dict,
    )

    trigger = await check_intake_triggers(ctx, plan_id)

    if trigger is not None:
        # Build a session starting at the trigger's entry_state.
        session = IntakeSession(
            state=trigger.entry_state,
            entry_state=trigger.entry_state,
        )
        if trigger.prompt:
            # Checkin level: return the pre-built prompt directly.
            return {
                "session": intake_session_to_dict(session),
                "prompt": trigger.prompt,
                "trigger": trigger.level,
            }
        else:
            # Full or partial: generate the first prompt via run_intake_step.
            result = await run_intake_step(session, student_input, ctx)
            return {
                "session": intake_session_to_dict(session),
                "prompt": result.get("prompt"),
                "trigger": trigger.level,
                "state": result.get("state"),
            }
    else:
        # No trigger — normal intake from GREETING.
        session = IntakeSession()
        result = await run_intake_step(session, student_input, ctx)
        return {
            "session": intake_session_to_dict(session),
            "prompt": result.get("prompt"),
            "trigger": None,
            "state": result.get("state"),
        }


@router.post("/intake/step")
async def intake_step_route(request: Request):
    """Advance an intake session one step.

    Request body: {"session": dict, "student_input": str}
    Returns: {"session": dict, "prompt": str | None, "state": str,
              "plan_id": str | None, "pivot": str | None}

    Also runs _detect_intake_intent(student_input) before dispatching —
    if a trigger is detected mid-session, overrides the session state
    accordingly and notes the pivot in the response.
    """
    container = _get_container(request)
    body = await request.json()
    session_dict = body.get("session", {})
    student_input = body.get("student_input", "")

    ctx = _make_ctx(container)

    from aristotle.actors.intake import (
        _detect_intake_intent,
        run_intake_step,
        intake_session_to_dict,
        intake_session_from_dict,
    )

    session = intake_session_from_dict(session_dict)

    # Intent detection: check if the learner's input signals a re-INTAKE
    # trigger mid-session. If so, override the session state + note the pivot.
    pivot = None
    if student_input:
        trigger = _detect_intake_intent(student_input)
        if trigger is not None:
            pivot = trigger.level
            session.entry_state = trigger.entry_state
            session.state = trigger.entry_state

    result = await run_intake_step(session, student_input, ctx)

    return {
        "session": intake_session_to_dict(session),
        "prompt": result.get("prompt"),
        "state": result.get("state"),
        "plan_id": result.get("plan_id"),
        "pivot": pivot,
    }


# ------------------------------------------------------------------
# Placer routes (Phase D — placement calibration, ADR-002 §9 stage 5)
# ------------------------------------------------------------------


@router.post("/placer/start")
async def placer_start_route(request: Request):
    """Start a placement calibration session.

    Request body: {"plan_id": str}
    Returns: {"session": dict, "question": str | None, "state": str,
              "concepts_placed": int}

    Phase D (ADR-002 §9 stage 5): reads the learning_plan by plan_id,
    samples concepts for placement via _sample_concepts_for_placement,
    creates a PlacerSession, and generates the first probe question.
    """
    container = _get_container(request)
    body = await request.json()
    plan_id = body.get("plan_id", "")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id required")

    ctx = _make_ctx(container)

    from aristotle.actors.intake import (
        PlacerSession,
        _sample_concepts_for_placement,
        run_placer_step,
        placer_session_to_dict,
    )
    import json as _json

    # Read the plan's concept_ids_json.
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="corpus_registry not available")

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        cur = await conn.execute(
            "SELECT concept_ids_json FROM aristotle_learning_plan WHERE id = ?",
            (plan_id,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is None:
            raise HTTPException(status_code=404, detail=f"plan {plan_id!r} not found")

        concept_ids = _json.loads(row[0]) if row[0] else []
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"plan read failed: {exc}")

    # Sample concepts for placement.
    sampled = _sample_concepts_for_placement(concept_ids, n=7)
    session = PlacerSession(
        plan_id=plan_id,
        concepts_to_assess=sampled,
    )

    # Generate the first question.
    result = await run_placer_step(session, "", ctx)
    return {
        "session": placer_session_to_dict(session),
        "question": result.get("question"),
        "state": result.get("state"),
        "concepts_placed": result.get("concepts_placed", 0),
    }


@router.post("/placer/step")
async def placer_step_route(request: Request):
    """Advance a placement calibration session one step.

    Request body: {"session": dict, "student_input": str}
    Returns: {"session": dict, "question": str | None, "state": str,
              "concepts_placed": int, "concepts_known": int | None,
              "starting_concept_idx": int | None}

    When state="COMPLETE", the response includes concepts_known +
    starting_concept_idx (from _finalize_placement).
    """
    container = _get_container(request)
    body = await request.json()
    session_dict = body.get("session", {})
    student_input = body.get("student_input", "")

    ctx = _make_ctx(container)

    from aristotle.actors.intake import (
        run_placer_step,
        placer_session_to_dict,
        placer_session_from_dict,
    )

    session = placer_session_from_dict(session_dict)
    result = await run_placer_step(session, student_input, ctx)

    response = {
        "session": placer_session_to_dict(session),
        "question": result.get("question"),
        "state": result.get("state"),
        "concepts_placed": result.get("concepts_placed", 0),
    }

    if result.get("state") == "COMPLETE":
        response["concepts_known"] = result.get("concepts_known", 0)

    return response


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
# Misconception log route (Phase D — GUI data)
# ------------------------------------------------------------------


@router.get("/misconceptions")
async def misconceptions_route(
    request: Request, limit: int = 20
):
    """Misconception log — recent entries for the default student.

    Returns:
        {"misconceptions": [
            {
                "id": int,
                "concept_id": str,
                "misconception_text": str,
                "corrective_text": str,
                "created_at": str
            }
        ]}
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

    cur = await conn.execute(
        "SELECT id, concept_id, misconception_text, corrective_text, created_at "
        "FROM aristotle_misconception_log "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    await cur.close()

    misconceptions = [
        {
            "id": row[0],
            "concept_id": row[1],
            "misconception_text": row[2],
            "corrective_text": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]

    return {"misconceptions": misconceptions}


# ------------------------------------------------------------------
# Settings routes (Phase D — GUI data)
# ------------------------------------------------------------------


_DEFAULT_SETTINGS = {
    "student_id": "definer",
    "display_name": "",
    "primary_language": "English",
    "alt_language": "",
    "session_length": 5,
    "mastery_threshold": 0.85,
    "hint_aggressiveness": "balanced",
    "updated_at": None,
}


@router.get("/settings")
async def get_settings_route(request: Request):
    """Return current student settings (or defaults if not set).

    Returns the aristotle_settings row for student_id='definer',
    or default values if no row exists yet.
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

    cur = await conn.execute(
        "SELECT student_id, display_name, primary_language, alt_language, "
        "session_length, mastery_threshold, hint_aggressiveness, updated_at "
        "FROM aristotle_settings WHERE student_id = ?",
        ("definer",),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is None:
        return dict(_DEFAULT_SETTINGS)

    return {
        "student_id": row[0],
        "display_name": row[1] or "",
        "primary_language": row[2] or "English",
        "alt_language": row[3] or "",
        "session_length": row[4] if row[4] is not None else 5,
        "mastery_threshold": row[5] if row[5] is not None else 0.85,
        "hint_aggressiveness": row[6] or "balanced",
        "updated_at": row[7],
    }


@router.post("/settings")
async def update_settings_route(request: Request):
    """Create or update student settings (upsert).

    Request body:
        {
            "display_name": str | null,
            "primary_language": str,
            "alt_language": str | null,
            "session_length": int,
            "mastery_threshold": float,
            "hint_aggressiveness": str
        }

    Returns: updated settings dict
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
    body = await request.json()

    display_name = body.get("display_name", "")
    primary_language = body.get("primary_language", "English")
    alt_language = body.get("alt_language", "")
    session_length = int(body.get("session_length", 5))
    mastery_threshold = float(body.get("mastery_threshold", 0.85))
    hint_aggressiveness = body.get("hint_aggressiveness", "balanced")
    now = datetime.now(timezone.utc).isoformat()

    await conn.execute(
        "INSERT OR REPLACE INTO aristotle_settings "
        "(student_id, display_name, primary_language, alt_language, "
        "session_length, mastery_threshold, hint_aggressiveness, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "definer", display_name, primary_language, alt_language,
            session_length, mastery_threshold, hint_aggressiveness, now,
        ),
    )
    await conn.commit()

    return {
        "student_id": "definer",
        "display_name": display_name,
        "primary_language": primary_language,
        "alt_language": alt_language,
        "session_length": session_length,
        "mastery_threshold": mastery_threshold,
        "hint_aggressiveness": hint_aggressiveness,
        "updated_at": now,
    }


# ------------------------------------------------------------------
# Upload route (OCR — Phase D surface layer)
# ------------------------------------------------------------------


@router.post("/upload")
async def upload_route(request: Request):
    """Extract text from uploaded files in various formats.

    Accepts raw body bytes with Content-Type indicating the file type.
    Supported formats:
      - Text: .txt .md .markdown .csv .log .yaml .yml .json
      - HTML: .html .htm (tags stripped)
      - DOCX: .docx (python-docx)
      - PDF: .pdf (pypdf)
      - Image: .jpg .jpeg .png .webp .bmp .tiff (pytesseract OCR)

    DEFERRED formats (not yet supported): .epub .pptx .xlsx .rtf .doc

    Returns:
        {
            "extracted_text": str,
            "source_type": "text" | "html" | "docx" | "pdf" | "image",
            "page_count": int | null,
            "char_count": int
        }

    Errors return 400/415 with detail message.
    Max file size: 10MB (reject with 413 if exceeded).
    """
    import io
    import re

    content_type = request.headers.get("content-type", "").lower()
    content_disposition = request.headers.get("content-disposition", "")
    body = await request.body()

    if len(body) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    # Extract filename from Content-Disposition for extension fallback.
    filename = ""
    if content_disposition:
        match = re.search(r'filename="?([^";\n]+)"?', content_disposition)
        if match:
            filename = match.group(1)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Determine file type from Content-Type, then extension, then magic bytes.
    # DEFERRED: .epub .pptx .xlsx .rtf .doc

    # TEXT formats
    text_content_types = {
        "text/plain", "text/markdown", "text/csv", "text/yaml",
        "application/json", "application/yaml", "application/x-yaml",
    }
    text_extensions = {"txt", "md", "markdown", "csv", "log", "yaml", "yml", "json"}

    # HTML
    html_content_types = {"text/html"}
    html_extensions = {"html", "htm"}

    # DOCX
    docx_content_types = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }
    docx_extensions = {"docx"}

    # PDF
    pdf_content_types = {"application/pdf"}
    pdf_extensions = {"pdf"}

    # Image
    image_content_types = {
        "image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff",
    }
    image_extensions = {"jpg", "jpeg", "png", "webp", "bmp", "tiff"}

    # Detect type
    detected = None

    if content_type in text_content_types or ext in text_extensions:
        detected = "text"
    elif content_type in html_content_types or ext in html_extensions:
        detected = "html"
    elif content_type in docx_content_types or ext in docx_extensions:
        detected = "docx"
    elif content_type in pdf_content_types or ext in pdf_extensions or body[:4] == b"%PDF":
        detected = "pdf"
    elif content_type in image_content_types or ext in image_extensions:
        detected = "image"
    elif content_type.startswith("image/"):
        detected = "image"

    if detected is None:
        supported = sorted(
            text_extensions | html_extensions | docx_extensions
            | pdf_extensions | image_extensions
        )
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Content-Type: {content_type}, "
                   f"extension: .{ext}. Supported: {', '.join(supported)}",
        )

    page_count = None

    if detected == "text":
        text = body.decode("utf-8", errors="replace")
        source_type = "text"

    elif detected == "html":
        from html.parser import HTMLParser

        class _TagStripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self._text_parts = []

            def handle_data(self, data):
                self._text_parts.append(data)

            def get_text(self):
                return re.sub(r"\s+", " ", " ".join(self._text_parts)).strip()

        stripper = _TagStripper()
        stripper.feed(body.decode("utf-8", errors="replace"))
        text = stripper.get_text()
        source_type = "html"

    elif detected == "docx":
        try:
            from docx import Document

            doc = Document(io.BytesIO(body))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            source_type = "docx"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"DOCX parse error: {exc}")

    elif detected == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(body))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            page_count = len(reader.pages)
            source_type = "pdf"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"PDF parse error: {exc}")

    elif detected == "image":
        try:
            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(body))
            text = pytesseract.image_to_string(img)
            source_type = "image"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"OCR error: {exc}")

    return {
        "extracted_text": text,
        "source_type": source_type,
        "page_count": page_count,
        "char_count": len(text),
    }


# ------------------------------------------------------------------
# Session history route (Phase D — teacher dashboard data)
# ------------------------------------------------------------------


@router.get("/session-history")
async def session_history_route(
    request: Request, limit: int = 15
):
    """Session activity reconstructed from misconception log.

    Groups misconception_log entries by session_id to produce
    a timeline of tutoring sessions.

    Returns:
        {"sessions": [
            {
                "session_id": str,
                "concept_id": str,
                "started_at": str,
                "last_activity": str,
                "event_count": int,
                "answer_count": int,
                "curiosity_count": int,
                "chat_count": int
            }
        ]}
    """
    container = _get_container(request)
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return {"sessions": []}

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        cur = await conn.execute(
            "SELECT "
            "  session_id, "
            "  concept_id, "
            "  COUNT(*) as event_count, "
            "  MIN(created_at) as started_at, "
            "  MAX(created_at) as last_activity, "
            "  SUM(CASE WHEN intent_class = 'ANSWER' THEN 1 ELSE 0 END) as answer_count, "
            "  SUM(CASE WHEN intent_class IN ('QUESTION', 'TANGENT') THEN 1 ELSE 0 END) as curiosity_count, "
            "  SUM(CASE WHEN intent_class = 'CHAT' THEN 1 ELSE 0 END) as chat_count "
            "FROM aristotle_misconception_log "
            "WHERE session_id IS NOT NULL AND session_id != '' "
            "GROUP BY session_id "
            "ORDER BY last_activity DESC "
            "LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        await cur.close()

        sessions = [
            {
                "session_id": row[0],
                "concept_id": row[1],
                "event_count": row[2],
                "started_at": row[3],
                "last_activity": row[4],
                "answer_count": row[5] or 0,
                "curiosity_count": row[6] or 0,
                "chat_count": row[7] or 0,
            }
            for row in rows
        ]

        return {"sessions": sessions}
    except Exception:
        return {"sessions": []}


# ------------------------------------------------------------------
# Serialization helpers
# ------------------------------------------------------------------


def _session_to_dict(session: SessionContext) -> dict:
    """Serialize a SessionContext to a JSON-safe dict."""
    return {
        "student_id": session.student_id,
        "concept_id": session.concept_id,
        "state": session.state.value,
        "last_prediction": session.last_prediction,
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
        "predict_generated": session.predict_generated,
        "hint_count": session.hint_count,
        "hint_generated": session.hint_generated,
        "last_diagnosis": session.last_diagnosis,
        "last_question_type": session.last_question_type,
        "concept_queue": list(session.concept_queue),
        "cold_start_pending": list(session.cold_start_pending),
        "plan_id": session.plan_id,
    }


def _session_from_dict(d: dict) -> SessionContext:
    """Deserialize a SessionContext from a dict."""
    return SessionContext(
        student_id=d.get("student_id", "definer"),
        concept_id=d.get("concept_id", ""),
        state=SessionState(d.get("state", "PREDICT")),
        last_prediction=d.get("last_prediction", ""),
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
        predict_generated=d.get("predict_generated", False),
        hint_count=d.get("hint_count", 0),
        hint_generated=d.get("hint_generated", False),
        last_diagnosis=d.get("last_diagnosis", None),
        last_question_type=d.get("last_question_type", "recognition"),
        concept_queue=d.get("concept_queue", []),
        cold_start_pending=set(d.get("cold_start_pending", [])),
        plan_id=d.get("plan_id", ""),
    )
