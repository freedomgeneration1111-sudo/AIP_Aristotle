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
    """The tutoring state machine states (ADR-001 §3 + ADR-002 Rev 2 §3).

    Phase B.5 adds PREDICT (initial state — generation effect) + HINT_1/HINT_2
    (2-rung hint ladder between EVALUATE and REMEDIATE). The full Phase B.5
    flow is:
      PREDICT → TEACH → PROBE → QUIZ → EVALUATE →
        if correct: NEXT_CONCEPT
        if wrong, hint_count == 0: HINT_1
        if wrong, hint_count == 1: HINT_2
        if wrong, hint_count >= 2: REMEDIATE
      HINT_1/HINT_2 are two-phase: phase 1 generates the hint, phase 2
      (with student_input) re-evaluates. Correct after a hint →
      hint_assisted_correct++ on aristotle_mastery, then NEXT_CONCEPT.
      Still wrong after HINT_2 → REMEDIATE.
    """
    PREDICT = "PREDICT"
    TEACH = "TEACH"
    PROBE = "PROBE"
    QUIZ = "QUIZ"
    EVALUATE = "EVALUATE"
    HINT_1 = "HINT_1"
    HINT_2 = "HINT_2"
    REMEDIATE = "REMEDIATE"
    NEXT_CONCEPT = "NEXT_CONCEPT"
    SESSION_COMPLETE = "SESSION_COMPLETE"


