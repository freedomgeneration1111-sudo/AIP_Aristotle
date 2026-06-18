"""Aristotle session coordinator — drives the tutoring loop (ADR-001 §3).

The session coordinator orchestrates the five modes (SOCRATES, EXAMINER,
MENTOR) through the TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine.
It's the thing that makes ARISTOTLE a tutor, not just a collection of actors.

The coordinator is called by:
  - An API route (when the learner sends a message)
  - A CLI command (for testing)
  - A GUI callback (when the GUI lands, platform v1.1)

It does NOT run on a timer — the tutoring state machine is driven by user
turns (ADR-001 §3: "the learner only feels rhythm").

Phase A: single-step sessions. Each call to `run_session_step()` advances
the state machine one step. The caller (API/CLI/GUI) stores the session
state between steps. A future full-session coordinator will manage the
complete loop in one call.

Layer: imports from aip.foundation.protocols.actors only (ActorContext).
Accesses the actors via the container (ctx.container.extensions.registry).
No aip.adapter or aip.orchestration imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class SessionState(str, Enum):
    """The tutoring state machine states (ADR-001 §3)."""
    TEACH = "TEACH"
    PROBE = "PROBE"
    QUIZ = "QUIZ"
    EVALUATE = "EVALUATE"
    REMEDIATE = "REMEDIATE"
    NEXT_CONCEPT = "NEXT_CONCEPT"
    SESSION_COMPLETE = "SESSION_COMPLETE"


@dataclass
class SessionContext:
    """Per-session state (passed between steps).

    The caller persists this between `run_session_step()` calls.
    """
    student_id: str = "definer"
    concept_id: str = ""
    state: SessionState = SessionState.TEACH
    # Accumulated results from each step
    last_explanation: str = ""
    last_probe_question: str = ""
    last_quiz_question: str = ""
    last_student_answer: str = ""
    last_evaluation: str = ""  # JSON from EXAMINER.evaluate()
    last_score: float = 0.0
    mastered: bool = False
    # Track retries (prevent infinite REMEDIATE loop)
    retry_count: int = 0
    max_retries: int = 2  # ADR-001 §3: different framing on retry, max 2


async def run_session_step(
    ctx: ActorContext,
    session: SessionContext,
    student_input: str = "",
) -> ActorResult:
    """Advance the tutoring state machine one step.

    Args:
        ctx: ActorContext with container + config + logger.
        session: the per-session state (mutated in-place).
        student_input: the learner's response (for PROBE/QUIZ steps).
            Empty for TEACH (the tutor explains; no input needed).

    Returns:
        ActorResult with:
        - ok=True: step completed. `error` field contains the step output
          (explanation, question, evaluation, etc.).
        - ok=False: step failed. `error` field contains the error message.

    The caller checks session.state after each step to know what to do next:
    - TEACH → display the explanation, wait for learner to continue
    - PROBE → display the probe question, wait for learner's response
    - QUIZ → display the quiz question, wait for learner's answer
    - EVALUATE → display the evaluation, session.state advances automatically
    - REMEDIATE → display the re-teaching, session.state advances to PROBE
    - NEXT_CONCEPT → session complete for this concept
    - SESSION_COMPLETE → no more concepts
    """
    logger = ctx.logger
    container: Any = ctx.container

    # Get the actors from the extension registry
    host = getattr(container, "extensions", None)
    if host is None:
        return ActorResult(ok=False, error="extension host not available")

    # Access the actors via the host's registry
    # The actors were registered by hooks.py::on_load
    registry = host.registry if hasattr(host, "registry") else None
    if registry is None:
        return ActorResult(ok=False, error="extension registry not available")

    # Get the actor instances. The actors are classes registered via
    # host.register_actor; we need to instantiate them or access a cached
    # instance. For Phase A, the actors are stateless (they take ctx per
    # call), so we can construct them directly.
    # This is a known coupling: the coordinator reaches into the extension's
    # actor classes. A future revision would have the host expose actor
    # instances via a lookup API.
    try:
        from aristotle.actors import ExaminerActor, MentorActor, SocratesActor
    except ImportError:
        return ActorResult(ok=False, error="aristotle.actors not importable")

    socrates = SocratesActor()
    examiner = ExaminerActor()
    mentor = MentorActor()

    # Dispatch based on session state
    if session.state == SessionState.TEACH:
        return await _step_teach(ctx, session, socrates)
    elif session.state == SessionState.PROBE:
        return await _step_probe(ctx, session, examiner)
    elif session.state == SessionState.QUIZ:
        return await _step_quiz(ctx, session, examiner, student_input)
    elif session.state == SessionState.EVALUATE:
        return await _step_evaluate(ctx, session, examiner, mentor)
    elif session.state == SessionState.REMEDIATE:
        return await _step_remediate(ctx, session, socrates, mentor)
    elif session.state == SessionState.NEXT_CONCEPT:
        return await _step_next_concept(ctx, session)
    else:
        return ActorResult(ok=False, error="session already complete")


async def _step_teach(
    ctx: ActorContext,
    session: SessionContext,
    socrates: Any,
) -> ActorResult:
    """TEACH: SOCRATES explains the concept (ADR-001 §3)."""
    logger = ctx.logger
    result = await socrates.teach(ctx, session.concept_id)

    if result.ok:
        session.last_explanation = result.error or ""
        session.state = SessionState.PROBE
        logger.info("session_step_teach concept=%s", session.concept_id)
    return result


async def _step_probe(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
) -> ActorResult:
    """PROBE: EXAMINER asks a low-stakes question (ADR-001 §3)."""
    logger = ctx.logger
    result = await examiner.probe(ctx, session.concept_id)

    if result.ok:
        session.last_probe_question = result.error or ""
        session.state = SessionState.QUIZ
        logger.info("session_step_probe concept=%s", session.concept_id)
    return result


async def _step_quiz(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
    student_input: str,
) -> ActorResult:
    """QUIZ: EXAMINER asks a real question (ADR-001 §3).

    If student_input is provided (the learner's probe response), it's
    recorded as the probe answer. The quiz question is generated.
    """
    logger = ctx.logger

    # If the learner provided a probe response, record it
    if student_input:
        session.last_student_answer = student_input

    result = await examiner.quiz(ctx, session.concept_id)

    if result.ok:
        session.last_quiz_question = result.error or ""
        # Wait for the learner's quiz answer — the caller will call
        # run_session_step again with student_input = the quiz answer.
        # For Phase A single-step, we advance to EVALUATE immediately
        # (the caller passes the quiz answer as student_input).
        if student_input and session.last_student_answer:
            session.state = SessionState.EVALUATE
        logger.info("session_step_quiz concept=%s", session.concept_id)
    return result


async def _step_evaluate(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
    mentor: Any,
) -> ActorResult:
    """EVALUATE: EXAMINER scores + MENTOR updates struggle_pattern (ADR-001 §3)."""
    logger = ctx.logger

    # EXAMINER scores the quiz answer
    eval_result = await examiner.evaluate(
        ctx, session.concept_id,
        student_answer=session.last_student_answer,
        quiz_question=session.last_quiz_question,
    )

    if not eval_result.ok:
        return eval_result

    session.last_evaluation = eval_result.error or ""

    # Parse the score from the evaluation JSON
    # The model returns JSON with {score, mastery_achieved, feedback}
    import json
    try:
        eval_data = json.loads(session.last_evaluation)
        session.last_score = float(eval_data.get("score", 0.0))
        session.mastered = bool(eval_data.get("mastery_achieved", False))
    except (json.JSONDecodeError, ValueError, TypeError):
        # If the model didn't return valid JSON, default to not mastered
        session.last_score = 0.0
        session.mastered = False
        logger.warning(
            "session_evaluate_parse_failed concept=%s raw=%s",
            session.concept_id, session.last_evaluation[:200],
        )

    # MENTOR updates the struggle_pattern
    mentor_result = await mentor.update_struggle_pattern(
        ctx, session.concept_id, session.last_evaluation,
    )
    # MENTOR failure is non-fatal — the evaluation still counts
    if not mentor_result.ok:
        logger.warning(
            "session_mentor_update_failed concept=%s error=%s",
            session.concept_id, mentor_result.error,
        )

    # Update SM-2 + mastery state
    await _update_mastery(ctx, session)

    # Branch: mastered → next concept; struggling → remediate
    config = ctx.config
    mastery_threshold = getattr(config, "mastery_threshold", 0.7) if config else 0.7
    if session.last_score >= mastery_threshold:
        session.state = SessionState.NEXT_CONCEPT
    else:
        if session.retry_count < session.max_retries:
            session.state = SessionState.REMEDIATE
            session.retry_count += 1
        else:
            # Max retries — move on (the learner needs a human teacher)
            session.state = SessionState.NEXT_CONCEPT

    logger.info(
        "session_step_evaluate concept=%s score=%.2f mastered=%s state=%s",
        session.concept_id, session.last_score, session.mastered, session.state.value,
    )
    return ActorResult(ok=True, error=session.last_evaluation)


async def _step_remediate(
    ctx: ActorContext,
    session: SessionContext,
    socrates: Any,
    mentor: Any,
) -> ActorResult:
    """REMEDIATE: SOCRATES re-teaches with different framing (ADR-001 §3)."""
    logger = ctx.logger

    # Get the struggle_pattern to inform the re-teaching
    struggle_pattern = await mentor.get_struggle_pattern(ctx, session.student_id)

    result = await socrates.teach(
        ctx, session.concept_id,
        retry=True,
        struggle_pattern=struggle_pattern,
    )

    if result.ok:
        session.last_explanation = result.error or ""
        # After remediation, re-probe (ADR-001 §3: re-probe after remediation)
        session.state = SessionState.PROBE
        logger.info(
            "session_step_remediate concept=%s retry=%d",
            session.concept_id, session.retry_count,
        )
    return result


async def _step_next_concept(
    ctx: ActorContext,
    session: SessionContext,
) -> ActorResult:
    """NEXT_CONCEPT: concept mastered (or max retries), advance (ADR-001 §3)."""
    logger = ctx.logger

    # For Phase A single-concept sessions, the session is complete.
    # A future full-session coordinator would consult the prerequisite DAG
    # for the next concept whose foundations are met.
    session.state = SessionState.SESSION_COMPLETE
    logger.info(
        "session_step_next_concept concept=%s mastered=%s — session complete",
        session.concept_id, session.mastered,
    )
    return ActorResult(
        ok=True,
        error=f"Concept {session.concept_id} session complete. Mastered: {session.mastered}",
    )


async def _update_mastery(ctx: ActorContext, session: SessionContext) -> None:
    """Update the aristotle_mastery table with SM-2 state + score."""
    from aristotle.sm2 import SM2State, update_sm2

    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # Read current SM-2 state
        cur = await conn.execute(
            "SELECT easiness_factor, interval_days, repetitions, next_review_at "
            "FROM aristotle_mastery WHERE student_id = ? AND concept_id = ?",
            (session.student_id, session.concept_id),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is not None:
            current_state = SM2State(
                easiness_factor=row[0],
                interval_days=row[1],
                repetitions=row[2],
                next_review_at=row[3],
            )
        else:
            current_state = SM2State()

        # Update SM-2 state
        new_state = update_sm2(current_state, session.last_score)
        mastered = 1 if session.mastered else 0
        now = datetime.now(timezone.utc).isoformat()

        await conn.execute(
            """
            INSERT OR REPLACE INTO aristotle_mastery
            (student_id, concept_id, easiness_factor, interval_days,
             repetitions, next_review_at, last_score, mastered, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.student_id, session.concept_id,
                new_state.easiness_factor, new_state.interval_days,
                new_state.repetitions, new_state.next_review_at,
                session.last_score, mastered, now,
            ),
        )
        await conn.commit()
    except Exception:
        pass  # non-fatal — mastery tracking is best-effort
