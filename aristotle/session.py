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
    last_evaluation: str = (
        ""  # JSON from EXAMINER.evaluate() (legacy — kept for backward compat)
    )
    last_score: float = 0.0
    mastered: bool = False
    # Phase B.5 (transfer questions): the type of the last quiz question.
    # "recognition" (default) or "transfer". Set by _step_quiz, read by
    # _step_evaluate to increment transfer_correct when the answer is right.
    last_question_type: str = "recognition"
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
    # Phase B.5 (interleaving, B.5 item 5): the concept queue for this
    # session. Slot 0 is the primary concept (the one the caller passed).
    # Slots 1-2 are due review concepts (selected by _build_concept_queue
    # at session start). The current concept is always concept_queue[0].
    # When NEXT_CONCEPT fires, queue[0] is popped; if non-empty, the
    # session advances to the next concept; if empty, SESSION_COMPLETE.
    concept_queue: list = field(default_factory=list)
    # Phase D (plan executor bridge): the learning plan this session is
    # driving. When set, _build_concept_queue reads the plan's
    # concept_ids_json[current_concept_idx] as the primary concept (ignoring
    # the caller-supplied primary_concept_id). _step_next_concept advances
    # the plan cursor after each concept is mastered. When the queue empties
    # but the plan has more concepts, the session continues (long-arc
    # executor). When the plan is exhausted, SESSION_COMPLETE.
    plan_id: str = ""
    # Phase B.5 (cold-start check, B.5 item 9): concepts in the queue that
    # need a cold-start check (skip PREDICT/TEACH, go directly to PROBE).
    # Populated by _build_concept_queue for review concepts whose SM-2
    # interval >= 7 days AND cold_start_passed == 0.
    cold_start_pending: set = field(default_factory=set)


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

    # ADR-002 Amendment A1: classify student input for curiosity path.
    # Only non-empty input is classified — empty input (e.g. first call
    # to generate a prompt) always goes through the ANSWER path.
    if student_input:
        intent_class = _classify_student_input(student_input)
        if intent_class in ("QUESTION", "TANGENT"):
            result = await _step_curiosity(ctx, session, student_input, intent_class)
            return result
        elif intent_class == "CHAT":
            result = await _step_chat(ctx, session, student_input)
            return result
        # else: ANSWER — fall through to existing dispatch

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
        return await _step_hint(
            ctx, session, examiner, mentor, student_input, hint_rung=1
        )
    elif session.state == SessionState.HINT_2:
        return await _step_hint(
            ctx, session, examiner, mentor, student_input, hint_rung=2
        )
    elif session.state == SessionState.REMEDIATE:
        return await _step_remediate(ctx, session, socrates, mentor)
    elif session.state == SessionState.NEXT_CONCEPT:
        return await _step_next_concept(ctx, session)
    else:
        return ActorResult(ok=False, error="session already complete")


# ---------------------------------------------------------------------------
# ADR-002 Amendment A1: Curiosity path — open learner model
# ---------------------------------------------------------------------------


