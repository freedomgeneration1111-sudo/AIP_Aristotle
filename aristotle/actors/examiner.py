"""EXAMINER actor — ADR-001 §2.

Role: Probe / quiz / evaluate. Generates questions, scores answers, decides
mastery. Drives the PROBE → QUIZ → EVALUATE transitions in the tutoring
state machine (ADR-001 §3).

Phase A: the actor has two faces:
  1. `run_cycle(ctx)` — the startup health check (verifies corpus + model
     reachability). Called by the host's scheduler once on start.
  2. `probe(ctx, concept_id)`, `quiz(ctx, concept_id)`, `evaluate(ctx, concept_id, student_answer)`
     — the real tutoring methods. Called on-demand by the session coordinator.

The tutoring state machine is actor-driven (the platform's ScriptNode is
disabled in production — platform gap logged in TECH_DEBT).

Layer: imports from aip.foundation.protocols.actors only (ActorResult,
ActorContext). The container is accessed via ctx.container (duck-typed).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


# ---------------------------------------------------------------------------
# Task 21 — shared helpers for resilient model calls
# ---------------------------------------------------------------------------

# How many times to retry a model call that returned error=True or empty
# content (rate-limit / transient network blip). Mirrors the retry shape
# already used in aristotle/actors/intake.py for the `beast`-slot call.
_MAX_MODEL_RETRIES = 2


def _model_call_failed(result: Any) -> bool:
    """Canonical failure check — matches ModelSlotResolver's `primary_failed`.

    A "failure" is: result.error truthy, OR content missing/whitespace-only.
    The platform's ModelSlotResolver returns `{"error": True, "content": "",
    "error_message": "..."}` on API failures (429, 5xx, network) instead of
    raising — so callers MUST inspect the dict, not just catch exceptions.
    """
    if not isinstance(result, dict):
        return True
    if result.get("error"):
        return True
    content = result.get("content", "")
    return not str(content).strip()


def _strip_json_fences(text: str) -> str:
    """Strip a leading/trailing ```json ... ``` fence if present.

    The evaluation model sometimes wraps its JSON response in a markdown
    code fence (```json\n{...}\n```). json.loads() fails on the raw fenced
    text — this is a solvable input-cleaning problem, not a malformed
    response. Language tag is optional (handles both ``` and ```json).
    """
    if not text:
        return text
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Task 23 Fix 1 — decline-to-answer gate
# ---------------------------------------------------------------------------
#
# A live placement run showed the tutor walking through 7 different concepts
# while the student answered "no. teach me about it." to every single one —
# and the system kept advancing to a NEW concept each time instead of
# teaching any of them. Root cause: run_placer_step sends whatever the
# student typed straight to examiner.evaluate(), which sends it to the
# grading model. The free-tier model mishandled the refusal-style non-content
# answer and self-reported mastery_achieved=true, so run_placer_step wrote
# a permanent aristotle_mastery row (repetitions=3, mastered=1) and advanced.
#
# Fix 1 lives INSIDE evaluate() (not in run_placer_step or session.py) so
# every caller gets the gate for free — placement, the quiz-check path, and
# any future caller. The gate runs BEFORE any model call, so a decline
# doesn't burn an LLM token and can't be mis-graded.
#
# The pattern list is intentionally TIGHT — favor false negatives over false
# positives. A genuine content answer that happens to start with "no" (e.g.
# "no, it binds non-covalently to the receptor") must NOT trigger the gate.
# If you're not confident a pattern is unambiguous, leave it out.

_DECLINE_PATTERNS = frozenset({
    "no",
    "i don't know",
    "i dont know",
    "idk",
    "not sure",
    "skip",
    "pass",
    "teach me",
    "teach me about it",
    "teach me about that",
    "just teach me",
    "i don't know, teach me",
    "i dont know, teach me",
    "no, teach me",
    "no, teach me about it",
    "no. teach me about it",
    "no. teach me",
})


def _is_decline_to_answer(text: str) -> bool:
    """Return True if the student's answer is an explicit decline, not a
    content attempt.

    Normalizes (strip whitespace, lowercase, strip trailing punctuation)
    then checks against _DECLINE_PATTERNS. Also catches short variants
    that clearly contain the "teach me" signal without being a real
    content attempt — but only when the text is short (< 40 chars), so
    a long answer that happens to include "teach me" (e.g. "Can you
    teach me more about how the receptor binds? I think it's covalent")
    is NOT caught — that's a real content attempt with a question, and
    the model should grade it.

    Designed to favor false negatives over false positives: when in
    doubt, send the answer to the model. The model can grade a genuine
    "I don't know" as score=0.0 safely; the danger is the reverse
    (gate-firing on a real answer and skipping the model).
    """
    if not text:
        return False
    normalized = text.strip().lower().rstrip(".!?;,:")
    if not normalized:
        return False
    if normalized in _DECLINE_PATTERNS:
        return True
    # Catch short "teach me" variants that clearly contain the decline
    # signal without being a real content attempt. The 40-char ceiling
    # is deliberate — a longer message that includes "teach me" is
    # almost certainly a real question, not a pure decline.
    if "teach me" in normalized and len(normalized) < 40:
        return True
    return False


async def _call_with_retry(
    model_provider: Any,
    *,
    slot_name: str,
    messages: list[dict],
) -> tuple[Any, Exception | None]:
    """Call model_provider.call() with up to _MAX_MODEL_RETRIES retries.

    Same backoff shape as aristotle/actors/intake.py::run_intake_step's
    `beast`-slot loop: sleep `1.0 * (attempt + 1)` seconds between attempts
    (1s, then 2s). Returns the final result dict (which may still be a
    failure) and the last exception (or None if no exception was raised).

    The retry triggers on:
      - raised exception (network error)
      - result.error == True (API failure, e.g. 429)
      - empty/whitespace content (OpenRouter free models sometimes return
        200 with empty content on rate-limit)

    If all retries are exhausted, the last result (or a synthesized
    error dict if the last call raised) is returned so the caller can
    decide how to handle it.
    """
    last_exc: Exception | None = None
    last_result: Any = {"error": True, "content": "", "error_message": "no attempt made"}
    for attempt in range(_MAX_MODEL_RETRIES + 1):
        try:
            result = await model_provider.call(
                slot_name=slot_name, messages=messages,
            )
            last_result = result
            if not _model_call_failed(result):
                return result, None  # success
        except Exception as exc:
            last_exc = exc
            last_result = {
                "error": True,
                "content": "",
                "error_message": f"{type(exc).__name__}: {exc}",
            }
        # Brief delay before retry (1s, then 2s) — only if attempts remain.
        if attempt < _MAX_MODEL_RETRIES:
            await asyncio.sleep(1.0 * (attempt + 1))
    return last_result, last_exc


class ExaminerActor:
    """EXAMINER — the probing/quizzing/evaluating mode of Aristotle (ADR-001 §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "EXAMINER" as a persona. Aristotle is the only voice (ADR-001 §1).
    """

    name: str = "examiner"
    cadence: float = 0.0  # manual-only — driven by user turns

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Startup health check — verifies corpus + model reachability."""
        logger = ctx.logger
        container: Any = ctx.container

        corpus_id = "aristotle:textbook"
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning("examiner_corpus_registry_missing")
            return ActorResult(
                ok=False, error="corpus_registry not available on container"
            )

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning("examiner_corpus_not_found corpus=%s", corpus_id)
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning(
                "examiner_corpus_access_failed corpus=%s error=%s", corpus_id, exc
            )
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        model_provider = getattr(container, "model_provider", None)
        model_status = "configured" if model_provider is not None else "NOT_CONFIGURED"
        logger.info("examiner_cycle_ok corpus=%s model=%s", corpus_id, model_status)
        return ActorResult(ok=True)

    # ------------------------------------------------------------------
    # Tutoring methods (called on-demand by the session coordinator)
    # ------------------------------------------------------------------

    async def probe(self, ctx: ActorContext, concept_id: str) -> ActorResult:
        """Generate a low-stakes probe question (ADR-001 §3 PROBE).

        A probe is "tell me in your own words" — not graded, just checks
        the explanation landed. Returns the probe question.

        Governance: if no model provider, returns NEEDS_CONFIGURATION.
        """
        return await self._generate_question(
            ctx,
            concept_id,
            question_type="probe",
            system_prompt=(
                "You are Aristotle. Ask the learner to explain the concept "
                "in their own words. This is a low-stakes probe — warm, "
                "inviting, not intimidating. One question only.\n\n"
                "LENGTH AND REGISTER: Keep it to 1-2 sentences. Plain, "
                "everyday English — no flowery opening ('my dear learner', "
                "'fascinating substances', 'let us delve into'), just ask "
                "the question directly. Many learners are studying in a "
                "second language; short, direct sentences are easier to "
                "parse than long ones with embedded clauses."
            ),
        )

    async def quiz(
        self,
        ctx: ActorContext,
        concept_id: str,
        *,
        question_type: str = "recognition",
    ) -> ActorResult:
        """Generate a quiz question — recognition or transfer (ADR-002 Rev 2 §3 QUIZ).

        Phase B.5: two question types:
          - "recognition" (default): can the learner identify/recall the
            correct answer? Tests whether the concept is known. This is
            the Phase A behavior — a definition check or identification.
          - "transfer": apply the concept to a NEW situation the learner
            hasn't seen. Different prompt — the model produces an
            application scenario, not a definition check. Transfer is
            the hard target; recognition is the gate. A learner who can
            recognize but not transfer hasn't truly mastered the concept.

        The session coordinator selects question_type based on mastery_level:
        recognition for level < 2, transfer for level >= 2 (ADR-002 §3).

        Returns:
            ActorResult(ok=True, data={"question": <str>, "question_type": <str>}).
            Phase B.5 migration: uses data, not error-as-payload. quiz() is
            the third actor migrated (evaluate() + teach() are the reference).

        Governance: if no model provider, returns NEEDS_CONFIGURATION.
        """
        if question_type == "transfer":
            system_prompt = (
                "You are Aristotle. Ask a TRANSFER question — apply this "
                "concept to a NEW situation the learner hasn't seen before. "
                "Do NOT ask for a definition or identification. Instead, "
                "present a novel scenario, problem, or application that "
                "requires the learner to USE the concept to reason through "
                "it. The scenario should be different from any examples in "
                "the textbook passage. One question only."
            )
        else:
            system_prompt = (
                "You are Aristotle. Ask a RECOGNITION question that tests "
                "whether the learner can identify or recall the correct "
                "answer for this concept. The question should match the "
                "concept's Bloom's taxonomy level. One question only."
            )
        return await self._generate_question(
            ctx,
            concept_id,
            question_type=question_type,
            system_prompt=system_prompt,
        )

    async def evaluate(
        self,
        ctx: ActorContext,
        concept_id: str,
        student_answer: str,
        quiz_question: str,
    ) -> ActorResult:
        """Score the student's quiz answer + produce error diagnosis if wrong.

        Phase B.5 (ADR-002 Rev 2 §3 EVALUATE — error diagnosis): when the
        answer is wrong, EXAMINER produces a three-part diagnosis
        (misconception / why_wrong / corrective) instead of just a score.
        When the answer is correct, diagnosis is None and feedback names
        *why* it was right (not just "correct!").

        Calls the model to evaluate the answer. Returns ActorResult with:
        - ok=True: scoring succeeded. `data` field contains a dict with:
            {
              "score": float,           # 0.0-1.0
              "mastery_achieved": bool,  # True if score >= mastery_threshold
              "feedback": str,           # one sentence; names why right/wrong
              "diagnosis": dict | None   # None when correct; dict when wrong
            }
            When diagnosis is a dict, it has:
            {
              "misconception": str,  # what the learner likely thought
              "why_wrong": str,      # jargon-free explanation of the error
              "corrective": str      # one memorable corrective sentence
            }
        - ok=False: scoring failed or model not configured. `error` has the
          reason; `data` is None.

        The score is 0.0-1.0. mastery_achieved is True if score >= the
        config's mastery_threshold (default 0.7). The model is asked to
        populate diagnosis ONLY when mastery_achieved is False.

        Phase B.5 migration: this method uses ActorResult.data (not
        error-as-payload). It is the reference migration for all
        ARISTOTLE actors — predict() and generate_hint() already use data;
        teach()/probe()/quiz() can migrate when next touched.

        Governance: if no model provider, returns NEEDS_CONFIGURATION.
        """
        logger = ctx.logger
        container: Any = ctx.container
        config = ctx.config

        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(
                ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
            )

        # Fetch the concept (for context)
        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(ok=False, error=f"concept {concept_id!r} not found")

        mastery_threshold = getattr(config, "mastery_threshold", 0.7) if config else 0.7

        # Task 23 Fix 1 — decline-to-answer gate. Before calling the model,
        # check whether the student explicitly declined to answer ("no",
        # "i don't know", "teach me about it", etc.). If so, skip the model
        # call entirely and return score=0.0, mastery_achieved=False with a
        # gentle feedback string. This is NOT a grading judgment — it's a
        # "the student didn't attempt, so don't grade" signal.
        #
        # Why this lives HERE (inside evaluate()) and not in the callers
        # (run_placer_step, session.py's quiz-check path): every caller
        # gets the gate for free, and there's no risk of a future caller
        # forgetting to add it. The downstream code already handles
        # mastery_achieved=False correctly (placement advances to the next
        # concept without writing a mastery row; the quiz-check path routes
        # to HINT_1/REMEDIATE), so no caller changes are needed.
        #
        # The gate is intentionally tight (see _is_decline_to_answer's
        # docstring) — false negatives are safe (the model grades a genuine
        # "I don't know" as 0.0), false positives are dangerous (gate fires
        # on a real answer and skips the model). The regression test that
        # matters most: a genuine content answer beginning with "no" (e.g.
        # "no, it binds non-covalently") must NOT trigger the gate.
        if _is_decline_to_answer(student_answer):
            logger.info(
                "examiner_evaluate_declined concept=%s — student declined to answer, skipping model call",
                concept_id,
            )
            return ActorResult(
                ok=True,
                data={
                    "score": 0.0,
                    "mastery_achieved": False,
                    "feedback": (
                        "No problem — let's move on to teaching this one."
                    ),
                    "diagnosis": None,
                },
            )

        # Phase B.5: the prompt now requests a diagnosis block when the
        # answer is wrong. Exact field names + structure — no ambiguity.
        system_prompt = (
            "You are Aristotle. Evaluate the learner's answer to the quiz "
            "question. Respond as JSON with EXACTLY these fields:\n"
            "  score: float (0.0-1.0)\n"
            "  mastery_achieved: bool (true if score >= mastery_threshold)\n"
            "  feedback: str (one sentence; when correct, name WHY it was "
            "right; when wrong, brief acknowledgment)\n"
            "  diagnosis: object or null\n"
            "\n"
            "When mastery_achieved is TRUE: set diagnosis to null. The "
            "feedback should name why the answer was right (e.g. 'Exactly — "
            "you identified that inertia resists the change in motion').\n"
            "\n"
            "When mastery_achieved is FALSE: set diagnosis to an object with "
            "EXACTLY these three fields:\n"
            "  misconception: str — what the learner likely thought (e.g. "
            "'You seem to think a force is needed to sustain motion')\n"
            "  why_wrong: str — jargon-free explanation of why that's wrong "
            "(e.g. 'The issue is that objects keep moving on their own — a "
            "force is only needed to change motion, not sustain it')\n"
            "  corrective: str — one memorable corrective sentence (e.g. "
            "'No force is needed to keep something moving; force changes "
            "motion')\n"
            "\n"
            "Be fair but exact — partial credit for partial understanding. "
            "Be warm — the diagnosis is for the learner, not a gradebook."
        )
        user_prompt = (
            f"Concept: {concept['topic']}\n"
            f"Quiz question: {quiz_question}\n"
            f"Learner's answer: {student_answer}\n"
            f"Mastery threshold: {mastery_threshold}\n"
            f"Respond as JSON:\n"
            f'{{"score": 0.0, "mastery_achieved": false, "feedback": "...", '
            f'"diagnosis": {{"misconception": "...", "why_wrong": "...", '
            f'"corrective": "..."}}}}\n'
            f"(Set diagnosis to null when mastery_achieved is true.)"
        )

        # Task 21 Fix 3: retry on failed/empty model calls BEFORE falling
        # into the score=0.0 fallback. Without this, infrastructure flakiness
        # (429 rate-limit, transient network blip) is indistinguishable from
        # a genuinely-wrong answer — both fall into the parse_failed branch
        # and score the student's answer as 0.0. The retry shape mirrors
        # aristotle/actors/intake.py's `beast`-slot retry loop (max_retries=2,
        # 1s then 2s backoff). Only after retries are exhausted does the
        # call fall through to the score=0.0 fallback path.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            result, retry_exc = await _call_with_retry(
                model_provider, slot_name="evaluation", messages=messages,
            )
        except Exception as exc:
            # _call_with_retry catches exceptions internally, but defend
            # against any unexpected raise from the helper itself.
            logger.warning(
                "examiner_evaluate_failed concept=%s error=%s:%s",
                concept_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

        # Task 21 Fix 3: if the call itself failed (after all retries),
        # surface a failure rather than silently scoring the student 0.0.
        # The caller (session coordinator) can retry the EVALUATE step or
        # route to REMEDIATE without penalizing the learner for infra flakiness.
        if _model_call_failed(result):
            err_msg = (
                result.get("error_message")
                if isinstance(result, dict)
                else "empty content"
            ) or "empty content"
            if retry_exc is not None:
                err_msg = f"{err_msg} (last exc: {type(retry_exc).__name__}: {retry_exc})"
            logger.warning(
                "examiner_evaluate_model_failed concept=%s error=%s",
                concept_id, err_msg,
            )
            return ActorResult(
                ok=False,
                error=f"model call failed or returned empty content: {err_msg}",
            )

        evaluation_text = result.get("content", "")

        # Parse the model's JSON response + re-package as a dict in
        # ActorResult.data. This is the Phase B.5 migration away from
        # error-as-payload: the caller (session coordinator) reads
        # result.data, not result.error.
        #
        # Task 21 Fix 1: strip a leading/trailing ```json ... ``` fence
        # before parsing. The evaluation model sometimes wraps its JSON
        # in a markdown code fence — that's a solvable input-cleaning
        # problem, not a genuinely malformed response. The except branch
        # below now only triggers for true malformed JSON.
        try:
            cleaned_text = _strip_json_fences(evaluation_text)
            eval_data = json.loads(cleaned_text)
            # Normalize: ensure all four fields exist. diagnosis may be
            # null/absent when correct — normalize to None.
            if not isinstance(eval_data, dict):
                raise ValueError("model response is not a JSON object")
            # Task 23 Fix 2 — derive mastery_achieved IN CODE from the
            # parsed score, ignoring whatever boolean the model put in
            # its own JSON. The model's self-report is not verified and
            # can be internally inconsistent (e.g. score=0.3 +
            # mastery_achieved=true) — this was the live failure mode in
            # the placement session that motivated this task: the model
            # self-reported mastery_achieved=true on refusal-style
            # non-content answers, and run_placer_step wrote a permanent
            # aristotle_mastery row (repetitions=3, mastered=1) for
            # concepts the student explicitly said they didn't know.
            #
            # Deriving in code closes the gap: the model's score is still
            # trusted (it's a continuous value the model is good at), but
            # the boolean threshold check is deterministic. If the model
            # returns score=0.9 + mastery_achieved=false, the code
            # overrides to mastery_achieved=true. If it returns score=0.3
            # + mastery_achieved=true, the code overrides to false. The
            # model's mastery_achieved field is effectively ignored.
            parsed_score = float(eval_data.get("score", 0.0))
            normalized = {
                "score": parsed_score,
                "mastery_achieved": parsed_score >= mastery_threshold,
                "feedback": str(eval_data.get("feedback", "")),
                "diagnosis": eval_data.get("diagnosis")
                if eval_data.get("diagnosis") is not None
                else None,
            }
            # If diagnosis is present, ensure it has the three expected
            # keys (defensive — the model may omit one). Missing keys
            # default to empty string so downstream consumers don't
            # KeyError.
            if isinstance(normalized["diagnosis"], dict):
                d = normalized["diagnosis"]
                normalized["diagnosis"] = {
                    "misconception": str(d.get("misconception", "")),
                    "why_wrong": str(d.get("why_wrong", "")),
                    "corrective": str(d.get("corrective", "")),
                }
            elif normalized["diagnosis"] is not None:
                # Model returned a non-dict, non-null diagnosis — normalize to None.
                normalized["diagnosis"] = None

            logger.info(
                "examiner_evaluate_ok concept=%s score=%.2f mastered=%s has_diagnosis=%s",
                concept_id,
                normalized["score"],
                normalized["mastery_achieved"],
                normalized["diagnosis"] is not None,
            )
            return ActorResult(ok=True, data=normalized)
        except (json.JSONDecodeError, ValueError, TypeError) as parse_exc:
            # Model didn't return valid JSON (even after fence-stripping).
            # Return a fallback dict with score=0.0 + a diagnosis noting
            # the parse failure. This keeps the session moving (the
            # coordinator treats score=0.0 as "not mastered" and routes
            # to hints/remediate).
            #
            # Note (Task 21 follow-up): differentiating the fallback
            # message between "infra flakiness" and "genuinely wrong
            # answer" would require touching session.py / the frontend
            # — out of scope for this fix. The retry above already
            # eliminates the infra-flakiness case from this branch.
            logger.warning(
                "examiner_evaluate_parse_failed concept=%s error=%s raw=%s",
                concept_id,
                parse_exc,
                evaluation_text[:200],
            )
            return ActorResult(
                ok=True,
                data={
                    "score": 0.0,
                    "mastery_achieved": False,
                    "feedback": "[parse error — model did not return valid JSON]",
                    "diagnosis": None,
                },
            )

    async def generate_hint(
        self,
        ctx: ActorContext,
        concept_id: str,
        hint_count: int,
    ) -> ActorResult:
        """Generate a graded hint for the current quiz question (ADR-002 Rev 2 §3 HINT ladder).

        The hint ladder has 2 rungs:
          hint_count == 0 (HINT_1): a gentle nudge — points the learner
            toward the right area of the concept without giving the answer.
            Example: "Think about what happens to a passenger when a bus
            brakes suddenly."
          hint_count == 1 (HINT_2): a stronger clue — near-direct, still
            preserves some effort. Gives a key formula or a fill-in-the-blank
            framing. Example: "The answer relates to inertia — objects tend
            to keep doing what they're doing unless a force acts on them.
            What's the specific term?"

        Does NOT give the answer outright — the learner still has to
        articulate it. The hint is generated by the model (evaluation slot)
        using the concept's content + the current quiz question for context.

        Args:
            ctx: ActorContext with container + logger.
            concept_id: the concept being quizzed.
            hint_count: 0 for HINT_1 (first hint), 1 for HINT_2 (second hint).
              Values >= 1 are treated as HINT_2 strength.

        Returns:
            ActorResult with ok=True + data={"hint": <hint text>}.
            ok=False + error if the concept is not found, the model is not
            configured, or the model call fails.
        """
        logger = ctx.logger
        container: Any = ctx.container

        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(
                ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
            )

        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(ok=False, error=f"concept {concept_id!r} not found")

        # Build the hint prompt based on the rung.
        if hint_count == 0:
            system_prompt = (
                "You are Aristotle. The learner just answered a quiz question "
                "incorrectly. Give a GENTLE HINT — a nudge toward the right "
                "area of the concept. Do NOT give the answer. Point them to a "
                "related example, a sub-property, or a perspective they might "
                "have missed. One or two sentences, warm tone."
            )
        else:
            # hint_count >= 1 → HINT_2: stronger clue.
            system_prompt = (
                "You are Aristotle. The learner answered incorrectly again, "
                "even after the first hint. Give a STRONGER HINT — a "
                "near-direct clue that still preserves some effort. You may "
                "name the key formula, principle, or term, but frame it so "
                "the learner still has to articulate the full answer. One to "
                "three sentences, warm tone."
            )

        user_prompt = (
            f"Concept: {concept['topic']}\nSubtopic: {concept.get('subtopic', 'n/a')}\n"
        )
        if concept.get("content_primary"):
            user_prompt += f"Textbook passage:\n{concept['content_primary'][:500]}\n"
        user_prompt += (
            f"\nHint rung: {hint_count + 1} of 2.\n"
            f"Give a {'gentle nudge' if hint_count == 0 else 'stronger clue'}. "
            f"Do NOT state the answer outright."
        )

        try:
            result = await model_provider.call(
                slot_name="evaluation",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            hint_text = result.get("content", "")
            logger.info(
                "examiner_generate_hint_ok concept=%s hint_count=%d hint_len=%d",
                concept_id,
                hint_count,
                len(hint_text),
            )
            # Phase B.5: use the new data field, not error-as-payload.
            return ActorResult(ok=True, data={"hint": hint_text})
        except Exception as exc:
            logger.warning(
                "examiner_generate_hint_failed concept=%s hint_count=%d error=%s:%s",
                concept_id,
                hint_count,
                type(exc).__name__,
                exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_question(
        self,
        ctx: ActorContext,
        concept_id: str,
        *,
        question_type: str,
        system_prompt: str,
    ) -> ActorResult:
        """Generate a probe or quiz question via the model provider."""
        logger = ctx.logger
        container: Any = ctx.container

        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(
                ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
            )

        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(ok=False, error=f"concept {concept_id!r} not found")

        user_prompt = (
            f"Concept: {concept['topic']}\n"
            f"Subtopic: {concept.get('subtopic', 'n/a')}\n"
            f"Bloom level: {concept.get('bloom_target', 3)}\n"
        )
        if concept.get("content_primary"):
            user_prompt += f"Textbook passage:\n{concept['content_primary'][:500]}\n"

        # Task 21 Fix 2: wrap the model call with the same retry-with-backoff
        # pattern used in aristotle/actors/intake.py for the `beast`-slot
        # call. max_retries=2, sleep 1.0 * (attempt + 1) seconds between
        # attempts. Also: model_provider.call() does NOT raise on API
        # failures (429, 5xx) — it returns {"error": True, "content": ""}.
        # The previous code only caught raised exceptions, so a rate-limited
        # call fell through to `question = result.get("content", "")` →
        # empty string → logged as `examiner_probe_ok ... question_len=0`
        # (success!) instead of a failure. Now we inspect result.error /
        # empty content (matching ModelSlotResolver's `primary_failed`
        # pattern) and return ok=False with a clear error message.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            result, retry_exc = await _call_with_retry(
                model_provider, slot_name="evaluation", messages=messages,
            )
        except Exception as exc:
            # _call_with_retry catches exceptions internally, but defend
            # against any unexpected raise from the helper itself.
            logger.warning(
                "examiner_%s_failed concept=%s error=%s:%s",
                question_type, concept_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

        # Task 21 Fix 2: inspect the result for failure BEFORE logging
        # success. This is the canonical pattern from ModelSlotResolver —
        # `result.error` truthy OR content missing/whitespace-only.
        if _model_call_failed(result):
            err_msg = (
                result.get("error_message")
                if isinstance(result, dict)
                else "empty content"
            ) or "empty content"
            if retry_exc is not None:
                err_msg = f"{err_msg} (last exc: {type(retry_exc).__name__}: {retry_exc})"
            logger.warning(
                "examiner_%s_failed concept=%s error=%s",
                question_type, concept_id, err_msg,
            )
            return ActorResult(
                ok=False,
                error=f"model call failed or returned empty content: {err_msg}",
            )

        question = result.get("content", "")
        logger.info(
            "examiner_%s_ok concept=%s question_type=%s question_len=%d",
            question_type,
            concept_id,
            question_type,
            len(question),
        )
        # Phase B.5: use the data field, not error-as-payload.
        # This migrates both quiz() AND probe() (which share this helper).
        return ActorResult(
            ok=True,
            data={"question": question, "question_type": question_type},
        )

    async def _fetch_concept(self, ctx: ActorContext, concept_id: str) -> dict | None:
        """Fetch a concept from the aristotle_concept table."""
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return None

        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn
            cur = await conn.execute(
                "SELECT id, topic, subtopic, content_primary, content_alt, "
                "content_alt_lang, prerequisite_concept_id, bloom_target "
                "FROM aristotle_concept WHERE id = ?",
                (concept_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                return None
            return {
                "id": row[0],
                "topic": row[1],
                "subtopic": row[2],
                "content_primary": row[3],
                "content_alt": row[4],
                "content_alt_lang": row[5],
                "prerequisite_concept_id": row[6],
                "bloom_target": row[7],
            }
        except Exception:
            return None

    def health(self) -> dict:
        """Health snapshot for the health surface (ADR-014 §7)."""
        return {
            "state": "active",
            "name": self.name,
            "cadence": self.cadence,
            "mode": "manual-only",
            "last_run": None,
            "error_count": 0,
        }
