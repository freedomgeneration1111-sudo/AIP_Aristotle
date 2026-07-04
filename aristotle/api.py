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
  POST /aristotle/intake/start    — start onboarding intake (ADR-002 §9)
  POST /aristotle/intake/step     — advance intake one turn
  POST /aristotle/placer/start    — start placement calibration (ADR-002 §9 stage 5)
  POST /aristotle/placer/step     — advance placement one turn

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

from fastapi import APIRouter, HTTPException, Request

from aip.foundation.protocols.actors import ActorContext
from aristotle.actors.intake import (
    IntakeSession,
    IntakeState,
    PlacerSession,
    _detect_intake_intent,
    _sample_concepts_for_placement,
    check_intake_triggers,
    intake_session_from_dict,
    intake_session_to_dict,
    placer_session_from_dict,
    placer_session_to_dict,
    run_intake_step,
    run_placer_step,
)
from aristotle.ingestor import ingest_concepts_from_yaml, list_concepts
from aristotle.session import (
    SessionContext,
    SessionState,
    run_session_step,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aristotle", tags=["aristotle"])


@router.get("/health")
async def health_route():
    """Extension health endpoint — direct check that ARISTOTLE's router is mounted.

    The platform's /health/extensions endpoint already surfaces extension state;
    this route is for direct curl checks during debugging.
    """
    return {"ok": True, "extension": "aristotle"}


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
    Returns: the initial SessionContext (state=PREDICT).
    """
    container = _get_container(request)
    body = await request.json()
    concept_id = body.get("concept_id", "")
    if not concept_id:
        raise HTTPException(status_code=400, detail="concept_id required")

    session = SessionContext(concept_id=concept_id, state=SessionState.PREDICT)
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
        "output": (
            result.error
            or (result.data.get("prompt", "") if isinstance(result.data, dict) else "")
            or ""
        ) if result.ok else "",
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

    session = SessionContext(concept_id=concept_id, state=SessionState.PREDICT)
    ctx = _make_ctx(container)
    answer_idx = 0
    steps: list[dict] = []
    max_steps = 20  # safety limit

    for _ in range(max_steps):
        if session.state.value == "SESSION_COMPLETE":
            break

        student_input = ""
        if (
            session.state == SessionState.QUIZ
            and session.quiz_generated
            and answer_idx < len(answers)
        ):
            student_input = answers[answer_idx]
            answer_idx += 1

        result = await run_session_step(ctx, session, student_input)
        steps.append(
            {
                "state": session.state.value,
                "output": result.error if result.ok else "",
                "ok": result.ok,
            }
        )

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

        mastery_by_concept.append(
            {
                "concept_id": concept_id,
                "topic": topic,
                "mastered": mastered,
                "last_score": last_score,
                "repetitions": repetitions,
                "next_review_at": next_review_at,
                "is_due": is_due,
                "updated_at": updated_at,
                "_sort_priority": sort_priority,
            }
        )

    # Sort: due first, then unstarted, then mastered, then not-due.
    # Within each priority, sort by next_review_at ascending (nulls first), then concept_id.
    mastery_by_concept.sort(
        key=lambda m: (
            m["_sort_priority"],
            m["next_review_at"] or "0000",
            m["concept_id"],
        )
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
# Intake routes (Phase D — ADR-002 §9 onboarding)
# ------------------------------------------------------------------


@router.post("/intake/start")
async def intake_start_route(request: Request):
    """Start an onboarding intake conversation (ADR-002 §9).

    Request body: {"plan_id": str | None}
      - plan_id None or absent → new learner; full intake from GREETING.
      - plan_id present → check_intake_triggers() decides whether to
        re-surface (full / checkin / partial) or skip intake entirely
        (returns trigger=None when the plan is healthy and mid-stream).

    Returns:
      {
        "trigger": "full" | "checkin" | "partial" | None,
        "prompt": str | None,           # first greeting/question
        "session": IntakeSession_dict | None,
      }

    When trigger is None the caller should proceed directly to
    /placer/start or /session/start — no intake needed.
    """
    container = _get_container(request)
    body = await request.json()
    plan_id = body.get("plan_id") or None
    ctx = _make_ctx(container)

    trigger = await check_intake_triggers(ctx, plan_id)

    if trigger is None:
        # No intake needed — plan exists and is healthy.
        return {"trigger": None, "prompt": None, "session": None}

    # Build a fresh IntakeSession at the trigger's entry_state.
    session = IntakeSession(
        state=trigger.entry_state,
        entry_state=trigger.entry_state,
        plan_id=plan_id or "",
    )

    # If the trigger carries a pre-built prompt (checkin / completed-plan
    # cases), surface it directly without dispatching run_intake_step —
    # the entry_state is GREETING and the prompt is the re-engagement
    # message, not the standard "what subject?" greeting.
    if trigger.prompt:
        return {
            "trigger": trigger.level,
            "prompt": trigger.prompt,
            "session": intake_session_to_dict(session),
        }

    # Phase 1 of the entry_state: generate the prompt via run_intake_step
    # with empty student_input. run_intake_step advances session.state to
    # the next state and returns the prompt for the current turn.
    result = await run_intake_step(session, "", ctx)
    return {
        "trigger": trigger.level,
        "prompt": result.get("prompt", ""),
        "session": intake_session_to_dict(session),
    }


@router.post("/intake/step")
async def intake_step_route(request: Request):
    """Advance an intake conversation one turn (ADR-002 §9).

    Request body: {
        "session": IntakeSession_dict,
        "student_input": str,
        "material_ids": [str, ...]  # optional — ids of newly uploaded
                                    # materials to attach to this turn
    }
      - student_input is the learner's free-form reply to the previous
        prompt. May be empty on the very first turn after intake_start
        if the caller wants to regenerate the prompt (rare).
      - material_ids (optional) — if the learner uploaded a file since
        the last turn, the GUI sends the material_id(s) here. The route
        appends them to session.material_ids so the IntakeActor includes
        their extracted text in its model context for this and future
        turns. Deduplicates against ids already in the session.

    Returns:
      {
        "state": str,                   # new IntakeState name
        "prompt": str | None,           # next prompt, or None at COMPLETE
        "pivot": IntakeTrigger_dict | None,  # set when intent detected
        "session": IntakeSession_dict,
        "plan_id": str | None,          # present when state=COMPLETE
        "concept_count": int | None,    # present when state=COMPLETE
      }

    Pivot detection: if the learner's student_input matches an intake
    intent keyword ("new topic", "exam", "schedule", etc.), the run
    surfaces the pivot so the caller can decide whether to branch the
    conversation. Pivots are advisory — the caller may ignore them and
    continue the normal flow.
    """
    container = _get_container(request)
    body = await request.json()
    session_dict = body.get("session", {})
    student_input = body.get("student_input", "")
    new_material_ids = body.get("material_ids", []) or []

    session = intake_session_from_dict(session_dict)
    ctx = _make_ctx(container)

    # Attach any newly uploaded materials to the session (dedup).
    for mid in new_material_ids:
        if mid and mid not in session.material_ids:
            session.material_ids.append(mid)

    # Detect mid-conversation intent pivots (advisory).
    pivot = _detect_intake_intent(student_input)

    result = await run_intake_step(session, student_input, ctx)

    # If the run reached GENERATING_PLAN it has just produced the plan.
    # run_intake_step returns {"state": "COMPLETE", "plan_id": ...,
    # "concept_count": N} in that case.
    response = {
        "state": result.get("state", session.state.value),
        "prompt": result.get("prompt"),
        "pivot": (
            {"level": pivot.level, "entry_state": pivot.entry_state.value}
            if pivot
            else None
        ),
        "session": intake_session_to_dict(session),
    }
    if result.get("state") == "COMPLETE":
        response["plan_id"] = result.get("plan_id", session.plan_id)
        response["concept_count"] = result.get("concept_count")
    return response


# ------------------------------------------------------------------
# Placer routes (Phase D — ADR-002 §9 stage 5 placement calibration)
# ------------------------------------------------------------------


@router.post("/placer/start")
async def placer_start_route(request: Request):
    """Start placement calibration for a learning plan (ADR-002 §9 stage 5).

    Request body: {"plan_id": str}
      - plan_id is the plan produced by a completed intake. The placer
        reads concept_ids_json from aristotle_learning_plan, samples a
        spread of concepts (beginning/middle/end via
        _sample_concepts_for_placement), and probes the learner on each
        to calibrate where to start tutoring.

    Returns:
      {
        "state": "PROBING",
        "question": str,                # first probe question
        "concepts_placed": 0,
        "session": PlacerSession_dict,
      }

    The learner never sees the placement labels (CONFIRMED / SHAKY /
    ABSENT / UNEXPECTED_STRENGTH) — they experience it as Aristotle
    getting to know them.
    """
    container = _get_container(request)
    body = await request.json()
    plan_id = body.get("plan_id", "")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id required")

    ctx = _make_ctx(container)
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="corpus_registry not available")

    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn

    # Read the concept_ids_json from the plan row.
    cur = await conn.execute(
        "SELECT concept_ids_json FROM aristotle_learning_plan WHERE id = ?",
        (plan_id,),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id!r} not found")

    import json as _json

    try:
        concept_ids = _json.loads(row[0]) if row[0] else []
    except (ValueError, TypeError):
        concept_ids = []

    if not concept_ids:
        raise HTTPException(
            status_code=409,
            detail=f"plan {plan_id!r} has no concepts to place",
        )

    sampled = _sample_concepts_for_placement(concept_ids)
    session = PlacerSession(
        plan_id=plan_id,
        concepts_to_assess=sampled,
    )

    # Phase 1: generate the first probe question.
    result = await run_placer_step(session, "", ctx)
    return {
        "state": result.get("state", "PROBING"),
        "question": result.get("question", ""),
        "concepts_placed": result.get("concepts_placed", 0),
        "session": placer_session_to_dict(session),
    }


@router.post("/placer/step")
async def placer_step_route(request: Request):
    """Advance a placement calibration one turn (ADR-002 §9 stage 5).

    Request body: {"session": PlacerSession_dict, "student_input": str}
      - student_input is the learner's answer to the current probe
        question. Empty string is invalid here — the placer always
        expects an answer (use /placer/start to get the first question
        without sending an answer).

    Returns:
      {
        "state": "PROBING" | "COMPLETE",
        "question": str | None,         # next probe question, or None
        "concepts_placed": int,
        "concepts_known": int | None,   # only at COMPLETE
        "session": PlacerSession_dict,
      }
    """
    container = _get_container(request)
    body = await request.json()
    session_dict = body.get("session", {})
    student_input = body.get("student_input", "")

    session = placer_session_from_dict(session_dict)
    ctx = _make_ctx(container)

    result = await run_placer_step(session, student_input, ctx)

    response = {
        "state": result.get("state", session.state),
        "question": result.get("question"),
        "concepts_placed": result.get("concepts_placed", len(session.results)),
        "session": placer_session_to_dict(session),
    }
    if result.get("state") == "COMPLETE":
        response["concepts_known"] = result.get("concepts_known")
    return response


# ------------------------------------------------------------------
# Upload route (Phase D — ADR-002 §9 stage 3 material inventory)
# ------------------------------------------------------------------


# Content-Type → source_type mapping for supported file types.
# Anything not in this map (or application/octet-stream without magic
# bytes) returns 415 Unsupported Media Type.
_UPLOAD_CT_MAP = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/bmp": "image",
    "image/tiff": "image",
    "text/plain": "text",
    "text/markdown": "text",
    "text/csv": "text",
    "text/html": "html",
    "application/json": "text",
    "application/x-yaml": "text",
}


def _extract_pdf_text(content: bytes) -> tuple[str, int]:
    """Extract text from a PDF via pypdf. Returns (text, page_count)."""
    import io
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(pages), len(reader.pages)


def _extract_image_text(content: bytes) -> str:
    """Extract text from an image via pytesseract OCR."""
    import io
    from PIL import Image
    import pytesseract

    img = Image.open(io.BytesIO(content))
    return pytesseract.image_to_string(img)


def _extract_html_text(content: bytes) -> str:
    """Extract text from HTML by stripping tags."""
    import re

    text = content.decode("utf-8", errors="replace")
    # Remove script/style blocks first (so their content doesn't leak)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_text(content: bytes) -> str:
    """Decode raw text bytes (txt/md/csv/json/yaml)."""
    return content.decode("utf-8", errors="replace")


@router.post("/upload")
async def upload_route(request: Request):
    """Upload a file for text extraction + persistence (ADR-002 §9 stage 3).

    Receives the raw file body + Content-Type header + Content-Disposition
    header (for filename). Extracts text using:
      - PDF → pypdf (returns page_count)
      - Image → pytesseract OCR
      - HTML → tag stripping
      - txt/md/csv/json/yaml → UTF-8 decode

    Then persists the extracted text to aristotle_uploaded_material
    (M007 schema) so the INTAKE actor can reference it during the
    LLM-driven intake conversation and the plan generator can derive
    concepts from the actual content. Returns the material_id so the
    GUI can attach it to subsequent /intake/step calls.

    Returns:
      {
        "material_id": str,            # row id in aristotle_uploaded_material
        "extracted_text": str,         # full extracted text (also stored in DB)
        "source_type": "pdf" | "image" | "text" | "html",
        "char_count": int,
        "page_count": int | None,      # PDF only
        "filename": str,
      }

    Returns 415 if the Content-Type is unsupported.
    """
    container = _get_container(request)
    content = await request.body()
    content_type = (request.headers.get("content-type") or "").lower().split(";")[0].strip()

    # Parse filename from Content-Disposition header (fallback: "upload").
    disposition = request.headers.get("content-disposition") or ""
    filename = "upload"
    if "filename=" in disposition:
        # Handles both filename="name.pdf" and filename=name.pdf
        import re as _re

        m = _re.search(r'filename="?([^";]+)"?', disposition)
        if m:
            filename = m.group(1)

    source_type = _UPLOAD_CT_MAP.get(content_type)
    if source_type is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {content_type}",
        )

    if not content:
        raise HTTPException(status_code=400, detail="Empty upload body")

    try:
        if source_type == "pdf":
            extracted, page_count = _extract_pdf_text(content)
        elif source_type == "image":
            extracted = _extract_image_text(content)
            page_count = None
        elif source_type == "html":
            extracted = _extract_html_text(content)
            page_count = None
        else:  # text
            extracted = _extract_text(content)
            page_count = None

        char_count = len(extracted)

        # Persist to aristotle_uploaded_material so INTAKE + plan generator
        # can read it back via material_id.
        material_id = str(__import__("uuid").uuid4())
        ingest_job_id = ""
        registry = getattr(container, "corpus_registry", None)
        if registry is not None:
            try:
                stores = await registry.get_stores("aristotle:textbook")
                conn = stores.connection_manager.write_conn
                # NOTE: column order is (id, student_id, filename, ...).
                # The id is the per-upload UUID (material_id); student_id
                # is the constant "definer" (single-tenant pre-alpha).
                # The values MUST match the column order — a previous
                # version had them swapped, which caused the second upload
                # to fail with UNIQUE constraint violation on id="definer".
                await conn.execute(
                    "INSERT INTO aristotle_uploaded_material "
                    "(id, student_id, filename, source_type, extracted_text, "
                    "char_count, page_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (material_id, "definer", filename, source_type, extracted,
                     char_count, page_count),
                )
                await conn.commit()
                logger.info(
                    "upload_stored material_id=%s filename=%s source=%s chars=%d",
                    material_id, filename, source_type, char_count,
                )

                # ADR-003: kick off background ingestion job (chunk + embed + analyze)
                # The job runs as a supervised background task. The GUI polls
                # GET /aristotle/ingest/{job_id}/status for progress.
                if char_count >= 100:
                    try:
                        from aristotle.ingestion.paper_ingestor import (
                            create_ingest_job, ingest_paper,
                        )
                        from aip.adapter.extensions.supervision import supervised_task

                        ingest_job_id = await create_ingest_job(
                            container, material_id, filename,
                        )
                        # Start the background job — supervised_task handles
                        # exception logging + cancellation on shutdown.
                        task = supervised_task(
                            f"aristotle:ingest:{ingest_job_id}",
                            ingest_paper(material_id, filename, extracted, container, ingest_job_id),
                        )
                        # Store the task on the container so shutdown cancels it.
                        if not hasattr(container, "_aristotle_ingest_tasks"):
                            container._aristotle_ingest_tasks = {}
                        container._aristotle_ingest_tasks[ingest_job_id] = task
                        logger.info(
                            "ingest_job_started job_id=%s material_id=%s",
                            ingest_job_id, material_id,
                        )
                    except Exception as ingest_exc:
                        logger.warning(
                            "ingest_job_start_failed material_id=%s error=%s:%s — "
                            "fallback to legacy truncation path",
                            material_id, type(ingest_exc).__name__, ingest_exc,
                        )
                else:
                    logger.info(
                        "ingest_job_skipped material_id=%s chars=%d — too short for chunking",
                        material_id, char_count,
                    )
            except Exception as db_exc:
                # DB persistence is best-effort — don't fail the upload if
                # the DB is unavailable. The extracted text is still
                # returned to the caller, just not stored for INTAKE to
                # reference. Log the failure so it's visible.
                logger.warning(
                    "upload_persist_failed material_id=%s error=%s:%s",
                    material_id, type(db_exc).__name__, db_exc,
                )
                material_id = ""

        return {
            "material_id": material_id,
            "extracted_text": extracted,
            "source_type": source_type,
            "char_count": char_count,
            "page_count": page_count,
            "filename": filename,
            "ingest_job_id": ingest_job_id,
            "ingest_status": "PENDING",
        }
    except HTTPException:
        raise
    except ModuleNotFoundError as exc:
        # Missing library (pypdf, pytesseract, PIL) — give the user a clear
        # fix instruction instead of a generic "Text extraction failed".
        missing_mod = str(exc).replace("No module named ", "").strip("'\"")
        logger.error("upload_missing_library ct=%s missing=%s", content_type, missing_mod)
        install_map = {
            "pypdf": "pypdf",
            "pytesseract": "pytesseract",
            "PIL": "Pillow",
        }
        install_name = install_map.get(missing_mod, missing_mod)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Upload failed — required library '{missing_mod}' is not installed. "
                f"Fix: cd ~/AIP_Aristotle && pip install {install_name}  "
                f"(or: uv pip install {install_name}).  "
                f"For image OCR, also install the system tesseract: "
                f"sudo apt install tesseract-ocr"
            ),
        )
    except Exception as exc:
        logger.warning("upload_extraction_failed ct=%s error=%s:%s", content_type, type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"Text extraction failed: {exc}")


# ------------------------------------------------------------------
# Ingestion job routes (ADR-003 — background pipeline)
# ------------------------------------------------------------------


@router.get("/ingest/{job_id}/status")
async def ingest_status_route(request: Request, job_id: str):
    """Get the status of a background paper ingestion job.

    Returns:
      {
        "job_id": str,
        "material_id": str,
        "filename": str,
        "phase": "PENDING"|"PARSING"|"CHUNKING"|"EMBEDDING"|"INDEXING"|"ANALYZING"|"COMPLETE"|"FAILED",
        "status": "PENDING"|"RUNNING"|"COMPLETE"|"FAILED",
        "chunks_total": int,
        "chunks_done": int,
        "analysis_complete": bool,
        "error": str | None,
        "started_at": str,
        "updated_at": str,
        "completed_at": str | None,
      }

    The GUI polls this every 2-3 seconds to render a progress indicator.
    """
    container = _get_container(request)
    from aristotle.ingestion.paper_ingestor import get_job_status

    status = await get_job_status(container, job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return status


@router.get("/material/{material_id}/structure")
async def material_structure_route(request: Request, material_id: str):
    """Get the structural metadata for an ingested paper.

    Returns the TOC, concept tags, prerequisite tags, and citation IDs
    for each chunk. Used by the GUI to render a "curriculum map" view
    and by the IntakeActor to build the structural map shown to the LLM.
    """
    container = _get_container(request)
    from aristotle.ingestion.paper_ingestor import get_material_structure, get_structural_map

    structure = await get_material_structure(container, material_id)
    structural_map = await get_structural_map(container, material_id)
    return {
        "material_id": material_id,
        "chunks": structure,
        "toc": structural_map.get("toc", []),
        "concepts": structural_map.get("concepts", []),
        "citations": structural_map.get("citations", []),
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
        "predict_generated": session.predict_generated,
    }


def _session_from_dict(d: dict) -> SessionContext:
    """Deserialize a SessionContext from a dict."""
    return SessionContext(
        student_id=d.get("student_id", "definer"),
        concept_id=d.get("concept_id", ""),
        state=SessionState(d.get("state", "PREDICT")),
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
    )
