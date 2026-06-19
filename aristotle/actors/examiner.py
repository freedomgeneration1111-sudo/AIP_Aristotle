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

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


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
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning("examiner_corpus_not_found corpus=%s", corpus_id)
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning("examiner_corpus_access_failed corpus=%s error=%s", corpus_id, exc)
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
            ctx, concept_id, question_type="probe",
            system_prompt=(
                "You are Aristotle. Ask the learner to explain the concept "
                "in their own words. This is a low-stakes probe — warm, "
                "inviting, not intimidating. One question only."
            ),
        )

    async def quiz(self, ctx: ActorContext, concept_id: str) -> ActorResult:
        """Generate a real quiz question (ADR-001 §3 QUIZ).

        A quiz is a real question at the concept's bloom_target level.
        Returns the quiz question. The student's answer is scored by
        `evaluate()`.

        Governance: if no model provider, returns NEEDS_CONFIGURATION.
        """
        return await self._generate_question(
            ctx, concept_id, question_type="quiz",
            system_prompt=(
                "You are Aristotle. Ask a real question that tests whether "
                "the learner has mastered this concept. The question should "
                "match the concept's Bloom's taxonomy level. One question only."
            ),
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
            return ActorResult(ok=False, error="NEEDS_CONFIGURATION: model_provider not available")

        # Fetch the concept (for context)
        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(ok=False, error=f"concept {concept_id!r} not found")

        mastery_threshold = getattr(config, "mastery_threshold", 0.7) if config else 0.7

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

        try:
            result = await model_provider.call(
                slot_name="evaluation",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            evaluation_text = result.get("content", "")

            # Parse the model's JSON response + re-package as a dict in
            # ActorResult.data. This is the Phase B.5 migration away from
            # error-as-payload: the caller (session coordinator) reads
            # result.data, not result.error.
            import json
            try:
                eval_data = json.loads(evaluation_text)
                # Normalize: ensure all four fields exist. diagnosis may be
                # null/absent when correct — normalize to None.
                if not isinstance(eval_data, dict):
                    raise ValueError("model response is not a JSON object")
                normalized = {
                    "score": float(eval_data.get("score", 0.0)),
                    "mastery_achieved": bool(eval_data.get("mastery_achieved", False)),
                    "feedback": str(eval_data.get("feedback", "")),
                    "diagnosis": eval_data.get("diagnosis") if eval_data.get("diagnosis") is not None else None,
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
                    concept_id, normalized["score"], normalized["mastery_achieved"],
                    normalized["diagnosis"] is not None,
                )
                return ActorResult(ok=True, data=normalized)
            except (json.JSONDecodeError, ValueError, TypeError) as parse_exc:
                # Model didn't return valid JSON. Return a fallback dict
                # with score=0.0 + a diagnosis noting the parse failure.
                # This keeps the session moving (the coordinator treats
                # score=0.0 as "not mastered" and routes to hints/remediate).
                logger.warning(
                    "examiner_evaluate_parse_failed concept=%s error=%s raw=%s",
                    concept_id, parse_exc, evaluation_text[:200],
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
        except Exception as exc:
            logger.warning(
                "examiner_evaluate_failed concept=%s error=%s:%s",
                concept_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

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
            return ActorResult(ok=False, error="NEEDS_CONFIGURATION: model_provider not available")

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
            f"Concept: {concept['topic']}\n"
            f"Subtopic: {concept.get('subtopic', 'n/a')}\n"
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
                concept_id, hint_count, len(hint_text),
            )
            # Phase B.5: use the new data field, not error-as-payload.
            return ActorResult(ok=True, data={"hint": hint_text})
        except Exception as exc:
            logger.warning(
                "examiner_generate_hint_failed concept=%s hint_count=%d error=%s:%s",
                concept_id, hint_count, type(exc).__name__, exc,
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
            return ActorResult(ok=False, error="NEEDS_CONFIGURATION: model_provider not available")

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

        try:
            result = await model_provider.call(
                slot_name="evaluation",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            question = result.get("content", "")
            logger.info(
                "examiner_%s_ok concept=%s question_len=%d",
                question_type, concept_id, len(question),
            )
            return ActorResult(ok=True, error=question)
        except Exception as exc:
            logger.warning(
                "examiner_%s_failed concept=%s error=%s:%s",
                question_type, concept_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

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