@dataclass
class SessionContext:
    """Per-session state (passed between steps).

    The caller persists this between `run_session_step()` calls.

    Phase B.5: the default initial state is now PREDICT (was TEACH).
    The PREDICT step asks the learner to guess before teaching — the
    generation effect. `last_prediction` records the learner's
    prediction text so it can be logged to aristotle_predict_event.

    Phase B.5 (HINT ladder): `hint_count` tracks how many hints have
    been given (0, 1, or 2). `hint_generated` tracks whether the
    current HINT_1/HINT_2 step has generated its hint (phase 1 done,
    waiting for learner's re-answer in phase 2).
    """
    student_id: str = "definer"
    concept_id: str = ""
    # Phase B.5: default initial state is PREDICT (was TEACH).
    state: SessionState = SessionState.PREDICT
    # Accumulated results from each step
    last_prediction: str = ""  # Phase B.5: learner's pre-TEACH prediction
    last_explanation: str = ""
    last_probe_question: str = ""
    last_quiz_question: str = ""
    last_student_answer: str = ""
    last_evaluation: str = ""  # JSON from EXAMINER.evaluate() (legacy — kept for backward compat)
    last_score: float = 0.0
    mastered: bool = False
    # Phase B.5 (error diagnosis): the structured diagnosis dict from
    # EXAMINER.evaluate() when the answer is wrong. None when correct or
    # when the model didn't return a diagnosis. Read by MENTOR's
    # log_misconception() in _step_evaluate.
    last_diagnosis: dict | None = None
    # Track retries (prevent infinite REMEDIATE loop)
    retry_count: int = 0
    max_retries: int = 2  # ADR-001 §3: different framing on retry, max 2
    # Track whether the quiz question has been generated (waiting for answer)
    quiz_generated: bool = False
    # Track whether the probe question has been generated (waiting for response)
    probe_generated: bool = False
    # Phase B.5: track whether the predict prompt has been generated
    # (waiting for learner's prediction response)
    predict_generated: bool = False
    # Phase B.5 (HINT ladder): number of hints given so far (0, 1, or 2).
    # Checked at EVALUATE branch: 0 → HINT_1, 1 → HINT_2, >=2 → REMEDIATE.
    hint_count: int = 0
    # Phase B.5 (HINT ladder): whether the current HINT_1/HINT_2 step has
    # generated its hint (phase 1 done, waiting for learner's re-answer).
    hint_generated: bool = False


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
    - PREDICT → display the predict prompt, wait for learner's prediction
      (Phase B.5: the generation effect — learner guesses before teaching)
    - TEACH → display the explanation, wait for learner to continue
    - PROBE → display the probe question, wait for learner's response
    - QUIZ → display the quiz question, wait for learner's answer
    - EVALUATE → display the evaluation, session.state advances automatically
      (Phase B.5 HINT ladder: on a failed quiz, routes to HINT_1, HINT_2,
      or REMEDIATE based on hint_count — see _step_evaluate's branch below)
    - HINT_1 → display the first hint (gentle nudge), wait for learner's
      re-answer. Re-evaluates; correct → NEXT_CONCEPT, still wrong → HINT_2.
    - HINT_2 → display the second hint (stronger clue), wait for learner's
      re-answer. Re-evaluates; correct → NEXT_CONCEPT, still wrong → REMEDIATE.
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
    if session.state == SessionState.PREDICT:
        return await _step_predict(ctx, session, socrates, student_input)
    elif session.state == SessionState.TEACH:
        return await _step_teach(ctx, session, socrates)
    elif session.state == SessionState.PROBE:
        return await _step_probe(ctx, session, examiner)
    elif session.state == SessionState.QUIZ:
        return await _step_quiz(ctx, session, examiner, student_input)
    elif session.state == SessionState.EVALUATE:
        return await _step_evaluate(ctx, session, examiner, mentor)
    elif session.state == SessionState.HINT_1:
        return await _step_hint(ctx, session, examiner, mentor, student_input, hint_rung=1)
    elif session.state == SessionState.HINT_2:
        return await _step_hint(ctx, session, examiner, mentor, student_input, hint_rung=2)
    elif session.state == SessionState.REMEDIATE:
        return await _step_remediate(ctx, session, socrates, mentor)
    elif session.state == SessionState.NEXT_CONCEPT:
        return await _step_next_concept(ctx, session)
    else:
        return ActorResult(ok=False, error="session already complete")


async def _step_predict(
    ctx: ActorContext,
    session: SessionContext,
    socrates: Any,
    student_input: str,
) -> ActorResult:
    """PREDICT: SOCRATES asks the learner to guess before teaching (ADR-002 Rev 2 §3).

    Two-phase (like QUIZ):
      Phase 1 (predict_generated=False): call socrates.predict() to get the
        warm prompt ("Before I explain this, what do you think [concept]
        means?..."). Display the prompt, wait for the learner's prediction.
      Phase 2 (predict_generated=True, student_input non-empty): record the
        learner's prediction to aristotle_predict_event, advance to TEACH.

    The prediction is ALWAYS accepted — never scored. The generation
    effect works regardless of whether the prediction was right or wrong.
    We log it for analysis (which concepts students guess well vs. poorly),
    not for the mastery model.

    ADR-002 §10.4: aristotle_predict_event has no correctness column by
    design. The ADR's `finding` column (set by PLACER in Phase D) is also
    omitted — Phase B.5 only records the raw prediction.
    """
    logger = ctx.logger

    if not session.predict_generated:
        # Phase 1: generate the predict prompt.
        result = await socrates.predict(ctx, session.concept_id)
        if result.ok:
            # The prompt is in result.data["prompt"] (Phase B.5: use the new
            # data field, not error-as-payload). Fall back to error for
            # backward compat with any actor that hasn't migrated yet.
            if result.data is not None and isinstance(result.data, dict):
                prompt = result.data.get("prompt", "")
            else:
                prompt = result.error or ""
            session.predict_generated = True
            # If student_input was provided (non-interactive mode), record
            # the prediction immediately + advance to TEACH in the same step.
            if student_input:
                session.last_prediction = student_input
                await _log_predict_event(ctx, session)
                session.state = SessionState.TEACH
                logger.info(
                    "session_step_predict_generate_and_record concept=%s",
                    session.concept_id,
                )
            else:
                logger.info(
                    "session_step_predict_generate concept=%s",
                    session.concept_id,
                )
        return result
    else:
        # Phase 2: learner's prediction arrived — record it + advance to TEACH.
        if student_input:
            session.last_prediction = student_input
        await _log_predict_event(ctx, session)
        session.state = SessionState.TEACH
        logger.info(
            "session_step_predict_record concept=%s prediction_len=%d",
            session.concept_id, len(session.last_prediction),
        )
        return ActorResult(
            ok=True,
            data={"prediction_recorded": True, "concept_id": session.concept_id},
        )


async def _log_predict_event(ctx: ActorContext, session: SessionContext) -> None:
    """Write the learner's prediction to aristotle_predict_event (ADR-002 §10.4).

    Best-effort: a DB failure here is non-fatal. The session still advances
    to TEACH — the prediction is logged for analysis, not for the tutoring
    loop's control flow.
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        # session_id: use a stable identifier. For Phase B.5 pre-alpha
        # (single-tenant, no session table yet), derive from student_id +
        # concept_id + a timestamp so multiple sessions per concept are
        # distinguishable. A real session_id comes in Phase D with the
        # aristotle_intake_session table.
        from datetime import datetime, timezone
        session_id = f"{session.student_id}:{session.concept_id}:{datetime.now(timezone.utc).isoformat()}"
        await conn.execute(
            "INSERT INTO aristotle_predict_event (session_id, concept_id, prediction_text) "
            "VALUES (?, ?, ?)",
            (session_id, session.concept_id, session.last_prediction),
        )
        await conn.commit()
    except Exception as exc:
        # Non-fatal — log + continue. The prediction is for analysis, not
        # for the tutoring loop's control flow.
        ctx.logger.warning(
            "session_predict_log_failed concept=%s error=%s:%s",
            session.concept_id, type(exc).__name__, exc,
        )


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
    """PROBE: EXAMINER asks a low-stakes question (ADR-001 §3).

    Two-phase: first call generates the probe question (no input needed),
    second call (with student_input) records the answer and advances to QUIZ.
    """
    logger = ctx.logger

    # If the probe question was already generated, record the student's answer
    if session.probe_generated and session.student_input if hasattr(session, 'student_input') else False:
        pass  # This path is handled by the caller passing student_input to the next step

    result = await examiner.probe(ctx, session.concept_id)

    if result.ok:
        session.last_probe_question = result.error or ""
        session.probe_generated = True
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

    Two-phase: first call generates the quiz question (no input needed),
    second call (with student_input) records the answer and advances to EVALUATE.
    """
    logger = ctx.logger

    if not session.quiz_generated:
        # Phase 1: generate the quiz question
        result = await examiner.quiz(ctx, session.concept_id)
        if result.ok:
            session.last_quiz_question = result.error or ""
            session.quiz_generated = True
            # If student_input was provided (non-interactive mode), record it
            # and advance to EVALUATE immediately
            if student_input:
                session.last_student_answer = student_input
                session.state = SessionState.EVALUATE
            logger.info("session_step_quiz_generate concept=%s", session.concept_id)
        return result
    else:
        # Phase 2: student's answer arrived — advance to EVALUATE
        if student_input:
            session.last_student_answer = student_input
        session.state = SessionState.EVALUATE
        logger.info("session_step_quiz_answer concept=%s", session.concept_id)
        return ActorResult(ok=True, error=session.last_quiz_question)


async def _step_evaluate(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
    mentor: Any,
) -> ActorResult:
    """EVALUATE: EXAMINER scores + MENTOR updates struggle_pattern (ADR-001 §3).

    Phase B.5 (error diagnosis): EXAMINER.evaluate() now returns a structured
    dict via ActorResult.data (not error-as-payload). The dict has
    {score, mastery_achieved, feedback, diagnosis}. When the answer is wrong,
    diagnosis is a dict with {misconception, why_wrong, corrective}; when
    correct, diagnosis is None. The diagnosis is stored on
    session.last_diagnosis for MENTOR's log_misconception() call (TASK 2).
    """
    logger = ctx.logger

    # EXAMINER scores the quiz answer
    eval_result = await examiner.evaluate(
        ctx, session.concept_id,
        student_answer=session.last_student_answer,
        quiz_question=session.last_quiz_question,
    )

    if not eval_result.ok:
        return eval_result

    # Phase B.5: read from result.data (the new structured return channel).
    # Fallback to result.error + JSON parse for backward compat with any
    # actor that hasn't migrated to data= yet.
    if eval_result.data is not None and isinstance(eval_result.data, dict):
        eval_data = eval_result.data
        # Keep last_evaluation as a JSON string for backward compat
        # (MENTOR.update_struggle_pattern still reads it, + the API
        # serialization includes it for old clients).
        import json
        session.last_evaluation = json.dumps(eval_data)
    else:
        # Legacy path: actor returned error-as-payload (a JSON string).
        session.last_evaluation = eval_result.error or ""
        import json
        try:
            eval_data = json.loads(session.last_evaluation)
        except (json.JSONDecodeError, ValueError, TypeError):
            # If the model didn't return valid JSON, default to not mastered
            session.last_score = 0.0
            session.mastered = False
            session.last_diagnosis = None
            logger.warning(
                "session_evaluate_parse_failed concept=%s raw=%s",
                session.concept_id, session.last_evaluation[:200],
            )
            eval_data = None

    if eval_data is not None:
        session.last_score = float(eval_data.get("score", 0.0))
        session.mastered = bool(eval_data.get("mastery_achieved", False))
        # Phase B.5: store the diagnosis dict (None when correct).
        session.last_diagnosis = eval_data.get("diagnosis")

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

    # Branch: mastered → next concept; struggling → hint ladder or remediate.
    # Phase B.5 HINT ladder (ADR-002 Rev 2 §3): on a failed quiz, route to
    # HINT_1 (first hint), HINT_2 (second hint), or REMEDIATE based on
    # hint_count. The hint ladder gives the learner a 2-rung chance to
    # self-correct before the full re-teaching. hint_count tracks how many
    # hints have been given so far in this session for this concept.
    config = ctx.config
    mastery_threshold = getattr(config, "mastery_threshold", 0.7) if config else 0.7
    if session.last_score >= mastery_threshold:
        session.state = SessionState.NEXT_CONCEPT
    else:
        # Failed quiz — consult hint_count for the next step.
        if session.hint_count == 0:
            session.state = SessionState.HINT_1
            session.hint_generated = False  # reset for the new HINT step
        elif session.hint_count == 1:
            session.state = SessionState.HINT_2
            session.hint_generated = False
        else:
            # hint_count >= 2 — both hints exhausted, remediate.
            # Respect max_retries: if we've already remediated max_retries
            # times, move on (the learner needs a human teacher).
            if session.retry_count < session.max_retries:
                session.state = SessionState.REMEDIATE
                session.retry_count += 1
            else:
                session.state = SessionState.NEXT_CONCEPT

    logger.info(
        "session_step_evaluate concept=%s score=%.2f mastered=%s state=%s has_diagnosis=%s",
        session.concept_id, session.last_score, session.mastered, session.state.value,
        session.last_diagnosis is not None,
    )
    return ActorResult(ok=True, error=session.last_evaluation)


async def _step_hint(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
    mentor: Any,
    student_input: str,
    *,
    hint_rung: int,
) -> ActorResult:
    """HINT_1/HINT_2: EXAMINER gives a graded hint, learner re-answers (ADR-002 Rev 2 §3).

    Two-phase (like QUIZ + PREDICT):
      Phase 1 (hint_generated=False): call examiner.generate_hint() with the
        current hint_count. Returns a gentle nudge (HINT_1) or a stronger
        clue (HINT_2). Display the hint, wait for the learner's re-answer.
      Phase 2 (hint_generated=True, student_input non-empty): the learner has
        seen the hint + provided a new answer. Re-evaluate via
        examiner.evaluate(). Then route:
        - correct (score >= mastery_threshold) → increment hint_assisted_correct
          on aristotle_mastery, advance to NEXT_CONCEPT.
        - still wrong, hint_rung == 1 → advance to HINT_2 (hint_count becomes 1).
        - still wrong, hint_rung == 2 → advance to REMEDIATE (hint_count
          becomes 2; retry_count incremented).

    Args:
        hint_rung: 1 for HINT_1, 2 for HINT_2. Passed to
            examiner.generate_hint() so it knows which hint strength to give.
    """
    logger = ctx.logger

    if not session.hint_generated:
        # Phase 1: generate the hint.
        result = await examiner.generate_hint(ctx, session.concept_id, session.hint_count)
        if result.ok:
            session.hint_generated = True
            # If student_input was provided (non-interactive mode), proceed
            # to phase 2 immediately in the same call.
            if student_input:
                return await _hint_phase2(ctx, session, examiner, mentor, student_input, hint_rung)
            logger.info(
                "session_step_hint_generate concept=%s rung=%d",
                session.concept_id, hint_rung,
            )
        return result
    else:
        # Phase 2: learner's re-answer arrived.
        return await _hint_phase2(ctx, session, examiner, mentor, student_input, hint_rung)


async def _hint_phase2(
    ctx: ActorContext,
    session: SessionContext,
    examiner: Any,
    mentor: Any,
    student_input: str,
    hint_rung: int,
) -> ActorResult:
    """Phase 2 of a HINT step: re-evaluate the learner's new answer + route.

    Extracted from _step_hint so both the interactive path (phase 1 returns,
    phase 2 called on next step) and the non-interactive path (both phases
    in one call) share the same routing logic.
    """
    logger = ctx.logger

    if student_input:
        session.last_student_answer = student_input

    # Re-evaluate with the new answer (same quiz question, new answer).
    eval_result = await examiner.evaluate(
        ctx, session.concept_id,
        student_answer=session.last_student_answer,
        quiz_question=session.last_quiz_question,
    )
    if not eval_result.ok:
        return eval_result

    # Phase B.5: read from result.data (same migration as _step_evaluate).
    # Fallback to result.error + JSON parse for backward compat.
    if eval_result.data is not None and isinstance(eval_result.data, dict):
        eval_data = eval_result.data
        import json
        session.last_evaluation = json.dumps(eval_data)
    else:
        session.last_evaluation = eval_result.error or ""
        import json
        try:
            eval_data = json.loads(session.last_evaluation)
        except (json.JSONDecodeError, ValueError, TypeError):
            session.last_score = 0.0
            session.mastered = False
            session.last_diagnosis = None
            logger.warning(
                "session_hint_eval_parse_failed concept=%s raw=%s",
                session.concept_id, session.last_evaluation[:200],
            )
            eval_data = None

    if eval_data is not None:
        session.last_score = float(eval_data.get("score", 0.0))
        session.mastered = bool(eval_data.get("mastery_achieved", False))
        session.last_diagnosis = eval_data.get("diagnosis")

    # MENTOR updates the struggle_pattern (non-fatal on failure).
    mentor_result = await mentor.update_struggle_pattern(
        ctx, session.concept_id, session.last_evaluation,
    )
    if not mentor_result.ok:
        logger.warning(
            "session_hint_mentor_update_failed concept=%s error=%s",
            session.concept_id, mentor_result.error,
        )

    # Update SM-2 + mastery state (same as _step_evaluate).
    await _update_mastery(ctx, session)

    # Route based on the new score.
    config = ctx.config
    mastery_threshold = getattr(config, "mastery_threshold", 0.7) if config else 0.7

    if session.last_score >= mastery_threshold:
        # Correct after a hint — increment hint_assisted_correct on
        # aristotle_mastery (the column exists from M003). Best-effort:
        # a DB failure here is non-fatal (the mastery row already exists
        # from _update_mastery; we're just incrementing a counter).
        await _increment_hint_assisted_correct(ctx, session)
        session.state = SessionState.NEXT_CONCEPT
        logger.info(
            "session_hint_correct_after_hint concept=%s rung=%d score=%.2f",
            session.concept_id, hint_rung, session.last_score,
        )
    else:
        # Still wrong after this hint. Increment hint_count + route.
        session.hint_count = hint_rung  # hint_rung is 1 or 2; this sets hint_count to match
        if hint_rung == 1:
            # Was HINT_1, still wrong → HINT_2.
            session.state = SessionState.HINT_2
            session.hint_generated = False  # reset for HINT_2's phase 1
            logger.info(
                "session_hint_still_wrong concept=%s rung=1 → HINT_2",
                session.concept_id,
            )
        else:
            # hint_rung == 2, still wrong → REMEDIATE.
            # Respect max_retries: if we've already remediated max_retries
            # times, move on (the learner needs a human teacher).
            if session.retry_count < session.max_retries:
                session.state = SessionState.REMEDIATE
                session.retry_count += 1
                session.hint_generated = False  # reset for any future HINT
                logger.info(
                    "session_hint_still_wrong concept=%s rung=2 → REMEDIATE retry=%d",
                    session.concept_id, session.retry_count,
                )
            else:
                session.state = SessionState.NEXT_CONCEPT
                logger.info(
                    "session_hint_still_wrong concept=%s rung=2 → NEXT_CONCEPT (max retries)",
                    session.concept_id,
                )

    return ActorResult(ok=True, error=session.last_evaluation)


async def _increment_hint_assisted_correct(
    ctx: ActorContext, session: SessionContext
) -> None:
    """Increment hint_assisted_correct on aristotle_mastery (ADR-002 §10.6, M003).

    Called when the learner gets the answer right after seeing a hint. The
    column was added by M003. Best-effort: non-fatal on DB failure (the
    mastery row already exists from _update_mastery; we're just incrementing
    a counter).
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        await conn.execute(
            "UPDATE aristotle_mastery SET hint_assisted_correct = hint_assisted_correct + 1 "
            "WHERE student_id = ? AND concept_id = ?",
            (session.student_id, session.concept_id),
        )
        await conn.commit()
    except Exception as exc:
        ctx.logger.warning(
            "session_hint_assisted_correct_increment_failed concept=%s error=%s:%s",
            session.concept_id, type(exc).__name__, exc,
        )


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
