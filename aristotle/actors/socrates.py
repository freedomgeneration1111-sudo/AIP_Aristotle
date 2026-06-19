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
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            stores = await registry.get_stores(corpus_id)
            if stores is None:
                logger.warning("socrates_corpus_not_found corpus=%s", corpus_id)
                return ActorResult(ok=False, error=f"corpus {corpus_id!r} not found")
        except Exception as exc:
            logger.warning("socrates_corpus_access_failed corpus=%s error=%s", corpus_id, exc)
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        # Check model availability
        model_provider = getattr(container, "model_provider", None)
        model_status = "configured" if model_provider is not None else "NOT_CONFIGURED"
        logger.info(
            "socrates_cycle_ok corpus=%s model=%s — ready for tutoring",
            corpus_id, model_status,
        )
        return ActorResult(ok=True)

    async def teach(
        self,
        ctx: ActorContext,
        concept_id: str,
        *,
        retry: bool = False,
        struggle_pattern: str | None = None,
    ) -> ActorResult:
        """Generate an explanation for a concept (ADR-001 §3 TEACH).

        Args:
            ctx: ActorContext with container + config + logger.
            concept_id: the concept to explain (must exist in aristotle_concept).
            retry: if True, this is a re-teaching after REMEDIATE. Use a
                different framing informed by the struggle_pattern.
            struggle_pattern: the student's diagnostic sentence (from MENTOR).
                Injected into the prompt when retry=True so the re-teaching
                addresses the specific gap.

        Returns:
            ActorResult with ok=True + the explanation in `error` field
            (re-purposed as the result payload since ActorResult has no
            `data` field — a future Protocol revision should add one).

        Governance: if no model provider is configured, returns
        NEEDS_CONFIGURATION (never a placeholder explanation).
        """
        logger = ctx.logger
        container: Any = ctx.container
        config = ctx.config

        # Governance: no silent model calls (AGENTS.md §1.7)
        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(ok=False, error="NEEDS_CONFIGURATION: model_provider not available")

        # Pull the concept from the textbook corpus (if ingested)
        concept = await self._fetch_concept(ctx, concept_id)
        if concept is None:
            return ActorResult(ok=False, error=f"concept {concept_id!r} not found in aristotle_concept")

        # Build the teaching prompt
        primary_lang = getattr(config, "primary_language", "en") if config else "en"
        alt_lang = getattr(config, "alt_language", "ur") if config else "ur"

        system_prompt = self._build_system_prompt(retry=retry)
        user_prompt = self._build_teach_prompt(
            concept=concept,
            retry=retry,
            struggle_pattern=struggle_pattern,
            primary_lang=primary_lang,
            alt_lang=alt_lang,
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
            logger.info(
                "socrates_teach_ok concept=%s retry=%s explanation_len=%d",
                concept_id, retry, len(explanation),
            )
            # ActorResult has no data field — use `error` as the payload.
            # This is a known Protocol limitation; future revision should add
            # a `data: Any` field to ActorResult.
            return ActorResult(ok=True, error=explanation)
        except Exception as exc:
            logger.warning(
                "socrates_teach_failed concept=%s error=%s:%s",
                concept_id, type(exc).__name__, exc,
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
            f"Before I explain this, what do you think \"{topic}\" means? "
            f"A wrong guess is fine — just say what comes to mind."
        )

        logger.info(
            "socrates_predict_ok concept=%s prompt_len=%d",
            concept_id, len(prompt),
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

    def _build_system_prompt(self, *, retry: bool = False) -> str:
        """Build the system prompt for the teaching model.

        ADR-001 §1: single-voice principle. The system prompt establishes
        Aristotle's voice — patient, clear, one tutor. The mode (teach vs
        re-teach) is an internal distinction; the learner just hears
        Aristotle explaining.
        """
        base = (
            "You are Aristotle — a patient, exact tutor. You explain concepts "
            "clearly, with examples. You speak in one voice: warm but precise. "
            "The learner trusts you because you never rush past confusion."
        )
        if retry:
            base += (
                "\n\nThis is a re-teaching. The learner struggled with your "
                "first explanation. Try a different angle — simpler words, a "
                "concrete example, an analogy. Address the specific gap noted "
                "in the struggle pattern."
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
    ) -> str:
        """Build the user prompt for the teaching model.

        Includes the concept content from the textbook corpus (if ingested)
        + the struggle_pattern (if retry). Requests bilingual output per
        ADR-001 §7.
        """
        parts = [f"Explain the concept: {concept['topic']}"]
        if concept.get("subtopic"):
            parts.append(f"Subtopic: {concept['subtopic']}")

        # Include the textbook passage (if ingested)
        if concept.get("content_primary"):
            parts.append(f"\nTextbook passage:\n{concept['content_primary']}")
        if concept.get("content_alt"):
            parts.append(f"\nAlt-language passage ({concept.get('content_alt_lang', alt_lang)}):\n{concept['content_alt']}")

        if retry and struggle_pattern:
            parts.append(f"\nThe learner's struggle pattern: {struggle_pattern}")
            parts.append("Address this specific gap in your re-teaching.")

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
