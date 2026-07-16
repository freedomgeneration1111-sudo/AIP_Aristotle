"""SOCRATES actor — ADR-001 §2.

Role: Teach / explain / re-explain. Pulls the passage + alternate framings
from the textbook corpus when the first explanation misses.

Phase A: the actor has two faces:
  1. `run_cycle(ctx)` — the startup health check (verifies corpus reachability).
     Called by the host's scheduler once on start, then waits for cancellation.
  2. `teach(ctx, concept_id)` — the real tutoring method. Calls the model
     provider to generate an explanation of the concept. Called on-demand
     by the session coordinator (or API route, or GUI callback).

The tutoring state machine (TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE) is
actor-driven, not workflow-driven. The workflow YAML (tutoring_session_v1.yaml)
documents the state machine, but the platform's ScriptNode is disabled in
production (platform gap — logged in TECH_DEBT). The actors handle the
transitions directly.

Layer: imports from aip.foundation.protocols.actors only (ActorResult,
ActorContext). The container is accessed via ctx.container (duck-typed).
"""

from __future__ import annotations

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class SocratesActor:
    """SOCRATES — the teaching mode of Aristotle (ADR-001 §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only:
    the host's scheduler runs one cycle on start, then waits for cancellation.
    The tutoring state machine is driven by user turns, not by a timer.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "SOCRATES" as a persona. Aristotle is the only voice (ADR-001 §1).
    """

    name: str = "socrates"
    cadence: float = 0.0  # manual-only — driven by user turns, not a timer

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Startup health check — verifies corpus + model reachability.

        Called by the host's scheduler once on start. The real tutoring
        happens via `teach()`, not `run_cycle()`.
        """
        logger = ctx.logger
        container: Any = ctx.container

        # Verify the aristotle:textbook corpus is registered
        corpus_id = "aristotle:textbook"
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning("socrates_corpus_registry_missing")
            return ActorResult(
                ok=False, error="corpus_registry not available on container"
            )

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning("socrates_corpus_not_found corpus=%s", corpus_id)
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning(
                "socrates_corpus_access_failed corpus=%s error=%s", corpus_id, exc
            )
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        # Check model availability
        model_provider = getattr(container, "model_provider", None)
        model_status = "configured" if model_provider is not None else "NOT_CONFIGURED"
        logger.info(
            "socrates_cycle_ok corpus=%s model=%s — ready for tutoring",
            corpus_id,
            model_status,
        )
        return ActorResult(ok=True)

    async def teach(
        self,
        ctx: ActorContext,
        concept_id: str,
        *,
        retry: bool = False,
        struggle_pattern: str | None = None,
        mastery_level: int = 0,
    ) -> ActorResult:
        """Generate an explanation for a concept (ADR-001 §3 TEACH + ADR-002 Rev 2 §3/§4).

        Phase B.5 (faded worked examples): the prompt adapts to mastery_level:
          - level 0 (new concept): full worked example + explanation.
          - level 1-2 (early mastery): partial faded example — set up the
            problem, work through the first step, leave the final step for
            the learner to complete.
          - level 3+ (near-mastered): conceptual explanation only, no
            worked example. Focus on depth and nuance.

        mastery_level maps to the `repetitions` column on aristotle_mastery
        (consecutive correct reviews — the SM-2 counter). 0 = new, 1-2 =
        early, 3+ = established. Default 0 when no mastery row exists yet.

        Args:
            ctx: ActorContext with container + config + logger.
            concept_id: the concept to explain (must exist in aristotle_concept).
            retry: if True, this is a re-teaching after REMEDIATE. Use a
                different framing informed by the struggle_pattern.
            struggle_pattern: the student's diagnostic sentence (from MENTOR).
                Injected into the prompt when retry=True so the re-teaching
                addresses the specific gap.
            mastery_level: 0/1/2/3+ — controls the fading mode.

        Returns:
            ActorResult with ok=True + data={"explanation": <str>,
            "fading_mode": <str>}. Phase B.5 migration: uses the data
            field, not error-as-payload. evaluate() is the reference
            migration; teach() is the second actor migrated.

        Governance: if no model provider is configured, returns
        NEEDS_CONFIGURATION (never a placeholder explanation).
        """
        logger = ctx.logger
        container: Any = ctx.container
        config = ctx.config

        # Governance: no silent model calls (AGENTS.md §1.7)
        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(
                ok=False, error="NEEDS_CONFIGURATION: model_provider not available"
            )

        # Pull the concept from the textbook corpus (if ingested)
        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(
                ok=False, error=f"concept {concept_id!r} not found in aristotle_concept"
            )

        # Build the teaching prompt
        primary_lang = getattr(config, "primary_language", "en") if config else "en"
        alt_lang = getattr(config, "alt_language", "ur") if config else "ur"

        system_prompt = self._build_system_prompt(
            retry=retry, mastery_level=mastery_level
        )
        user_prompt = self._build_teach_prompt(
            concept=concept,
            retry=retry,
            struggle_pattern=struggle_pattern,
            primary_lang=primary_lang,
            alt_lang=alt_lang,
            mastery_level=mastery_level,
        )

        # Call the model (beast slot for explanation generation)
        try:
            result = await model_provider.call(
                slot_name="beast",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            explanation = result.get("content", "")
            fading_mode = self._fading_mode_for_level(mastery_level)
            logger.info(
                "socrates_teach_ok concept=%s retry=%s mastery_level=%d fading=%s explanation_len=%d",
                concept_id,
                retry,
                mastery_level,
                fading_mode,
                len(explanation),
            )
            # Phase B.5: use the data field, not error-as-payload.
            return ActorResult(
                ok=True,
                data={"explanation": explanation, "fading_mode": fading_mode},
            )
        except Exception as exc:
            logger.warning(
                "socrates_teach_failed concept=%s error=%s:%s",
                concept_id,
                type(exc).__name__,
                exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

    async def predict(
        self,
        ctx: ActorContext,
        concept_id: str,
    ) -> ActorResult:
        """Generate the pre-teach prediction prompt (ADR-002 Rev 2 §3 PREDICT).

        The generation effect: asking the learner to guess before teaching
        improves retention — regardless of whether the guess was right or
        wrong. This method fetches the concept's name/topic from the corpus
        and builds a warm, low-pressure prompt asking the learner to predict
        what the concept means.

        Does NOT evaluate correctness — the prediction is always accepted.
        Does NOT call a model — the prompt is a fixed template with the
        concept name interpolated in. No model_provider needed (unlike
        teach()).

        Uses ActorResult(ok=True, data={"prompt": ...}) — the new `data`
        field (Brain commit ce44e53, DEFINER decision ADR-002 §16 #4).
        The session coordinator reads result.data["prompt"] to display to
        the learner.

        Args:
            ctx: ActorContext with container + logger.
            concept_id: the concept to ask about (must exist in
                aristotle_concept).

        Returns:
            ActorResult with ok=True + data={"prompt": <warm prompt str>}.
            ok=False + error if the concept is not found or the corpus is
            unreachable.
        """
        logger = ctx.logger

        # Fetch the concept (same query pattern as teach()).
        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(
                ok=False,
                error=f"concept {concept_id!r} not found in aristotle_concept",
            )

        # Build the warm, low-pressure prediction prompt. The framing is
        # deliberate: "a wrong guess is fine" reduces affective filter and
        # encourages engagement. The concept name (topic) is interpolated
        # so the prompt is specific, not generic.
        topic = concept.get("topic", concept_id)
        prompt = (
            f'Before I explain this, what do you think "{topic}" means? '
            f"A wrong guess is fine — just say what comes to mind."
        )

        logger.info(
            "socrates_predict_ok concept=%s prompt_len=%d",
            concept_id,
            len(prompt),
        )
        # Phase B.5: use the new `data` field, not error-as-payload.
        return ActorResult(ok=True, data={"prompt": prompt})

    async def _fetch_concept(self, ctx: ActorContext, concept_id: str) -> dict | None:
        """Fetch a concept from the aristotle_concept table.

        Returns a dict with: id, topic, subtopic, content_primary, content_alt,
        content_alt_lang, prerequisite_concept_id, bloom_target. Returns None
        if not found.
        """
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

    def _fading_mode_for_level(self, mastery_level: int) -> str:
        """Return the fading mode name for a mastery level (ADR-002 §4).

        - level 0 → "full_worked_example"
        - level 1-2 → "partial_faded_example"
        - level 3+ → "conceptual_only"
        """
        if mastery_level <= 0:
            return "full_worked_example"
        elif mastery_level <= 2:
            return "partial_faded_example"
        else:
            return "conceptual_only"

    def _build_system_prompt(
        self, *, retry: bool = False, mastery_level: int = 0
    ) -> str:
        """Build the system prompt for the teaching model.

        ADR-001 §1: single-voice principle. The system prompt establishes
        Aristotle's voice — patient, clear, one tutor. The mode (teach vs
        re-teach) is an internal distinction; the learner just hears
        Aristotle explaining.

        Phase B.5 (faded worked examples): the system prompt includes a
        mastery-adaptive instruction block that tells the model which
        presentation mode to use (full example / partial faded / conceptual
        only). This keeps the fading deterministic — exact instruction for
        each level, no ambiguity about which mode applies.

        Task 21 Fix 4 (length ceiling + plain-language register): the base
        prompt now carries an explicit length + register constraint that
        applies across ALL fading modes. Without it, the `full_worked_example`
        mode's "Show every step — do not skip anything" instruction had no
        counterweight and produced ~5000-character single explanations in
        production. The fading-mode branches were also softened so
        "completeness of steps" doesn't bleed into "verbosity per step".
        """
        base = (
            "You are Aristotle — a patient, exact tutor. You explain concepts "
            "clearly, with examples. You speak in one voice: warm but precise. "
            "The learner trusts you because you never rush past confusion."
        )
        # Task 21 Fix 4: explicit length + register constraint, applied
        # across ALL fading modes. The plain-language framing is important
        # — many learners are studying in a second language, so plain
        # everyday English + short sentences is required, not optional.
        # This is a real, testable constraint: a unit test asserts the
        # string presence so a future refactor can't silently drop it.
        base += (
            "\n\nLENGTH AND REGISTER: Keep explanations short — 2 to 4 short "
            "paragraphs maximum, even when the concept is complex. Use plain, "
            "everyday English; many learners are studying in a second "
            "language. Prefer short sentences over long ones. Do not pad. "
            "Do not repeat yourself. When walking through multiple steps, "
            "keep each step to one or two sentences — completeness of steps, "
            "not verbosity per step."
        )
        if retry:
            base += (
                "\n\nThis is a re-teaching. The learner struggled with your "
                "first explanation. Try a different angle — simpler words, a "
                "concrete example, an analogy. Address the specific gap noted "
                "in the struggle pattern. Keep the length ceiling above — a "
                "re-teaching is not a longer explanation, it is a different one."
            )

        # Phase B.5: mastery-adaptive fading instruction.
        # Task 21 Fix 4: the `full_worked_example` branch was softened from
        # "Show every step — do not skip anything" (no counterweight →
        # ~5000-char explanations in production) to "Show every step, but
        # keep each step to 1-2 sentences". The completeness-of-steps
        # instruction is preserved; only the per-step verbosity is bounded.
        fading_mode = self._fading_mode_for_level(mastery_level)
        if fading_mode == "full_worked_example":
            base += (
                "\n\nFADING MODE: full worked example. The learner is new to "
                "this concept. Walk through a complete step-by-step example "
                "showing exactly how this concept applies. Show every step, "
                "but keep each step to 1-2 sentences — completeness of steps, "
                "not verbosity per step. The learner needs to see the full "
                "reasoning path before they can attempt it themselves, but a "
                "wall of text per step is harder to follow, not easier."
            )
        elif fading_mode == "partial_faded_example":
            base += (
                "\n\nFADING MODE: partial faded example. The learner has seen "
                "this concept before and has early mastery. Set up a problem, "
                "work through the first step explicitly, then STOP before the "
                "final step. Clearly mark where the learner should complete it "
                "(e.g. 'Now you try the last step: ...'). Do not give the "
                "final answer — let the learner finish. This is the faded "
                "worked-example technique: the learner practices the hardest "
                "part while still seeing the setup."
            )
        else:  # conceptual_only
            base += (
                "\n\nFADING MODE: conceptual explanation only. The learner "
                "has near-mastered this concept — they have seen it multiple "
                "times. Do NOT include a worked example. Focus on depth and "
                "nuance: edge cases, common misconceptions, connections to "
                "other concepts, the 'why' behind the mechanics. The learner "
                "already knows the 'how'; give them the 'why'."
            )
        return base

    def _build_teach_prompt(
        self,
        *,
        concept: dict,
        retry: bool,
        struggle_pattern: str | None,
        primary_lang: str,
        alt_lang: str,
        mastery_level: int = 0,
    ) -> str:
        """Build the user prompt for the teaching model.

        Includes the concept content from the textbook corpus (if ingested)
        + the struggle_pattern (if retry). Requests bilingual output per
        ADR-001 §7.

        Phase B.5: the user prompt echoes the fading mode so the model has
        a second deterministic cue (the system prompt has the primary
        instruction; this reinforces it in the user turn).
        """
        parts = [f"Explain the concept: {concept['topic']}"]
        if concept.get("subtopic"):
            parts.append(f"Subtopic: {concept['subtopic']}")

        # Include the textbook passage (if ingested)
        if concept.get("content_primary"):
            parts.append(f"\nTextbook passage:\n{concept['content_primary']}")
        if concept.get("content_alt"):
            parts.append(
                f"\nAlt-language passage ({concept.get('content_alt_lang', alt_lang)}):\n{concept['content_alt']}"
            )

        if retry and struggle_pattern:
            parts.append(f"\nThe learner's struggle pattern: {struggle_pattern}")
            parts.append("Address this specific gap in your re-teaching.")

        # Phase B.5: echo the fading mode in the user prompt.
        fading_mode = self._fading_mode_for_level(mastery_level)
        parts.append(f"\nMastery level: {mastery_level} (fading mode: {fading_mode}).")
        parts.append(
            "Follow the FADING MODE instruction from the system prompt exactly."
        )

        parts.append(f"\nProvide the explanation in {primary_lang} (primary).")
        parts.append(f"If possible, also provide a {alt_lang} translation.")
        return "\n".join(parts)

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
