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
        """Score the student's quiz answer (ADR-001 §3 EVALUATE).

        Calls the model to evaluate the answer. Returns ActorResult with:
        - ok=True: scoring succeeded. `error` field contains a JSON string
          with {score, mastery_achieved, feedback}.
        - ok=False: scoring failed or model not configured.

        The score is 0.0-1.0. mastery_achieved is True if score >= the
        config's mastery_threshold (default 0.7).

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

        system_prompt = (
            "You are Aristotle. Evaluate the learner's answer to the quiz "
            "question. Respond as JSON with fields: score (0.0-1.0), "
            "mastery_achieved (bool), feedback (one sentence). Be fair but "
            "exact — partial credit for partial understanding."
        )
        user_prompt = (
            f"Concept: {concept['topic']}\n"
            f"Quiz question: {quiz_question}\n"
            f"Learner's answer: {student_answer}\n"
            f"Mastery threshold: {mastery_threshold}\n"
            f"Respond as JSON: {{\"score\": 0.0, \"mastery_achieved\": false, \"feedback\": \"...\"}}"
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
            logger.info(
                "examiner_evaluate_ok concept=%s response_len=%d",
                concept_id, len(evaluation_text),
            )
            return ActorResult(ok=True, error=evaluation_text)
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