def _classify_student_input(text: str) -> str:
    """Classify student input intent for curiosity path routing.

    Returns one of: 'ANSWER' | 'QUESTION' | 'TANGENT' | 'CHAT'

    v1: heuristic/keyword-based (ADR-002 Amendment A1 §v1 note).
    v2: LLM-based classifier (future ADR-002 TODO item).

    Task 24 Fix 2: pattern matching is now sentence-level, not just
    whole-string. The input is split into clauses on `.`, `!`, `?`, and
    newlines, and each trigger phrase is checked against the START of
    EACH clause — not just the start of the whole input. This catches
    real-world inputs like "this is my second session. i want to be
    oriented in what we are learning next" where the question-like part
    ("i want to be oriented...") is in the second clause, not the first.

    The existing whole-string `endswith("?")` check is preserved as-is
    (additive — don't remove or restructure existing checks, just extend
    how they're applied). New patterns added:
      - question_starters: "give me", "show me", "walk me through",
        "orient me", "remind me", "help me understand"
      - tangent_markers: "i want to", "i'd like to", "i wanted to"

    Favor false negatives over false positives (same principle as Task 23's
    decline gate). "let's see" was considered and deliberately left out —
    too likely to be the start of a genuine tentative answer.
    """
    stripped = text.strip()
    lower = stripped.lower()

    # Task 24 Fix 2: split into clauses for sentence-level matching.
    # Each clause is checked independently against the trigger phrases.
    # The whole input is also checked (as the "zeroth clause") so the
    # existing whole-string startswith behavior is preserved.
    import re as _re

    # Split on sentence-ending punctuation + newlines. Keep it simple —
    # we're not doing NLP here, just giving the pattern matcher a fair
    # chance to see clause-internal triggers.
    clauses = [lower]
    clauses.extend(
        c.strip().lower()
        for c in _re.split(r"[.!?\n]", lower)
        if c.strip()
    )

    # QUESTION signals
    if stripped.endswith("?"):
        return "QUESTION"
    question_starters = (
        "what ",
        "why ",
        "how ",
        "when ",
        "where ",
        "who ",
        "could you",
        "can you",
        "would you",
        "explain ",
        "tell me",
        "describe ",
        "define ",
        "what's",
        "how's",
        # Task 24 Fix 2: new question starters — direct-request phrasings
        # that are clearly questions/instructions, not content answers.
        # Each was checked against the false-positive risk: a real content
        # answer is unlikely to start a clause with any of these.
        "give me",
        "show me",
        "walk me through",
        "orient me",
        "remind me",
        "help me understand",
    )
    # Task 24 Fix 2: check each clause, not just the whole input.
    if any(clause.startswith(s) for clause in clauses for s in question_starters):
        return "QUESTION"

    # TANGENT signals
    tangent_markers = (
        "what about",
        "but what",
        "wait,",
        "wait —",
        "actually,",
        "actually —",
        "but ",
        "hold on",
        "i was thinking",
        "speaking of",
        "that reminds me",
        # Task 24 Fix 2: new tangent markers — learner-stance phrasings
        # that signal a redirect away from the current concept. Checked
        # against false-positive risk: "i want to" could start a real
        # answer ("i want to say it's covalent"), but the clause-split
        # means the rest of the clause would need to NOT match any
        # question_starter for this to fire — and the curiosity path is
        # the safer fallback (answers + doesn't grade) when uncertain.
        "i want to",
        "i'd like to",
        "i wanted to",
    )
    # Task 24 Fix 2: check each clause, not just the whole input.
    if any(clause.startswith(m) for clause in clauses for m in tangent_markers):
        return "TANGENT"

    # CHAT signals — short social acknowledgments
    word_count = len(stripped.split())
    if word_count <= 4:
        # Task 26 Fix 3: "no" and "yes" are carved out of the general
        # prefix-matching loop because they have a high collision risk
        # with substantive content. "no idea", "no clue", "yes but I'm
        # not sure" are real content (declines or qualified answers),
        # not bare acknowledgments — but the old prefix match
        # (lower.startswith("no ")) caught them all as CHAT. Require an
        # EXACT match for these two specifically; the rest of social_words
        # (ok, thanks, sure, etc.) keep the existing prefix-match behavior
        # because they don't have the same collision risk.
        exact_only_words = {"no", "yes"}
        prefix_ok_words = (
            "ok",
            "okay",
            "cool",
            "thanks",
            "got it",
            "sure",
            "right",
            "hmm",
            "interesting",
            "i see",
            "makes sense",
            "understood",
        )
        # Exact match for "no" / "yes" (with optional trailing comma/period,
        # which the lower.strip().rstrip(".,") handles — but we already
        # only stripped the whole input, so check both bare and
        # comma-suffixed forms).
        if lower in exact_only_words or lower.rstrip(",.") in exact_only_words:
            return "CHAT"
        # Prefix match for the rest (existing behavior).
        if any(
            lower == s or lower.startswith(s + " ") or lower.startswith(s + ",")
            for s in prefix_ok_words
        ):
            return "CHAT"

    return "ANSWER"


async def _step_curiosity(
    ctx: ActorContext,
    session: SessionContext,
    student_input: str,
    intent_class: str,
    *,
    concept_id: str | None = None,
    session_id: str | None = None,
) -> ActorResult:
    """Handle QUESTION and TANGENT intents (ADR-002 Amendment A1).

    Answers the student's question using the beast model slot (same slot
    as SOCRATES.teach — conversational explanation). Does NOT advance
    session phase. Logs curiosity event. Appends a soft weave-back offer.

    Task 24 Fix 1: takes explicit `concept_id` + `session_id` keyword args
    so it can be called from placement (which uses PlacerSession, not
    SessionContext — PlacerSession has no `concept_id` or `student_id`
    field, so `_derive_session_id(session)` would AttributeError). When
    either is None (the default), falls back to the SessionContext-derived
    value for backward compat with the existing call path. Existing tests
    that don't pass these args continue to work unchanged.
    """
    logger = ctx.logger
    container: Any = ctx.container

    model_provider = getattr(container, "model_provider", None)
    if model_provider is None:
        return ActorResult(
            ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
        )

    # Task 24 Fix 1: prefer the explicit concept_id; fall back to
    # session.concept_id for backward compat with SessionContext callers.
    effective_concept_id = concept_id
    if effective_concept_id is None:
        effective_concept_id = getattr(session, "concept_id", None)

    concept_context = ""
    if effective_concept_id:
        concept_context = f"The student is studying: {effective_concept_id}. "

    system_prompt = (
        "You are Aristotle — a patient, exact tutor. A student has asked "
        "a question or raised a tangent during a tutoring session. Answer "
        "their question clearly and helpfully. If it connects to what "
        "you're studying, make that connection. Keep the response "
        "conversational and under 150 words."
    )
    user_prompt = f"{concept_context}The student asked: {student_input}"

    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        response_text = result.get("content", "")
    except Exception as exc:
        logger.warning("curiosity_model_failed error=%s:%s", type(exc).__name__, exc)
        return ActorResult(ok=False, error=f"model call failed: {exc}")

    # Soft weave-back offer — never forces return.
    weave_back = (
        "\n\nWant to keep exploring this, or shall we continue where we left off?"
    )
    full_response = response_text + weave_back

    # Log curiosity event (best-effort — never raises).
    # Task 24 Fix 1: pass the explicit concept_id + session_id through.
    # When session_id is None, _log_curiosity_event falls back to
    # _derive_session_id(session) — which works for SessionContext but
    # would AttributeError (caught by its try/except) for PlacerSession.
    # Placement callers should pass session_id explicitly to avoid the
    # failed-then-warned internal derivation.
    await _log_curiosity_event(
        ctx, session, student_input, intent_class,
        concept_id=effective_concept_id,
        session_id=session_id,
    )

    logger.info(
        "curiosity_path concept=%s intent=%s response_len=%d",
        effective_concept_id,
        intent_class,
        len(full_response),
    )

    # Session state is NOT advanced — the student stays at the same phase.
    return ActorResult(
        ok=True,
        error=full_response,
        data={"response": full_response, "intent_class": intent_class},
    )


