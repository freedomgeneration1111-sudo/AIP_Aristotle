"""MENTOR actor — ADR-001 §2.

Role: Track the long arc. Mastery per concept + the `struggle_pattern`
field (one persistent AI-written diagnostic sentence per student — the
tutor's memory of *who this learner is*; feeds every REMEDIATE prompt).

Phase A: the actor has two faces:
  1. `run_cycle(ctx)` — the startup health check. Reads/initializes the
     struggle_pattern. Called by the host's scheduler once on start.
  2. `update_struggle_pattern(ctx, concept_id, evaluation_result)` — the
     real tutoring method. Calls the model to write a new diagnostic
     sentence based on the student's recent EVALUATE result, then UPDATEs
     the table. Called on-demand by the session coordinator after EVALUATE.
  3. `get_struggle_pattern(ctx, student_id)` — reads the current
     struggle_pattern. Called by SOCRATES before REMEDIATE to inject the
     pattern into the re-teaching prompt.

Layer: imports from aip.foundation.protocols.actors only (ActorResult,
ActorContext). The container is accessed via ctx.container (duck-typed).
SQL is executed via the corpus's write connection
(stores.connection_manager.write_conn).
"""
from __future__ import annotations

from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


class MentorActor:
    """MENTOR — the long-arc tracking mode of Aristotle (ADR-001 §2).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only.
    Reads/writes the aristotle_struggle_pattern table in the
    aristotle:textbook corpus.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "MENTOR" as a persona. Aristotle is the only voice (ADR-001 §1).
    """

    name: str = "mentor"
    cadence: float = 0.0  # manual-only — driven by user turns

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Startup health check — reads/initializes struggle_pattern."""
        logger = ctx.logger
        container: Any = ctx.container

        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            logger.warning("mentor_corpus_registry_missing")
            return ActorResult(ok=False, error="corpus_registry not available on container")

        try:
            stores = await registry.get_stores("aristotle:textbook")
            if stores is None:
                logger.warning("mentor_corpus_not_found")
                return ActorResult(ok=False, error="corpus aristotle:textbook not found")
        except Exception as exc:
            logger.warning("mentor_corpus_access_failed error=%s", exc)
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")

        # Read the current struggle_pattern for the default student
        pattern = await self._read_struggle_pattern(ctx, "definer")
        if pattern is None:
            # Initialize with a placeholder
            placeholder = (
                "No struggles recorded yet — the tutor is still learning "
                "who this learner is."
            )
            await self._write_struggle_pattern(ctx, "definer", placeholder)
            logger.info("mentor_struggle_pattern_initialized student=definer")
        else:
            logger.info(
                "mentor_struggle_pattern_read student=definer pattern=%s",
                pattern[:80] + "..." if len(pattern) > 80 else pattern,
            )
        return ActorResult(ok=True)

    # ------------------------------------------------------------------
    # Tutoring methods (called on-demand by the session coordinator)
    # ------------------------------------------------------------------

    async def get_struggle_pattern(
        self,
        ctx: ActorContext,
        student_id: str = "definer",
    ) -> str | None:
        """Read the current struggle_pattern for a student.

        Called by SOCRATES before REMEDIATE to inject the pattern into the
        re-teaching prompt (ADR-001 §2: "feeds every REMEDIATE prompt").

        Returns the pattern text, or None if not found.
        """
        return await self._read_struggle_pattern(ctx, student_id)

    async def update_struggle_pattern(
        self,
        ctx: ActorContext,
        concept_id: str,
        evaluation_result: str,
        student_id: str = "definer",
    ) -> ActorResult:
        """Write a new AI-diagnostic struggle_pattern sentence (ADR-001 §2 MENTOR).

        Called by the session coordinator after EVALUATE. Calls the model
        (sexton slot) to write a new diagnostic sentence based on:
        - The current struggle_pattern (if any)
        - The concept just evaluated
        - The evaluation result (score + feedback)

        The new sentence is UPDATEd into aristotle_struggle_pattern.

        Governance: if no model provider, returns NEEDS_CONFIGURATION.
        The existing struggle_pattern is NOT overwritten (preserves the
        last known good diagnostic).
        """
        logger = ctx.logger
        container: Any = ctx.container

        model_provider = getattr(container, "model_provider", None)
        if model_provider is None:
            return ActorResult(ok=False, error="NEEDS_CONFIGURATION: model_provider not available")

        # Read the current pattern (to build on it, not replace blindly)
        current_pattern = await self._read_struggle_pattern(ctx, student_id)
        if current_pattern is None:
            current_pattern = "No previous struggles recorded."

        system_prompt = (
            "You are Aristotle's MENTOR mode — the tutor's internal memory "
            "of who this learner is. Write ONE diagnostic sentence that "
            "captures the learner's pattern of struggle. Be specific and "
            "actionable — the sentence feeds every re-teaching prompt. "
            "One sentence only. No preamble."
        )
        user_prompt = (
            f"Current struggle pattern: {current_pattern}\n"
            f"Concept just evaluated: {concept_id}\n"
            f"Evaluation result: {evaluation_result}\n"
            f"Write the updated struggle pattern (one sentence):"
        )

        try:
            result = await model_provider.call(
                slot_name="sexton",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            new_pattern = result.get("content", "").strip()
            if not new_pattern:
                return ActorResult(ok=False, error="model returned empty struggle_pattern")

            # Persist the new pattern
            await self._write_struggle_pattern(ctx, student_id, new_pattern)
            logger.info(
                "mentor_struggle_pattern_updated student=%s concept=%s pattern=%s",
                student_id, concept_id,
                new_pattern[:80] + "..." if len(new_pattern) > 80 else new_pattern,
            )
            return ActorResult(ok=True, error=new_pattern)
        except Exception as exc:
            logger.warning(
                "mentor_struggle_pattern_update_failed student=%s error=%s:%s",
                student_id, type(exc).__name__, exc,
            )
            return ActorResult(ok=False, error=f"model call failed: {exc}")

    async def log_misconception(
        self,
        ctx: ActorContext,
        concept_id: str,
        session_id: str,
        diagnosis: dict,
        student_id: str = "definer",
    ) -> ActorResult:
        """Write one row to aristotle_misconception_log (ADR-002 Rev 2 §7, B.5 item 7).

        Called by the session coordinator after a wrong EVALUATE step (when
        diagnosis is present). This is the instance-level misconception
        tracking that complements the existing struggle_pattern (which is
        the long-arc summary). The log builds a queryable history of
        specific misconceptions per student per concept — after 3+ entries
        with similar misconception_text, MENTOR can update struggle_pattern
        to explicitly name the pattern (ADR-002 §7 pattern recognition —
        future work, not in this commit).

        Best-effort: returns ActorResult(ok=True) even if the DB write
        fails. A misconception-log failure must NEVER break the session —
        the learner's progress through the tutoring loop is more important
        than the analytics row. The error is logged at WARNING for
        observability.

        Args:
            ctx: ActorContext with container + logger.
            concept_id: the concept the learner struggled with.
            session_id: the session identifier (derived by the coordinator
                from student_id:concept_id:timestamp when no real session
                table exists — Phase D will provide a real session_id).
            diagnosis: the diagnosis dict from EXAMINER.evaluate(). Must
                have "misconception" and "corrective" keys. The "why_wrong"
                key is not stored in the M003 simplified schema (it's
                available in session.last_evaluation for future analysis).
            student_id: defaults to "definer" (single-tenant pre-alpha).

        Returns:
            ActorResult(ok=True, data={"logged": True, ...}) on success.
            ActorResult(ok=True, data={"logged": False, "error": ...}) on
            best-effort failure (ok is ALWAYS True — see above).
        """
        logger = ctx.logger
        container: Any = ctx.container

        # Defensive: extract the two fields we store. Missing keys default
        # to empty string so a partial diagnosis doesn't KeyError.
        misconception_text = str(diagnosis.get("misconception", "")) if isinstance(diagnosis, dict) else ""
        corrective_text = str(diagnosis.get("corrective", "")) if isinstance(diagnosis, dict) else ""

        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            # Best-effort: log + return ok=True (never break the session).
            logger.warning("mentor_log_misconception_skipped reason=registry_none concept=%s", concept_id)
            return ActorResult(ok=True, data={"logged": False, "reason": "registry_none"})

        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn
            await conn.execute(
                "INSERT INTO aristotle_misconception_log "
                "(session_id, concept_id, misconception_text, corrective_text) "
                "VALUES (?, ?, ?, ?)",
                (session_id, concept_id, misconception_text, corrective_text),
            )
            await conn.commit()
            logger.info(
                "mentor_misconception_logged concept=%s session=%s misconception_len=%d",
                concept_id, session_id, len(misconception_text),
            )
            return ActorResult(
                ok=True,
                data={
                    "logged": True,
                    "concept_id": concept_id,
                    "session_id": session_id,
                    "misconception_len": len(misconception_text),
                },
            )
        except Exception as exc:
            # Best-effort: NEVER break the session over an analytics row.
            # Log at WARNING for observability + return ok=True.
            logger.warning(
                "mentor_log_misconception_failed concept=%s error=%s:%s",
                concept_id, type(exc).__name__, exc,
            )
            return ActorResult(
                ok=True,
                data={"logged": False, "error": f"{type(exc).__name__}: {exc}"},
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _read_struggle_pattern(
        self,
        ctx: ActorContext,
        student_id: str,
    ) -> str | None:
        """Read the struggle_pattern for a student from the corpus DB."""
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return None

        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn
            cur = await conn.execute(
                "SELECT pattern_text FROM aristotle_struggle_pattern WHERE student_id = ?",
                (student_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            return row[0] if row is not None else None
        except Exception:
            return None

    async def _write_struggle_pattern(
        self,
        ctx: ActorContext,
        student_id: str,
        pattern_text: str,
    ) -> None:
        """Write (INSERT OR REPLACE) the struggle_pattern for a student."""
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return

        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        await conn.execute(
            "INSERT OR REPLACE INTO aristotle_struggle_pattern "
            "(student_id, pattern_text) VALUES (?, ?)",
            (student_id, pattern_text),
        )
        await conn.commit()

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