async def _step_chat(
    ctx: ActorContext,
    session: SessionContext,
    student_input: str,
) -> ActorResult:
    """Handle CHAT intents — brief conversational reply (ADR-002 Amendment A1).

    Keeps session alive but advances no state.
    """
    logger = ctx.logger
    container: Any = ctx.container

    model_provider = getattr(container, "model_provider", None)
    if model_provider is None:
        return ActorResult(
            ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
        )

    system_prompt = (
        "You are Aristotle — a warm, patient tutor. The student sent a "
        "brief social message. Respond warmly in one sentence and gently "
        "invite them to continue the session."
    )
    user_prompt = f'The student sent: "{student_input}"'

    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        response_text = result.get("content", "")
    except Exception as exc:
        logger.warning("chat_model_failed error=%s:%s", type(exc).__name__, exc)
        return ActorResult(ok=False, error=f"model call failed: {exc}")

    logger.info(
        "chat_path concept=%s response_len=%d",
        getattr(session, "concept_id", None),
        len(response_text),
    )

    return ActorResult(
        ok=True,
        error=response_text,
        data={"response": response_text, "intent_class": "CHAT"},
    )


async def _log_curiosity_event(
    ctx: ActorContext,
    session: SessionContext,
    student_input: str,
    intent_class: str,
    *,
    concept_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Log curiosity event to aristotle_misconception_log (best-effort).

    Uses the intent_class column added by M006. Does NOT update
    last_score — curiosity cannot fail a concept.

    Task 24 Fix 1: takes explicit `concept_id` + `session_id` keyword args
    so it can be called from placement (which uses PlacerSession, not
    SessionContext — PlacerSession has no `concept_id` or `student_id`
    field, so `_derive_session_id(session)` would AttributeError). When
    either is None (the default), falls back to the SessionContext-derived
    value for backward compat with the existing call path. Existing tests
    that don't pass these args continue to work unchanged.
    """
    logger = ctx.logger
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    # Task 24 Fix 1: prefer explicit params; fall back to session-derived
    # values for backward compat with SessionContext callers.
    effective_concept_id = concept_id
    if effective_concept_id is None:
        effective_concept_id = getattr(session, "concept_id", None)
    effective_session_id = session_id
    if effective_session_id is None:
        effective_session_id = _derive_session_id(session)

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        await conn.execute(
            "INSERT INTO aristotle_misconception_log "
            "(session_id, concept_id, misconception_text, corrective_text, intent_class) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                effective_session_id,
                effective_concept_id,
                student_input,
                "",  # no corrective text for curiosity events
                intent_class,
            ),
        )
        await conn.commit()
    except Exception as exc:
        logger.warning(
            "curiosity_log_failed concept=%s error=%s:%s",
            effective_concept_id,
            type(exc).__name__,
            exc,
        )


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
            session.concept_id,
            len(session.last_prediction),
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
        session_id = _derive_session_id(session)
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
            session.concept_id,
            type(exc).__name__,
            exc,
        )


def _derive_session_id(session: SessionContext) -> str:
    """Derive a session_id for analytics tables (predict_event, misconception_log).

    Phase B.5 pre-alpha: single-tenant, no session table yet. Derive from
    student_id + concept_id + a timestamp so multiple sessions per concept
    are distinguishable. A real session_id comes in Phase D with the
    aristotle_intake_session table.
    """
    from datetime import datetime, timezone

    return f"{session.student_id}:{session.concept_id}:{datetime.now(timezone.utc).isoformat()}"


async def _get_mastery_level(ctx: ActorContext, session: SessionContext) -> int:
    """Query the concept's mastery level (repetitions column on aristotle_mastery).

    Phase B.5 (faded worked examples): used by _step_teach + _step_remediate
    to adapt the teach() prompt. The `repetitions` column counts consecutive
    correct reviews (SM-2) — it's the natural mastery-level proxy:
      0 = new concept (no mastery row yet, or row exists but 0 reps)
      1-2 = early mastery
      3+ = near-mastered

    Returns 0 if no mastery row exists yet (new concept) or if the query
    fails (best-effort — the teach step should never block on a DB read).
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return 0

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        cur = await conn.execute(
            "SELECT repetitions FROM aristotle_mastery "
            "WHERE student_id = ? AND concept_id = ?",
            (session.student_id, session.concept_id),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return 0
        return int(row[0])
    except Exception:
        return 0  # best-effort — never block the teach step


async def _build_concept_queue(
    ctx: ActorContext,
    primary_concept_id: str,
    student_id: str = "definer",
    plan_id: str = "",
) -> tuple[list, set]:
    """Build the interleaved concept queue for a session (ADR-002 §6, B.5 item 5).

    Phase D (plan executor bridge): if plan_id is provided and non-empty,
    reads the plan's concept_ids_json[current_concept_idx] as the primary
    concept (ignoring the caller-supplied primary_concept_id). The review
    concepts are still queried from aristotle_mastery (due for SM-2 review)
    but filtered to plan concepts only (those that appear earlier in the
    plan, idx < current_concept_idx).

    Slot 0 (primary): from the plan (if plan_id set) or the caller's arg.
    Slots 1-2 (review): up to 2 concepts where next_review_at <= now
    AND concept_id != primary, ordered by next_review_at ASC. These are
    due for a spaced-repetition retrieval check — interleaved practice
    (contextual interference effect, Bjork).

    Also identifies cold-start candidates: review concepts whose SM-2
    interval >= 7 days AND cold_start_passed == 0. These skip
    PREDICT/TEACH and go directly to PROBE (unassisted retrieval) —
    the cold-start check catches overreliance on hints (B.5 item 9).

    Returns: (concept_queue, cold_start_pending)
      - concept_queue: list of concept_ids, [primary, review1, review2?]
      - cold_start_pending: set of concept_ids needing a cold-start check

    If no review concepts are due, returns ([primary], set()).
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return [primary_concept_id], set()

    # Phase D: if plan_id is set, read the plan to determine the primary concept.
    actual_primary = primary_concept_id
    plan_concept_set = (
        None  # None = no plan filtering (all concepts eligible for review)
    )
    if plan_id:
        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn
            cur = await conn.execute(
                "SELECT concept_ids_json, current_concept_idx, status "
                "FROM aristotle_learning_plan WHERE id = ?",
                (plan_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is not None:
                import json as _json

                concept_ids = _json.loads(row[0]) if row[0] else []
                current_idx = row[1] if row[1] is not None else 0
                plan_status = row[2] if row[2] is not None else "active"
                if plan_status == "complete":
                    # Plan is complete — no new concepts to study.
                    return [], set()
                if current_idx < len(concept_ids):
                    actual_primary = concept_ids[current_idx]
                # Filter reviews to plan concepts only (those before current_idx).
                plan_concept_set = set(concept_ids[:current_idx])
        except Exception:
            pass  # best-effort — fall back to caller's primary_concept_id

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        now_iso = datetime.now(timezone.utc).isoformat()

        # Query for due review concepts (next_review_at <= now, not the primary).
        # Order by next_review_at ASC so the most overdue come first. Limit 2.
        cur = await conn.execute(
            "SELECT concept_id, interval_days, cold_start_passed "
            "FROM aristotle_mastery "
            "WHERE student_id = ? AND concept_id != ? "
            "AND next_review_at IS NOT NULL AND next_review_at <= ? "
            "ORDER BY next_review_at ASC LIMIT 2",
            (student_id, actual_primary, now_iso),
        )
        rows = await cur.fetchall()
        await cur.close()

        queue = [actual_primary]
        if rows is None:
            rows = []
        cold_start_pending: set = set()

        for row in rows:
            review_concept_id = row[0]
            # Phase D: if a plan is attached, filter reviews to plan concepts only.
            if (
                plan_concept_set is not None
                and review_concept_id not in plan_concept_set
            ):
                continue
            interval_days = row[1] if row[1] is not None else 0
            cold_start_passed = row[2] if row[2] is not None else 0
            queue.append(review_concept_id)
            # Cold-start check: interval >= 7 days AND not yet passed.
            if interval_days >= 7 and cold_start_passed == 0:
                cold_start_pending.add(review_concept_id)

        return queue, cold_start_pending
    except Exception:
        return [primary_concept_id], set()


async def _advance_plan_cursor(ctx: ActorContext, plan_id: str) -> None:
    """Increment current_concept_idx on the learning plan (Phase D bridge).

    If the new idx >= len(concept_ids_json): set status='complete'.
    Best-effort — don't fail the session on DB error.
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # Read the plan to get concept count + current idx.
        cur = await conn.execute(
            "SELECT concept_ids_json, current_concept_idx "
            "FROM aristotle_learning_plan WHERE id = ?",
            (plan_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return

        import json as _json

        concept_ids = _json.loads(row[0]) if row[0] else []
        current_idx = row[1] if row[1] is not None else 0
        new_idx = current_idx + 1

        if new_idx >= len(concept_ids):
            # Plan exhausted — mark complete.
            await conn.execute(
                "UPDATE aristotle_learning_plan SET status = 'complete', "
                "current_concept_idx = ? WHERE id = ?",
                (new_idx, plan_id),
            )
        else:
            await conn.execute(
                "UPDATE aristotle_learning_plan SET current_concept_idx = ? "
                "WHERE id = ?",
                (new_idx, plan_id),
            )
        await conn.commit()
    except Exception:
        pass  # best-effort — never block the session


async def _increment_mastery_column(
    ctx: ActorContext, session: SessionContext, column_name: str
) -> None:
    """Increment an integer counter column on aristotle_mastery (best-effort).

    Phase B.5: used by _step_quiz (transfer_attempted) and _step_evaluate
    (transfer_correct). The columns were added by M003. Best-effort:
    non-fatal on DB failure (the mastery row may not exist yet; the
    counter is analytics, not control flow).
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        # parameterized column name isn't supported in SQL — validate
        # against a whitelist to prevent injection.
        allowed = {
            "transfer_attempted",
            "transfer_correct",
            "hint_assisted_correct",
            "slip_count",
            "cold_start_passed",
        }
        if column_name not in allowed:
            return
        await conn.execute(
            f"UPDATE aristotle_mastery SET {column_name} = {column_name} + 1 "
            "WHERE student_id = ? AND concept_id = ?",
            (session.student_id, session.concept_id),
        )
        await conn.commit()
    except Exception:
        pass  # best-effort — analytics counter, never block the session


async def _check_and_synthesize_pattern(ctx: ActorContext, concept_id: str) -> None:
    """Check if pattern synthesis should fire + synthesize if so (ADR-002 §7).

    Fires when the misconception count for a concept is a multiple of 3
    AND >= 3 (i.e. at 3, 6, 9, ...). This catches persistent patterns
    without synthesizing on every session.

    When synthesis fires:
    1. Fetch up to 9 most recent misconception_text entries for the concept.
    2. Call mentor.synthesize_struggle_pattern() (model does the synthesis).
    3. Write the result to aristotle_struggle_pattern.

    Best-effort throughout — DB errors and model errors are swallowed.
    The session must NEVER fail on this.
    """
    logger = ctx.logger
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # Count misconception entries for this concept.
        cur = await conn.execute(
            "SELECT COUNT(*) FROM aristotle_misconception_log WHERE concept_id = ?",
            (concept_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        count = row[0] if row is not None else 0

        # Fire at 3, 6, 9 — multiples of 3, >= 3.
        if count < 3 or count % 3 != 0:
            return

        # Fetch up to 9 most recent misconception_text entries.
        cur = await conn.execute(
            "SELECT misconception_text FROM aristotle_misconception_log "
            "WHERE concept_id = ? ORDER BY id DESC LIMIT 9",
            (concept_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        if not rows:
            return

        misconceptions = [r[0] for r in rows if r[0]]

        # Call MENTOR to synthesize the pattern.
        from aristotle.actors.mentor import MentorActor

        mentor = MentorActor()
        result = await mentor.synthesize_struggle_pattern(
            ctx,
            concept_id,
            misconceptions,
        )

        pattern = ""
        if result.ok and result.data:
            pattern = result.data.get("pattern", "")

        if not pattern:
            logger.info(
                "session_pattern_synthesize_empty concept=%s count=%d",
                concept_id,
                count,
            )
            return

        # Write the synthesized pattern to aristotle_struggle_pattern.
        # The struggle_pattern table is keyed by student_id (single-tenant
        # pre-alpha — student_id='definer'). The pattern is a concept-
        # specific synthesis, but for pre-alpha we write it as the
        # student's overall struggle_pattern (the most recent concept's
        # pattern takes precedence — a future revision may key by concept).
        await conn.execute(
            "INSERT OR REPLACE INTO aristotle_struggle_pattern "
            "(student_id, pattern_text) VALUES (?, ?)",
            ("definer", pattern),
        )
        await conn.commit()
        logger.info(
            "session_pattern_synthesized concept=%s count=%d pattern_len=%d",
            concept_id,
            count,
            len(pattern),
        )
    except Exception as exc:
        logger.warning(
            "session_pattern_synthesize_failed concept=%s error=%s:%s",
            concept_id,
            type(exc).__name__,
            exc,
        )


async def _step_teach(
    ctx: ActorContext,
    session: SessionContext,
    socrates: Any,
) -> ActorResult:
    """TEACH: SOCRATES explains the concept (ADR-001 §3 + ADR-002 Rev 2 §3/§4).

    Phase B.5 (faded worked examples): queries the concept's mastery level
    (repetitions column on aristotle_mastery) before calling teach(), so
    the prompt can adapt — full worked example for new concepts, partial
    faded for early mastery, conceptual-only for near-mastered. Default 0
    when no mastery row exists yet.
    """
    logger = ctx.logger
    mastery_level = await _get_mastery_level(ctx, session)
    result = await socrates.teach(ctx, session.concept_id, mastery_level=mastery_level)

    if result.ok:
        # Phase B.5: read from result.data (not error-as-payload).
        # Fallback to result.error for backward compat with any actor
        # that hasn't migrated yet.
        if result.data is not None and isinstance(result.data, dict):
            session.last_explanation = result.data.get("explanation", "")
        else:
            session.last_explanation = result.error or ""
        session.state = SessionState.PROBE
        logger.info(
            "session_step_teach concept=%s mastery_level=%d",
            session.concept_id,
            mastery_level,
        )
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
    if (
        session.probe_generated and session.student_input
        if hasattr(session, "student_input")
        else False
    ):
        pass  # This path is handled by the caller passing student_input to the next step

    result = await examiner.probe(ctx, session.concept_id)

    if result.ok:
        # Phase B.5: read from result.data (not error-as-payload).
        # probe() shares _generate_question with quiz(), which was migrated.
        if result.data is not None and isinstance(result.data, dict):
            session.last_probe_question = result.data.get("question", "")
        else:
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
    """QUIZ: EXAMINER asks a real question (ADR-001 §3 + ADR-002 Rev 2 §3).

    Two-phase: first call generates the quiz question (no input needed),
    second call (with student_input) records the answer and advances to EVALUATE.

    Phase B.5 (transfer questions): the question type is selected based on
    mastery_level — recognition for level < 2, transfer for level >= 2.
    Transfer questions apply the concept to a new situation the learner
    hasn't seen. When transfer is selected, transfer_attempted is
    incremented on aristotle_mastery (column from M003). The question
    type is stored on session.last_question_type so _step_evaluate can
    increment transfer_correct when the answer is right.
    """
    logger = ctx.logger

    if not session.quiz_generated:
        # Phase B.5: select question_type based on mastery_level.
        mastery_level = await _get_mastery_level(ctx, session)
        if mastery_level >= 2:
            question_type = "transfer"
            # Best-effort: increment transfer_attempted on aristotle_mastery.
            await _increment_mastery_column(ctx, session, "transfer_attempted")
        else:
            question_type = "recognition"

        # Phase 1: generate the quiz question
        result = await examiner.quiz(
            ctx, session.concept_id, question_type=question_type
        )
        if result.ok:
            # Phase B.5: read from result.data (not error-as-payload).
            if result.data is not None and isinstance(result.data, dict):
                session.last_quiz_question = result.data.get("question", "")
                session.last_question_type = result.data.get(
                    "question_type", question_type
                )
            else:
                session.last_quiz_question = result.error or ""
                session.last_question_type = question_type
            session.quiz_generated = True
            # If student_input was provided (non-interactive mode), record it
            # and advance to EVALUATE immediately
            if student_input:
                session.last_student_answer = student_input
                session.state = SessionState.EVALUATE
            logger.info(
                "session_step_quiz_generate concept=%s question_type=%s mastery_level=%d",
                session.concept_id,
                session.last_question_type,
                mastery_level,
            )
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
        ctx,
        session.concept_id,
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
                session.concept_id,
                session.last_evaluation[:200],
            )
            eval_data = None

    if eval_data is not None:
        session.last_score = float(eval_data.get("score", 0.0))
        session.mastered = bool(eval_data.get("mastery_achieved", False))
        # Phase B.5: store the diagnosis dict (None when correct).
        session.last_diagnosis = eval_data.get("diagnosis")

    # MENTOR updates the struggle_pattern
    mentor_result = await mentor.update_struggle_pattern(
        ctx,
        session.concept_id,
        session.last_evaluation,
    )
    # MENTOR failure is non-fatal — the evaluation still counts
    if not mentor_result.ok:
        logger.warning(
            "session_mentor_update_failed concept=%s error=%s",
            session.concept_id,
            mentor_result.error,
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
        # Phase B.5 (transfer questions): if the correct answer was for a
        # transfer question, increment transfer_correct on aristotle_mastery
        # (column from M003). Best-effort — non-fatal on DB failure.
        if session.last_question_type == "transfer":
            await _increment_mastery_column(ctx, session, "transfer_correct")

        # Phase B.5 (cold-start check, item 9): if this concept was in
        # cold_start_pending (unassisted retrieval after a long interval),
        # a correct answer means the learner has independent recall —
        # mark cold_start_passed = 1 + remove from pending.
        if session.concept_id in session.cold_start_pending:
            await _increment_mastery_column(ctx, session, "cold_start_passed")
            # _increment_mastery_column increments, but cold_start_passed
            # is a flag (0→1), not a counter. Use a direct UPDATE to set
            # it to 1. Best-effort.
            try:
                container_cs = ctx.container
                registry_cs = getattr(container_cs, "corpus_registry", None)
                if registry_cs is not None:
                    stores_cs = await registry_cs.get_stores("aristotle:textbook")
                    conn_cs = stores_cs.connection_manager.write_conn
                    await conn_cs.execute(
                        "UPDATE aristotle_mastery SET cold_start_passed = 1 "
                        "WHERE student_id = ? AND concept_id = ?",
                        (session.student_id, session.concept_id),
                    )
                    await conn_cs.commit()
            except Exception:
                pass  # best-effort
            session.cold_start_pending.discard(session.concept_id)
            logger.info(
                "session_cold_start_passed concept=%s — independent recall confirmed",
                session.concept_id,
            )

        session.state = SessionState.NEXT_CONCEPT
    else:
        # Phase B.5 (cold-start check, item 9): if this was a cold-start
        # concept and the answer was wrong, remove from pending — the
        # normal session flow (TEACH → PROBE → QUIZ → ...) resumes on
        # the next cycle via REMEDIATE/HINT.
        if session.concept_id in session.cold_start_pending:
            session.cold_start_pending.discard(session.concept_id)
            logger.info(
                "session_cold_start_failed concept=%s — normal flow resumes",
                session.concept_id,
            )

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

    # Phase B.5 (misconception log, B.5 item 7): if the answer was wrong
    # and EXAMINER produced a diagnosis, fire-and-forget a
    # mentor.log_misconception() call. This is best-effort — a DB failure
    # never breaks the session (log_misconception returns ok=True always).
    # The misconception log builds a queryable per-instance history that
    # complements the long-arc struggle_pattern (ADR-002 §7).
    if session.last_diagnosis is not None and session.last_score < mastery_threshold:
        session_id = _derive_session_id(session)
        try:
            await mentor.log_misconception(
                ctx,
                concept_id=session.concept_id,
                session_id=session_id,
                diagnosis=session.last_diagnosis,
            )
        except Exception as exc:
            # Non-fatal — log + continue. The session must never break
            # over an analytics row.
            logger.warning(
                "session_misconception_log_failed concept=%s error=%s:%s",
                session.concept_id,
                type(exc).__name__,
                exc,
            )

        # Phase D (MENTOR pattern recognition, ADR-002 §7): after logging
        # the misconception, check if pattern synthesis should fire. This
        # is a second independent fire-and-forget call — it runs after
        # log_misconception so the new entry is included in the count.
        # Best-effort: DB errors and model errors are swallowed.
        try:
            await _check_and_synthesize_pattern(ctx, session.concept_id)
        except Exception as exc:
            logger.warning(
                "session_pattern_check_failed concept=%s error=%s:%s",
                session.concept_id,
                type(exc).__name__,
                exc,
            )

    logger.info(
        "session_step_evaluate concept=%s score=%.2f mastered=%s state=%s has_diagnosis=%s",
        session.concept_id,
        session.last_score,
        session.mastered,
        session.state.value,
        session.last_diagnosis is not None,
    )
    # Task 22 Fix 1 (contract fix in the session coordinator): pass
    # `data=eval_data` through so the API's `output` field can read
    # `data.feedback` (the learner-facing message). Previously this
    # returned `ActorResult(ok=True, error=session.last_evaluation)` —
    # the legacy error-as-payload pattern from pre-Phase-B.5 — which put
    # the JSON string in `error` and left `data=None`. The API then
    # couldn't read `data.feedback`, so EVALUATE's feedback was silently
    # dropped from the chat UI (same bug as TEACH/PROBE/QUIZ, different
    # root cause: the coordinator re-wrapped the actor's result instead
    # of passing it through).
    #
    # We keep `error=session.last_evaluation` for backward compat with
    # any consumer that still reads result.error (e.g. the legacy
    # _session_to_dict serialization that includes last_evaluation).
    # The new `data=eval_data` is the canonical channel — the API reads
    # it preferentially. eval_data is the parsed dict from
    # examiner.evaluate(); when the model returned non-JSON (legacy
    # fallback path above), eval_data is None and we pass an empty dict
    # so the API's `isinstance(result.data, dict)` check still holds.
    return ActorResult(
        ok=True,
        error=session.last_evaluation,  # backward compat (legacy JSON string)
        data=eval_data if eval_data is not None else {},
    )


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
        result = await examiner.generate_hint(
            ctx, session.concept_id, session.hint_count
        )
        if result.ok:
            session.hint_generated = True
            # If student_input was provided (non-interactive mode), proceed
            # to phase 2 immediately in the same call.
            if student_input:
                return await _hint_phase2(
                    ctx, session, examiner, mentor, student_input, hint_rung
                )
            logger.info(
                "session_step_hint_generate concept=%s rung=%d",
                session.concept_id,
                hint_rung,
            )
        return result
    else:
        # Phase 2: learner's re-answer arrived.
        return await _hint_phase2(
            ctx, session, examiner, mentor, student_input, hint_rung
        )


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
        ctx,
        session.concept_id,
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
                session.concept_id,
                session.last_evaluation[:200],
            )
            eval_data = None

    if eval_data is not None:
        session.last_score = float(eval_data.get("score", 0.0))
        session.mastered = bool(eval_data.get("mastery_achieved", False))
        session.last_diagnosis = eval_data.get("diagnosis")

    # MENTOR updates the struggle_pattern (non-fatal on failure).
    mentor_result = await mentor.update_struggle_pattern(
        ctx,
        session.concept_id,
        session.last_evaluation,
    )
    if not mentor_result.ok:
        logger.warning(
            "session_hint_mentor_update_failed concept=%s error=%s",
            session.concept_id,
            mentor_result.error,
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
            session.concept_id,
            hint_rung,
            session.last_score,
        )
    else:
        # Still wrong after this hint. Increment hint_count + route.
        session.hint_count = (
            hint_rung  # hint_rung is 1 or 2; this sets hint_count to match
        )
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
                    session.concept_id,
                    session.retry_count,
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
            session.concept_id,
            type(exc).__name__,
            exc,
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

    # Phase B.5: pass mastery_level so the re-teaching uses the right
    # fading mode (a learner at level 3+ who somehow ends up in REMEDIATE
    # should get a conceptual re-frame, not a full worked example).
    mastery_level = await _get_mastery_level(ctx, session)
    result = await socrates.teach(
        ctx,
        session.concept_id,
        retry=True,
        struggle_pattern=struggle_pattern,
        mastery_level=mastery_level,
    )

    if result.ok:
        # Phase B.5: read from result.data (not error-as-payload).
        if result.data is not None and isinstance(result.data, dict):
            session.last_explanation = result.data.get("explanation", "")
        else:
            session.last_explanation = result.error or ""
        # After remediation, re-probe (ADR-001 §3: re-probe after remediation)
        session.state = SessionState.PROBE
        logger.info(
            "session_step_remediate concept=%s retry=%d",
            session.concept_id,
            session.retry_count,
        )
    return result


async def _step_next_concept(
    ctx: ActorContext,
    session: SessionContext,
) -> ActorResult:
    """NEXT_CONCEPT: advance to the next concept in the queue, or complete (ADR-002 §6).

    Phase B.5 (interleaving): pops concept_queue[0] (the just-completed
    concept). If the queue is non-empty, sets concept_id = queue[0],
    resets the per-concept state (state, hint_count, last_diagnosis,
    last_question_type, predict_generated, etc.), and continues the
    session. If the queue is empty, the session is complete.

    Cold-start handling: if the new concept_id is in cold_start_pending,
    the state is set to PROBE (not PREDICT) — the cold-start check skips
    PREDICT/TEACH and goes directly to unassisted retrieval (B.5 item 9).

    Phase D (plan executor bridge): if session.plan_id is set, advances
    the plan cursor after the primary concept is completed. When the queue
    empties but the plan has more concepts, rebuilds the queue from the
    plan (long-arc executor — the plan keeps driving sessions until
    complete). When the plan is exhausted (status=complete),
    SESSION_COMPLETE.
    """
    logger = ctx.logger

    # Phase D: if a plan is attached, advance the plan cursor.
    if session.plan_id:
        await _advance_plan_cursor(ctx, session.plan_id)

    # Pop the just-completed concept from the queue.
    if session.concept_queue:
        session.concept_queue.pop(0)

    if session.concept_queue:
        # Advance to the next concept in the queue.
        next_concept = session.concept_queue[0]
        session.concept_id = next_concept

        # Reset per-concept state for the new concept.
        session.hint_count = 0
        session.hint_generated = False
        session.last_diagnosis = None
        session.last_question_type = "recognition"
        session.predict_generated = False
        session.last_prediction = ""
        session.last_explanation = ""
        session.last_probe_question = ""
        session.last_quiz_question = ""
        session.last_student_answer = ""
        session.last_evaluation = ""
        session.last_score = 0.0
        session.mastered = False
        session.retry_count = 0
        session.quiz_generated = False
        session.probe_generated = False

        # Cold-start check: if the new concept is in cold_start_pending,
        # skip PREDICT/TEACH and go directly to PROBE (unassisted).
        if next_concept in session.cold_start_pending:
            session.state = SessionState.PROBE
            logger.info(
                "session_step_next_concept cold_start concept=%s — skipping PREDICT/TEACH",
                next_concept,
            )
        else:
            session.state = SessionState.PREDICT
            logger.info(
                "session_step_next_concept advance concept=%s queue_len=%d",
                next_concept,
                len(session.concept_queue),
            )
        return ActorResult(
            ok=True,
            error=f"Advancing to concept {next_concept}. Cold-start: {next_concept in session.cold_start_pending}",
        )
    else:
        # Queue empty. Phase D: if a plan is attached, check if the plan
        # has more concepts. If so, rebuild the queue (long-arc executor).
        if session.plan_id:
            new_queue, new_cold_start = await _build_concept_queue(
                ctx,
                "",
                plan_id=session.plan_id,
            )
            if new_queue:
                # Plan has more concepts — continue the session.
                session.concept_queue = new_queue
                session.cold_start_pending = new_cold_start
                session.concept_id = new_queue[0]
                # Reset per-concept state.
                session.hint_count = 0
                session.hint_generated = False
                session.last_diagnosis = None
                session.last_question_type = "recognition"
                session.predict_generated = False
                session.last_prediction = ""
                session.last_explanation = ""
                session.last_probe_question = ""
                session.last_quiz_question = ""
                session.last_student_answer = ""
                session.last_evaluation = ""
                session.last_score = 0.0
                session.mastered = False
                session.retry_count = 0
                session.quiz_generated = False
                session.probe_generated = False
                if session.concept_id in session.cold_start_pending:
                    session.state = SessionState.PROBE
                else:
                    session.state = SessionState.PREDICT
                logger.info(
                    "session_step_next_concept long_arc concept=%s — rebuilt queue from plan",
                    session.concept_id,
                )
                return ActorResult(
                    ok=True,
                    error=f"Long-arc: advancing to concept {session.concept_id} from plan.",
                )
            # else: plan exhausted (status=complete) → fall through to SESSION_COMPLETE.

        # Queue empty AND no plan (or plan exhausted) — session complete.
        session.state = SessionState.SESSION_COMPLETE
        logger.info(
            "session_step_next_concept concept=%s mastered=%s — session complete (queue empty)",
            session.concept_id,
            session.mastered,
        )
        return ActorResult(
            ok=True,
            error=f"Concept {session.concept_id} session complete. Mastered: {session.mastered}",
        )


async def _update_mastery(ctx: ActorContext, session: SessionContext) -> None:
    """Update the aristotle_mastery table with SM-2 state + score.

    Phase B.5 (item 8 — extended mastery model): also tracks slips. A slip
    is a correct answer that scored below 0.85 — a near-miss where the
    learner got it right but shakily. Slip count feeds mastery_probability()
    as a penalty (slip_rate = slip_count / repetitions). The slip increment
    is best-effort via _increment_mastery_column.

    TODO: mastery_probability is computed in the health snapshot (see
    _step_evaluate's return) but does NOT yet drive the SM-2 interval
    logic. This is intentional — the probabilistic model is read-only
    diagnostic for now. A future commit may use mastery_probability to
    adjust intervals (e.g. shorten the interval if probability drops
    below 0.5 despite a correct answer).
    """
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
                session.student_id,
                session.concept_id,
                new_state.easiness_factor,
                new_state.interval_days,
                new_state.repetitions,
                new_state.next_review_at,
                session.last_score,
                mastered,
                now,
            ),
        )
        await conn.commit()

        # Phase B.5 (item 8): slip tracking. A slip is a correct answer
        # that scored below 0.85 — the learner got it right but shakily.
        # This feeds mastery_probability() as a penalty.
        if session.mastered and 0.7 <= session.last_score < 0.85:
            await _increment_mastery_column(ctx, session, "slip_count")
    except Exception:
        pass  # non-fatal — mastery tracking is best-effort
