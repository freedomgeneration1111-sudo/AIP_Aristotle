"""INTAKE actor — ADR-002 Rev 2 §9, §11.

Role: Drive the multi-turn intake conversation that onboards a new learner.
The INTAKE actor asks about subject, prior knowledge, goals, and schedule,
then generates a learning plan (writes one row to aristotle_learning_plan
+ one row to aristotle_intake_session).

Phase D introduces three re-entry levels:
  - full: new subject or reset (starts from GREETING)
  - partial: one dimension changed (mid-flow entry at GOALS, SCHEDULE, etc.)
  - checkin: re-engagement after absence (GREETING with a "welcome back" prompt)

The actor does NOT call a model for its prompts — they are fixed templates
with the learner's subject interpolated in (same pattern as
SocratesActor.predict). The generate_plan() method queries aristotle_concept
for concepts matching the subject + writes the plan rows.

Layer: imports from aip.foundation.protocols.actors only (ActorResult,
ActorContext). The container is accessed via ctx.container (duck-typed).
SQL is executed via the corpus's write connection.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from aip.foundation.protocols.actors import ActorContext, ActorResult


# ---------------------------------------------------------------------------
# State + trigger types
# ---------------------------------------------------------------------------


class IntakeState(str, Enum):
    """The intake conversation states (ADR-002 §9, simplified for Phase D)."""

    GREETING = "GREETING"
    SUBJECT = "SUBJECT"
    PRIOR_KNOWLEDGE = "PRIOR_KNOWLEDGE"
    GOALS = "GOALS"
    SCHEDULE = "SCHEDULE"
    GENERATING_PLAN = "GENERATING_PLAN"
    COMPLETE = "COMPLETE"


@dataclass
class IntakeTrigger:
    """A trigger detected at session start or mid-session.

    level: "full" (new subject/reset), "partial" (one dimension changed),
           "checkin" (re-engagement after absence).
    entry_state: the IntakeState to resume at. GREETING for full/checkin;
                 mid-flow (GOALS, SCHEDULE, PRIOR_KNOWLEDGE,
                 GENERATING_PLAN) for partial.
    prompt: pre-built prompt for checkin level (e.g. "Welcome back! ...").
            None for full and partial — IntakeActor generates the prompt.
    """

    level: str
    entry_state: IntakeState
    prompt: str | None = None


@dataclass
class IntakeSession:
    """Per-intake-conversation state (passed between steps).

    The caller persists this between run_intake_step() calls (via the API
    serialization in api.py).

    Phase D brain-transplant fields (added when the IntakeActor became
    LLM-driven):
      - material_ids:    ids of uploaded materials (aristotle_uploaded_material
                         rows) the actor should include in its model context.
      - extracted:       running structured extraction from the model
                         (subject, prior_knowledge, goals, schedule_minutes).
                         Updated each turn; persisted so the conversation
                         can resume with the model's understanding intact.
      - draft_plan:      when the model proposes a concept sequence (focus
                         == PLAN_DRAFT), the proposed concepts land here for
                         learner review. Empty list until the model drafts.
      - current_focus:   the model-decided focus for the next turn. Replaces
                         the deterministic state machine's `state` field as
                         the source of truth for "what are we talking about
                         right now." Kept in sync with `state` for backwards
                         compatibility with code that still reads .state.
      - plan_confirmed:  True once the learner confirms the draft plan.
                         Triggers GENERATING_PLAN → COMPLETE transition.
    """

    state: IntakeState = IntakeState.GREETING
    entry_state: IntakeState = IntakeState.GREETING
    plan_id: str = ""
    subject: str = ""
    prior_knowledge: str = ""
    goals: str = ""
    schedule_minutes: int = 30
    responses: list = field(default_factory=list)
    # Phase D brain-transplant fields
    material_ids: list = field(default_factory=list)
    extracted: dict = field(default_factory=dict)
    draft_plan: list = field(default_factory=list)
    current_focus: str = "SUBJECT"
    plan_confirmed: bool = False
    # Task 18 (ADR-004): student_id flows from the API (/intake/start
    # request body, default 'definer') through generate_plan() into
    # aristotle_learning_plan.student_id. Required so GET /aristotle/plans
    # and GET /dashboard can scope by student instead of returning every
    # plan/concept in the shared table.
    student_id: str = "definer"
    # BUG-001 fix: turns spent in the current focus area. Incremented each
    # turn the model returns the same next_focus; reset to 0 on focus change.
    # Passed to the model via the user prompt so it knows how many turns
    # it has spent in the current focus.
    turns_in_focus: int = 0
    # BUG-001-REDUX (Task 15): whether this session gets thorough,
    # open-ended probing (True) or a bounded, fast-converging interview
    # (False, default).
    #
    # History: BUG-001's server-side forcing function was implemented
    # (Task 11), then reverted (Task 12) because it cut off legitimate
    # deep probing for a custom curriculum built around a complex,
    # single-author paper (NBCM). But the revert applied globally — it
    # also removed the safety net for the much more common case: a
    # learner on a standard, already-structured institutional textbook
    # (e.g. Sameer's Punjab Pharmacy Council pharmacognosy material) who
    # gives clear, simple answers and just needs the interview to move on.
    # For that case an unconstrained free-tier model can re-ask the same
    # question 4-5 times in different words with nothing to stop it.
    #
    # deep_intake=False (default): the guided/fast path. The system
    # prompt carries a hard cap + auto-advance rule, AND the server
    # enforces it (see run_intake_step) regardless of whether the model
    # follows the prompt instruction. This is the right default for
    # pilot students (Rameez, Sameer, Freedom Generation classroom).
    #
    # deep_intake=True: the exploratory path (current pre-Task-13
    # behavior, unchanged) — no server-side cap, the model paces itself.
    # Intended for self-directed research curricula (e.g. Moses/NBCM).
    #
    # Set at session start via the /intake/start request body, or
    # mid-session via a small set of trigger phrases the learner can say
    # (see _DEEP_INTAKE_KEYWORDS below) — e.g. "this is a custom research
    # curriculum, take your time." Once set True it is never auto-reset
    # to False.
    deep_intake: bool = False


# ---------------------------------------------------------------------------
# IntakeActor
# ---------------------------------------------------------------------------


class IntakeActor:
    """INTAKE — the onboarding mode of Aristotle (ADR-002 Rev 2 §9, §11).

    Conforms to the foundation Actor Protocol. cadence=0 means manual-only.
    The intake conversation is driven by user turns, not by a timer.

    The actor is a SINGLE internal orchestration mode — the learner never
    meets "INTAKE" as a persona. Aristotle is the only voice (ADR-001 §1).
    """

    name: str = "intake"
    cadence: float = 0.0  # manual-only — driven by user turns

    async def run_cycle(self, ctx: ActorContext) -> ActorResult:
        """Startup health check — verifies corpus reachability."""
        logger = ctx.logger
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return ActorResult(ok=False, error="corpus_registry not available")
        try:
            stores = await registry.get_stores("aristotle:textbook")
            if stores is None:
                return ActorResult(
                    ok=False, error="corpus aristotle:textbook not found"
                )
        except Exception as exc:
            return ActorResult(ok=False, error=f"corpus access failed: {exc}")
        return ActorResult(ok=True)

    # ------------------------------------------------------------------
    # Conversation methods (called on-demand by the session dispatcher)
    # ------------------------------------------------------------------

    async def greet(self, ctx: ActorContext) -> ActorResult:
        """Warm welcome, asks what subject the learner wants to study."""
        prompt = (
            "Hello! I'm Aristotle, your tutor. What subject would you like "
            "to study? It can be anything — physics, pharmacy, history, "
            "a programming language, anything at all."
        )
        return ActorResult(ok=True, data={"prompt": prompt})

    async def ask_prior_knowledge(self, ctx: ActorContext, subject: str) -> ActorResult:
        """Asks what the learner already knows about the subject.

        Low-pressure framing — "none at all is a perfectly fine answer."
        """
        prompt = (
            f"Great — {subject}. Before we start, how much do you already "
            f"know about {subject}? None at all is a perfectly fine answer "
            f"— I just want to know where to start."
        )
        return ActorResult(ok=True, data={"prompt": prompt})

    async def ask_goals(self, ctx: ActorContext, subject: str) -> ActorResult:
        """Asks what the learner wants to achieve.

        Exam? Job? Personal interest? For partial re-INTAKE at GOALS, the
        dispatcher opens with "What's changed about what you want to achieve?"
        instead of calling this method.
        """
        prompt = (
            f"What do you want to achieve with {subject}? Are you preparing "
            f"for an exam, a job interview, a certification, or is this "
            f"personal interest? Knowing your goal helps me tailor the plan."
        )
        return ActorResult(ok=True, data={"prompt": prompt})

    async def ask_schedule(self, ctx: ActorContext) -> ActorResult:
        """Asks how many minutes per day the learner can commit.

        Suggests 20/30/45 as anchors. For partial re-INTAKE at SCHEDULE,
        the dispatcher opens with "How much time do you have now?"
        """
        prompt = (
            "How many minutes per day can you commit to studying? "
            "20 minutes is a good start, 30 is solid, 45 is ambitious. "
            "Pick what feels sustainable — consistency beats intensity."
        )
        return ActorResult(ok=True, data={"prompt": prompt})

    async def generate_plan(
        self, ctx: ActorContext, session: IntakeSession
    ) -> ActorResult:
        """Generate a learning plan from the intake responses + draft_plan.

        If session.draft_plan is non-empty (the LLM-driven path), ingests
        each proposed concept as a new aristotle_concept row and uses
        those concept_ids in the plan. This is the conversational plan
        generation path — concepts come from the model's understanding
        of the conversation + uploaded materials, NOT from a LIKE query
        against sample data.

        If session.draft_plan is empty (the deterministic fallback path
        or a legacy session), falls back to the old behavior: LIKE query
        against aristotle_concept matching the subject, or all concepts
        if no match.

        Either way, writes one row to aristotle_learning_plan + one row
        to aristotle_intake_session (status='complete'). Also updates
        aristotle_uploaded_material.concept_ids_json for any materials
        in session.material_ids so the materials are linked to the
        concepts they informed.

        Returns ActorResult(ok=True, data={"plan_id": ..., "concept_count": N}).
        """
        logger = ctx.logger
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return ActorResult(ok=False, error="corpus_registry not available")

        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn

            concept_ids: list[str] = []

            # Task 18 (ADR-004): generate plan_id BEFORE inserting concepts
            # so each concept row can carry plan_id + material_id at write
            # time. The plan row is inserted further down. material_id is
            # the first uploaded material (the primary source the plan was
            # built from), or None for the deterministic-fallback path that
            # has no material context.
            plan_id = str(uuid.uuid4())
            material_id = session.material_ids[0] if session.material_ids else None

            if session.draft_plan:
                # LLM-driven path: ingest each proposed concept as a new
                # aristotle_concept row. The draft_plan is a list of dicts
                # with keys: topic, subtopic, bloom_target, content_primary,
                # prerequisite_concept_id (index into the list or None).
                # We generate stable-ish ids from the subject + index so
                # re-ingestion is idempotent-ish (same subject + same
                # draft_plan produces the same ids).
                subject_slug = "".join(
                    c.lower() if c.isalnum() else "_" for c in session.subject
                )[:40] or "concept"
                for idx, concept in enumerate(session.draft_plan):
                    cid = f"{subject_slug}_{idx:03d}"
                    topic = concept.get("topic", f"Concept {idx+1}")
                    subtopic = concept.get("subtopic", "")
                    bloom = int(concept.get("bloom_target", 3))
                    content_primary = concept.get("content_primary", "")
                    prereq_idx = concept.get("prerequisite_concept_id")
                    prereq_id = (
                        f"{subject_slug}_{prereq_idx:03d}"
                        if isinstance(prereq_idx, int)
                        else None
                    )

                    # INSERT OR REPLACE so re-ingestion (learner amends +
                    # reconfirms) updates the concept rather than failing
                    # on the PRIMARY KEY collision.
                    # Task 18 (ADR-004): now includes plan_id + material_id
                    # so future callers can scope by plan without parsing
                    # concept_ids_json. Both are nullable in the schema, so
                    # the deterministic-fallback path (which doesn't have a
                    # draft_plan and never enters this branch) is unaffected.
                    await conn.execute(
                        "INSERT OR REPLACE INTO aristotle_concept "
                        "(id, textbook_chapter, topic, subtopic, bloom_target, "
                        "content_primary, content_alt, content_alt_lang, "
                        "prerequisite_concept_id, created_at, "
                        "plan_id, material_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, datetime('now'), ?, ?)",
                        (
                            cid,
                            subject_slug,
                            topic,
                            subtopic,
                            bloom,
                            content_primary,
                            prereq_id,
                            plan_id,
                            material_id,
                        ),
                    )
                    concept_ids.append(cid)

                logger.info(
                    "intake_draft_plan_ingested concept_count=%d subject=%s",
                    len(concept_ids),
                    session.subject,
                )
            else:
                # Deterministic fallback path: LIKE query against
                # aristotle_concept matching the subject, or all concepts
                # if no match. This is the old behavior — kept for the
                # no-model fallback path and legacy sessions.
                # Task 18 (ADR-004): concepts loaded here were not created
                # by this plan, so their plan_id/material_id stay NULL
                # (or whatever they were before). The plan row itself
                # still gets student_id + material_id below.
                cur = await conn.execute(
                    "SELECT id FROM aristotle_concept "
                    "WHERE topic LIKE ? OR subtopic LIKE ? "
                    "ORDER BY id",
                    (f"%{session.subject}%", f"%{session.subject}%"),
                )
                rows = await cur.fetchall()
                await cur.close()

                if rows:
                    concept_ids = [row[0] for row in rows]
                else:
                    # No match — use all concepts ordered by insertion order.
                    cur = await conn.execute("SELECT id FROM aristotle_concept ORDER BY id")
                    rows = await cur.fetchall()
                    await cur.close()
                    concept_ids = [row[0] for row in rows] if rows else []

            now = datetime.now(timezone.utc).isoformat()
            concept_ids_json = json.dumps(concept_ids)

            # Write the learning_plan row.
            # Task 18 (ADR-004): now includes student_id + material_id so
            # GET /aristotle/plans?student_id=X and GET /dashboard?plan_id=Y
            # can scope without parsing concept_ids_json.
            await conn.execute(
                "INSERT INTO aristotle_learning_plan "
                "(id, subject, goals, schedule_minutes_per_day, "
                "concept_ids_json, current_concept_idx, status, created_at, "
                "student_id, material_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    session.subject,
                    session.goals,
                    session.schedule_minutes,
                    concept_ids_json,
                    0,
                    "active",
                    now,
                    session.student_id,
                    material_id,
                ),
            )

            # Write the intake_session row (status='complete').
            responses_json = json.dumps(session.responses)
            await conn.execute(
                "INSERT INTO aristotle_intake_session "
                "(plan_id, subject, prior_knowledge, goals, "
                "schedule_minutes_per_day, responses_json, status, "
                "created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    session.subject,
                    session.prior_knowledge,
                    session.goals,
                    session.schedule_minutes,
                    responses_json,
                    "complete",
                    now,
                    now,
                ),
            )

            # Link uploaded materials to the ingested concepts so the
            # teacher dashboard can show "this material informed these
            # concepts" (Piece 4 will surface this in the GUI).
            if session.material_ids and concept_ids:
                for mid in session.material_ids:
                    await conn.execute(
                        "UPDATE aristotle_uploaded_material "
                        "SET concept_ids_json = ? WHERE id = ?",
                        (concept_ids_json, mid),
                    )

            await conn.commit()
            logger.info(
                "intake_plan_generated plan_id=%s subject=%s concept_count=%d",
                plan_id,
                session.subject,
                len(concept_ids),
            )
            return ActorResult(
                ok=True,
                data={"plan_id": plan_id, "concept_count": len(concept_ids)},
            )
        except Exception as exc:
            logger.warning(
                "intake_plan_generation_failed error=%s:%s",
                type(exc).__name__,
                exc,
            )
            return ActorResult(ok=False, error=f"plan generation failed: {exc}")

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


# ---------------------------------------------------------------------------
# Intent detection (keyword-based, no NLP)
# ---------------------------------------------------------------------------

# Keyword lists for each trigger. Checked in order — first match wins.
# Case-insensitive substring match.
# TODO: candidate for classifier actor in v2 (a small model that reads the
# learner's input and classifies the intent). For pre-alpha, keyword
# matching is sufficient and deterministic.
_FULL_KEYWORDS = [
    "new topic",
    "start over",
    "different subject",
    "learn something else",
    "reset",
    "something new",
]
_GOALS_KEYWORDS = [
    "exam",
    "deadline",
    "goal changed",
    "target",
    "need to pass",
    "interview",
]
_SCHEDULE_KEYWORDS = [
    "busier",
    "less time",
    "only have",
    "minutes a day",
    "schedule",
    "not enough time",
]
_PLAN_KEYWORDS = [
    "add more topics",
    "go deeper",
    "extend my plan",
    "more advanced",
]
# Task 15: mid-session opt-in to the exploratory (deep_intake=True) path.
# Matches the existing keyword-detector idiom used for GOALS/SCHEDULE/PLAN
# pivots above. Deliberately narrow — false positives just mean a few extra
# thoughtful questions, not a broken interview, so precision matters more
# than recall here. TODO (same caveat as _detect_intake_intent): a
# classifier would be more robust than keywords; fine for pre-alpha.
_DEEP_INTAKE_KEYWORDS = [
    "custom curriculum",
    "custom research",
    "my own paper",
    "my own research",
    "research curriculum",
    "take your time",
    "ask as many questions",
]


def _detect_deep_intake_opt_in(text: str) -> bool:
    """True if the learner's text signals they want the exploratory,
    unbounded-probing intake path rather than the default guided path."""
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _DEEP_INTAKE_KEYWORDS)


def _detect_intake_intent(text: str) -> IntakeTrigger | None:
    """Detect intake intent from the learner's free-form text.

    Keyword-based, case-insensitive, substring match. Returns a trigger
    or None (no match). Checked in order: full, goals, schedule, plan.

    TODO: candidate for classifier actor in v2. A small model that reads
    the learner's input and classifies the intent would be more robust
    than keyword matching. For pre-alpha, keywords are sufficient and
    deterministic.
    """
    if not text:
        return None
    lower = text.lower()

    for kw in _FULL_KEYWORDS:
        if kw in lower:
            return IntakeTrigger(
                level="full",
                entry_state=IntakeState.GREETING,
            )

    for kw in _GOALS_KEYWORDS:
        if kw in lower:
            return IntakeTrigger(
                level="partial",
                entry_state=IntakeState.GOALS,
            )

    for kw in _SCHEDULE_KEYWORDS:
        if kw in lower:
            return IntakeTrigger(
                level="partial",
                entry_state=IntakeState.SCHEDULE,
            )

    for kw in _PLAN_KEYWORDS:
        if kw in lower:
            return IntakeTrigger(
                level="partial",
                entry_state=IntakeState.GENERATING_PLAN,
            )

    return None


# ---------------------------------------------------------------------------
# System-side trigger checking (run at session start)
# ---------------------------------------------------------------------------


async def check_intake_triggers(
    ctx: ActorContext, plan_id: str | None
) -> IntakeTrigger | None:
    """System-side checks run at session start (ADR-002 §9 re-surfacing logic).

    In order:
    a. If plan_id is None → full (no plan exists yet).
    b. Query learning_plan by plan_id. If status='complete' → full,
       entry=GREETING, prompt="You've completed your plan. Want to start
       something new?"
    c. If last_session_at is not None and days since last_session_at > 14
       → checkin, entry=GREETING, prompt="Welcome back! You've been away
       a while. Still working on [subject], or ready for something new?"
    d. If consecutive_missed_sessions > 3 → checkin, entry=SCHEDULE,
       prompt="Looks like the current schedule isn't quite fitting. Want
       to adjust how much time you're committing?"
    e. Otherwise → None (proceed normally).
    """
    # (a) No plan → full intake.
    if plan_id is None:
        return IntakeTrigger(
            level="full",
            entry_state=IntakeState.GREETING,
        )

    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return None

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        cur = await conn.execute(
            "SELECT subject, status, last_session_at, "
            "consecutive_missed_sessions "
            "FROM aristotle_learning_plan WHERE id = ?",
            (plan_id,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is None:
            # Plan not found → full intake.
            return IntakeTrigger(
                level="full",
                entry_state=IntakeState.GREETING,
            )

        subject = row[0] or ""
        status = row[1] or "active"
        last_session_at = row[2]
        missed = row[3] if row[3] is not None else 0

        # (b) Plan complete → full, with a "completed" prompt.
        if status == "complete":
            return IntakeTrigger(
                level="full",
                entry_state=IntakeState.GREETING,
                prompt=("You've completed your plan. Want to start something new?"),
            )

        # (c) Long absence (> 14 days) → checkin at GREETING.
        if last_session_at is not None:
            try:
                last = datetime.fromisoformat(last_session_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_since = (now - last).days
                if days_since > 14:
                    subject_phrase = (
                        f"still working on {subject}"
                        if subject
                        else "ready for something new"
                    )
                    return IntakeTrigger(
                        level="checkin",
                        entry_state=IntakeState.GREETING,
                        prompt=(
                            f"Welcome back! You've been away a while. "
                            f"Still working on {subject}, or ready for something new?"
                        ),
                    )
            except (ValueError, TypeError):
                pass  # malformed timestamp → skip this check

        # (d) Too many missed sessions → checkin at SCHEDULE.
        if missed > 3:
            return IntakeTrigger(
                level="checkin",
                entry_state=IntakeState.SCHEDULE,
                prompt=(
                    "Looks like the current schedule isn't quite fitting. "
                    "Want to adjust how much time you're committing?"
                ),
            )

        # (e) No trigger — proceed normally.
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session dispatcher
# ---------------------------------------------------------------------------

# Ordered list of states for flow progression (excludes GREETING at the
# start since it's the entry point, and GENERATING_PLAN/COMPLETE which are
# terminal/single-phase).
_FLOW_ORDER = [
    IntakeState.GREETING,
    IntakeState.SUBJECT,
    IntakeState.PRIOR_KNOWLEDGE,
    IntakeState.GOALS,
    IntakeState.SCHEDULE,
    IntakeState.GENERATING_PLAN,
    IntakeState.COMPLETE,
]


def _state_index(state: IntakeState) -> int:
    """Return the index of a state in the flow order."""
    try:
        return _FLOW_ORDER.index(state)
    except ValueError:
        return 0


async def _fetch_material_texts(ctx: ActorContext, material_ids: list) -> list[dict]:
    """Fetch extracted text for each material_id from aristotle_uploaded_material.

    Returns a list of {filename, source_type, extracted_text} dicts. Skips
    ids that don't have a row (best-effort). Used by the LLM-driven intake
    loop to include uploaded material content in the model context.
    """
    if not material_ids:
        return []
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return []
    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        placeholders = ",".join("?" * len(material_ids))
        cur = await conn.execute(
            f"SELECT filename, source_type, extracted_text "
            f"FROM aristotle_uploaded_material WHERE id IN ({placeholders})",
            tuple(material_ids),
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {"filename": r[0], "source_type": r[1], "extracted_text": r[2] or ""}
            for r in rows
        ]
    except Exception:
        return []


# System prompt for the LLM-driven intake loop. Built dynamically per
# session (Task 15) — the pacing guidance differs between the guided
# (fast, bounded) and deep (exploratory, unbounded) paths; everything
# else — identity, JSON schema, materials handling — is shared.
_INTAKE_SYSTEM_PROMPT_HEADER = """You are Aristotle, an adaptive tutor conducting the onboarding intake interview with a new learner. You are the ONLY voice the learner meets — they never see "INTAKE" as a persona.

YOUR JOB: Have a real conversation to understand what the learner wants to study, what they already know, what their goals are, how much time they have, and what materials they've brought. Then propose a learning plan."""

_INTAKE_PACING_GUIDED = """This is NOT a rigid questionnaire, but it IS a bounded one. Each focus area (subject, prior knowledge, goals, schedule, materials) gets at most 2 turns before you must advance — this is enforced by the server regardless of your choice, so treat it as a real constraint, not a suggestion. If the learner has already given you a clear, specific, usable answer (a named subject, a concrete goal like "become a pharmacist", a stated time budget), do NOT re-ask the same question in different words to gather more nuance — advance. Precision is nice; a completed intake the learner can actually start learning from is more important. Most learners are on a known, already-structured course (a textbook, an institutional curriculum) — you don't need to reconstruct their whole educational history, just enough to propose a sensible first plan.

HARD CAP ON INTERROGATION: advance next_focus after at most 2 turns in any one focus area, even if your understanding feels incomplete — you can always refine later, in TUTORING.
AUTO-ADVANCE RULE: once subject, prior_knowledge, goals, and schedule_minutes are all populated in "extracted", set next_focus="PLAN_DRAFT" on your very next turn.
FOCUS COHERENCE: the question you ask in "response" must match the next_focus you declare this same turn — don't declare next_focus="GOALS" while asking a prior-knowledge question, or vice versa. If you notice you're about to ask something you've already asked (even reworded), that's your signal to advance instead."""

_INTAKE_PACING_DEEP = """This is NOT a rigid questionnaire. Each focus area (subject, prior knowledge, goals, schedule, materials) is a PHASE, not a single turn. You can ask follow-up questions, dig deeper, circle back, or adjust based on what the learner says. You decide when you have enough signal to advance. This learner has opted into thorough intake for a custom or research-grade curriculum — for a custom curriculum built around a complex paper, TAKE YOUR TIME — it's better to ask 8-10 thoughtful questions and build a precise plan than to rush to PLAN_DRAFT with a shallow understanding.

FOCUS COHERENCE: the question you ask in "response" should still match the next_focus you declare this same turn."""

_INTAKE_SYSTEM_PROMPT_BODY = """

UPLOADED MATERIALS ARE THE CURRICULUM. When the learner uploads a paper, textbook chapter, or notes, that material IS the curriculum — you must read it deeply and derive the learning plan's concepts from its actual content. Do NOT ask the learner to summarize the paper for you; read it yourself from the "Uploaded materials" section of the user prompt. Acknowledge specifically what you read (e.g., "I see this paper covers Newton's three laws and includes worked examples on inclined planes" — not "I see you uploaded a paper"). When you propose a draft_plan, the concepts should come from the paper's actual sections, equations, theorems, or chapters — ordered by prerequisite dependency as the paper presents them.

If the "Uploaded materials" section shows a truncation notice ("... [PAPER TRUNCATED: N more chars]"), acknowledge to the learner that you've read the first portion and ask whether the remaining content follows the same structure or introduces new topics — don't pretend you've read the whole paper.

YOU MUST RETURN VALID JSON with this exact schema:
{
  "response": "what you say to the learner next (conversational, warm, in Aristotle's voice)",
  "next_focus": "SUBJECT" | "PRIOR_KNOWLEDGE" | "GOALS" | "SCHEDULE" | "MATERIALS" | "PLAN_DRAFT" | "COMPLETE",
  "extracted": {
    "subject": "the actual subject extracted from the learner's words (e.g., 'physics', not 'i want to learn physics')",
    "prior_knowledge": "what they already know, summarized",
    "goals": "what they want to achieve, summarized",
    "schedule_minutes": 30
  },
  "draft_plan": null
}

Rules for next_focus:
- SUBJECT: still figuring out what they want to study
- PRIOR_KNOWLEDGE: probing what they already know
- GOALS: understanding their goals (exam? personal interest? SME?)
- SCHEDULE: figuring out time commitment
- MATERIALS: discussing uploaded materials, asking them to upload if relevant
- PLAN_DRAFT: you have enough to propose a concept sequence (set draft_plan to a list of concept objects — see below)
- COMPLETE: the learner has confirmed the draft plan; intake is done

When next_focus == "PLAN_DRAFT", set draft_plan to a list of concept objects:
[
  {
    "topic": "short topic name",
    "subtopic": "more specific",
    "bloom_target": 1-6,
    "content_primary": "1-2 sentence description of what this concept covers",
    "prerequisite_concept_id": null or index of prerequisite in this list
  },
  ...
]

The draft_plan should be derived from the conversation + any uploaded materials. If the learner uploaded a paper, the concepts should come from the paper's actual content — its sections, equations, theorems, or chapter headings. Do NOT invent generic concepts; ground every concept in the paper's actual structure. Order concepts by prerequisite dependency (foundations first, as the paper presents them).

When next_focus == "COMPLETE", the learner has confirmed the plan. Keep draft_plan as the confirmed list.

The "extracted" field should be updated each turn with your current understanding. Start with empty strings and fill in as you learn. "subject" must be the extracted subject (e.g., "physics", "null boundary constraint manifolds"), NOT the learner's raw words.

Be conversational. Reflect back what you heard. Ask one question at a time. The turns_in_focus counter is for your awareness — if you've spent many turns in one focus area, consider whether you have enough signal or whether you're asking the same question in different ways.

When you do propose a draft_plan, ground every concept in the paper's actual content (sections, equations, theorems) AND in what you learned about the learner's gaps. The plan should bridge from the learner's current knowledge to the paper's advanced topics."""


def _build_intake_system_prompt(deep_intake: bool) -> str:
    """Assemble the intake system prompt for this session's pacing mode.

    Task 15: the header + body (identity, JSON schema, materials handling)
    are shared; the pacing section — how aggressively to advance next_focus
    — differs between guided (default, bounded) and deep (opted-in,
    exploratory) sessions. See IntakeSession.deep_intake for the rationale.
    """
    pacing = _INTAKE_PACING_DEEP if deep_intake else _INTAKE_PACING_GUIDED
    return f"{_INTAKE_SYSTEM_PROMPT_HEADER}\n\n{pacing}{_INTAKE_SYSTEM_PROMPT_BODY}"


def _build_intake_user_prompt(
    session: IntakeSession,
    student_input: str,
    materials: list[dict],
    material_preview_chars: int = 20000,
) -> str:
    """Build the user prompt for the LLM-driven intake turn.

    Includes: current focus, conversation history, extracted so far,
    uploaded material texts (truncated to material_preview_chars with a
    clear truncation notice), and the learner's latest reply.

    Args:
        material_preview_chars: Max chars of each material to include.
            Default 20000 (~5000 tokens) — large enough for the LLM to
            actually read a paper's abstract + intro + methods + first
            results section. For longer papers, a truncation notice is
            appended so the LLM knows the paper continues.
    """
    parts = []
    parts.append(f"Current focus: {session.current_focus}")
    # turns_in_focus is for the model's awareness — it helps the model
    # notice if it's been asking the same type of question for many turns.
    # This is NOT a forcing function; the model decides when to advance.
    parts.append(f"Turns spent in current focus: {session.turns_in_focus}")
    parts.append("")

    # Conversation history (last 8 turns to keep context manageable).
    history = session.responses[-8:] if len(session.responses) > 8 else session.responses
    if history:
        parts.append("Conversation so far (learner's replies, most recent last):")
        for i, r in enumerate(history, 1):
            parts.append(f"  {i}. {r}")
        parts.append("")

    # Extracted understanding so far.
    if session.extracted:
        parts.append("Your current understanding:")
        for k, v in session.extracted.items():
            if v:
                parts.append(f"  {k}: {v}")
        parts.append("")

    # Uploaded materials — the curriculum. Include the full text up to
    # material_preview_chars per material. For longer materials, append a
    # clear truncation notice so the LLM knows the paper continues and
    # can ask the learner about the remaining scope.
    if materials:
        parts.append("Uploaded materials (these ARE the curriculum — read them deeply):")
        for m in materials:
            filename = m.get("filename", "unknown")
            source_type = m.get("source_type", "unknown")
            full_text = m.get("extracted_text") or ""
            total_chars = len(full_text)
            # BUG-002 Part A: guard against empty/garbled extracted_text.
            # If pypdf failed to extract meaningful text (common with
            # math-heavy PDFs where text is rendered as glyphs/LaTeX),
            # tell the LLM explicitly so it doesn't hallucinate that it
            # read the paper.
            if total_chars < 100:
                parts.append(
                    f"  --- {filename} ({source_type}) — EXTRACTION FAILED: "
                    f"only {total_chars} chars extracted. This is likely a "
                    f"math-heavy or scanned PDF that pypdf cannot parse. "
                    f"DO NOT claim to have read this paper. Tell the learner "
                    f"the extraction failed and ask them to paste the "
                    f"abstract + section headings as text. ---"
                )
                parts.append("")
                continue
            if total_chars <= material_preview_chars:
                parts.append(f"  --- {filename} ({source_type}, {total_chars} chars) ---")
                parts.append(full_text)
            else:
                preview = full_text[:material_preview_chars]
                remaining = total_chars - material_preview_chars
                parts.append(f"  --- {filename} ({source_type}, {total_chars} chars total — showing first {material_preview_chars}) ---")
                parts.append(preview)
                parts.append(
                    f"  ... [PAPER TRUNCATED: {remaining} more chars not shown. "
                    f"Ask the learner whether the remaining content follows the "
                    f"same structure or introduces new topics.]"
                )
            parts.append("")

    # Draft plan if one exists (learner is reviewing/amending).
    if session.draft_plan:
        parts.append("Current draft plan:")
        for i, c in enumerate(session.draft_plan):
            parts.append(f"  {i+1}. {c.get('topic', '?')} — {c.get('content_primary', '')[:100]}")
        parts.append("")

    # BUG-003 fix: distinguish three cases for student_input:
    #   1. Non-empty text → learner's latest reply
    #   2. Empty text + session.responses exists → upload auto-trigger
    #      (materials just arrived, no new text). Tell the LLM to
    #      acknowledge the upload specifically — NOT to re-greet.
    #   3. Empty text + no responses → genuine first turn (greeting)
    if student_input:
        parts.append(f"Learner's latest reply: {student_input}")
    elif session.responses:
        parts.append(
            "(The learner just uploaded a file. No new text from them. "
            "Acknowledge SPECIFICALLY what you read in the uploaded material — "
            "sections, equations, topics you can see. Do NOT claim you read it "
            "if the material shows EXTRACTION FAILED. Do NOT re-greet. "
            "If all four extracted fields are populated, propose a draft_plan now.)"
        )
    else:
        parts.append("(This is the first turn — generate your greeting.)")

    parts.append("")
    parts.append("Respond with the JSON object described in your instructions.")
    return "\n".join(parts)


def _build_rag_intake_prompt(
    session: IntakeSession,
    student_input: str,
    structural_maps: list[dict],
    rag_chunks: list[dict],
) -> str:
    """Build the RAG-driven intake prompt (ADR-003).

    Replaces the legacy truncation path. The LLM sees:
      1. Current focus + turns_in_focus
      2. Conversation history (last 8 turns)
      3. Extracted understanding so far
      4. Structural map (TOC + concept index) — compact, shown every turn
      5. Retrieved chunks (top-K relevant to the current context)
      6. Learner's latest reply

    No truncation. The LLM sees the paper's structure always + the specific
    chunks relevant to the current question. Over multiple turns, the LLM
    effectively "reads" the whole paper.
    """
    parts = []
    parts.append(f"Current focus: {session.current_focus}")
    parts.append(f"Turns spent in current focus: {session.turns_in_focus}")
    parts.append("")

    # Conversation history (last 8 turns)
    history = session.responses[-8:] if len(session.responses) > 8 else session.responses
    if history:
        parts.append("Conversation so far (learner's replies, most recent last):")
        for i, r in enumerate(history, 1):
            parts.append(f"  {i}. {r}")
        parts.append("")

    # Extracted understanding so far
    if session.extracted:
        parts.append("Your current understanding:")
        for k, v in session.extracted.items():
            if v:
                parts.append(f"  {k}: {v}")
        parts.append("")

    # Structural map (TOC + concept index) — shown every turn so the LLM
    # always knows the paper's structure. Compact (~2k tokens).
    if structural_maps:
        parts.append("=== UPLOADED PAPER STATUS ===")
        parts.append("The learner HAS uploaded a paper. It is ingested + chunked + embedded.")
        parts.append("You DO NOT need to ask them to upload it again. It is HERE.")
        parts.append("")
        parts.append("Paper structure (table of contents + key concepts):")
        for smap in structural_maps:
            toc = smap.get("toc", [])
            concepts = smap.get("concepts", [])
            if toc:
                parts.append("  Table of contents:")
                for entry in toc[:20]:  # cap at 20 entries to stay compact
                    parts.append(f"    {entry.get('chunk_index', '?')}. {entry.get('heading', '?')}")
            if concepts:
                parts.append(f"  Key concepts: {', '.join(concepts[:30])}")
        parts.append("")

    # Retrieved chunks — the RAG retrieval results. Each chunk has its
    # section heading + full text. The LLM sees the specific parts of
    # the paper relevant to the current question.
    if rag_chunks:
        parts.append("Relevant excerpts from the paper (retrieved for this turn):")
        for i, chunk in enumerate(rag_chunks, 1):
            content = chunk.get("content", "")
            metadata = chunk.get("metadata", {})
            heading = metadata.get("heading", "")
            section = metadata.get("section_path", "")
            score = chunk.get("score", 0.0)
            parts.append(f"  --- Excerpt {i} (relevance: {score:.2f}) ---")
            if section or heading:
                parts.append(f"  Section: {section or heading}")
            parts.append(content)
            parts.append("")
    else:
        parts.append("(No relevant excerpts retrieved — the paper may still be ingesting. Answer based on the structural map above + conversation context.)")
        parts.append("")

    # Draft plan if one exists
    if session.draft_plan:
        parts.append("Current draft plan:")
        for i, c in enumerate(session.draft_plan):
            parts.append(f"  {i+1}. {c.get('topic', '?')} — {c.get('content_primary', '')[:100]}")
        parts.append("")

    # Learner's latest reply (3-way branch: reply / upload auto-trigger / first turn)
    if student_input:
        parts.append(f"Learner's latest reply: {student_input}")
    elif session.responses:
        parts.append(
            "(The learner just uploaded a file. No new text from them. "
            "Acknowledge SPECIFICALLY what you read in the retrieved excerpts — "
            "sections, equations, topics you can see. Do NOT claim you read it "
            "if no excerpts were retrieved. Do NOT re-greet. "
            "If all four extracted fields are populated, propose a draft_plan now.)"
        )
    else:
        parts.append("(This is the first turn — generate your greeting.)")

    parts.append("")
    parts.append("Respond with the JSON object described in your instructions.")
    return "\n".join(parts)


async def run_intake_step(
    session: IntakeSession,
    student_input: str,
    ctx: ActorContext,
) -> dict:
    """LLM-driven intake loop (ADR-002 §9 brain transplant).

    Each turn:
      1. Build context: conversation history, current focus, extracted
         understanding, uploaded material texts, learner's latest reply.
      2. Call model_provider.call(slot_name="beast", ...) with the
         INTAKE system prompt + the context user prompt.
      3. Parse the JSON response: {response, next_focus, extracted,
         draft_plan}.
      4. Update session fields from the parsed response.
      5. If next_focus == "COMPLETE" and draft_plan is present, ingest
         the concepts (Piece 3) + generate the plan + advance state.

    Fallback: if no model_provider is available, delegates to the old
    deterministic dispatcher (_run_intake_step_deterministic) so the
    system still works in tests / offline mode.

    Returns: {"state": ..., "prompt": ...} or {"state": "COMPLETE",
    "plan_id": ..., "concept_count": N}.
    """
    logger = ctx.logger
    container: Any = ctx.container
    model_provider = getattr(container, "model_provider", None)

    # No model → fall back to deterministic path (tests, offline mode).
    if model_provider is None:
        return await _run_intake_step_deterministic(session, student_input, ctx)

    # ADR-003: RAG retrieval. Instead of fetching the full paper text
    # (truncated to 20k chars), retrieve the top-K chunks relevant to the
    # current conversation context. This scales to full textbooks + multiple
    # papers. Falls back to legacy truncation if RAG retrieval returns nothing
    # (e.g., ingestion job still running, or paper too short to chunk).
    materials = await _fetch_material_texts(ctx, session.material_ids)

    # Log material fetch results — critical for debugging "the LLM didn't
    # see my paper" issues. If session.material_ids is non-empty but
    # materials is empty, the DB lookup returned 0 rows (the material_id
    # didn't match any row in aristotle_uploaded_material).
    if session.material_ids:
        total_chars = sum(len(m.get("extracted_text") or "") for m in materials)
        logger.info(
            "intake_materials_fetched requested=%d fetched=%d total_chars=%d",
            len(session.material_ids),
            len(materials),
            total_chars,
        )
        if len(materials) < len(session.material_ids):
            logger.warning(
                "intake_materials_missing requested_ids=%s fetched_filenames=%s — "
                "some uploaded materials were not found in the DB. This usually "
                "means the upload route failed to persist them (check the upload "
                "route's INSERT column/value order).",
                session.material_ids,
                [m.get("filename") for m in materials],
            )
        # BUG-002 Part C: warn when extracted_text is suspiciously short.
        # This catches math-heavy PDFs where pypdf extracts only a few
        # chars (LaTeX rendered as glyphs, scanned images without OCR).
        # The LLM context will be effectively empty — without this warning
        # the operator can't tell why the LLM claims it "read" the paper
        # but asks about its structure.
        for m in materials:
            text_len = len(m.get("extracted_text") or "")
            if text_len < 200:
                logger.warning(
                    "intake_material_text_thin filename=%s chars=%d — "
                    "PDF likely failed extraction (math-heavy or scanned). "
                    "LLM context will be effectively empty; the model may "
                    "hallucinate that it read the paper.",
                    m.get("filename"), text_len,
                )
    else:
        logger.debug("intake_no_materials_attached session_id=%s", id(session))

    # Record the learner's reply in the conversation history.
    if student_input:
        session.responses.append(student_input)

    # Task 15: mid-session opt-in to the exploratory (deep_intake) path.
    # Checked every turn, never auto-reset — once a learner signals they
    # want thorough probing (e.g. "this is a custom research curriculum"),
    # that preference sticks for the rest of the session.
    if not session.deep_intake and _detect_deep_intake_opt_in(student_input):
        session.deep_intake = True
        logger.info("intake_deep_mode_opted_in student_input=%r", student_input[:80])

    # ADR-003: RAG retrieval. Build a retrieval query from the current
    # conversation context + retrieve top-K chunks from the vector store.
    # This replaces the legacy truncation path — the LLM sees relevant
    # chunks instead of the first 20k chars. Falls back to legacy
    # truncation if RAG returns nothing (ingestion not done yet).
    rag_chunks: list[dict] = []
    structural_maps: list[dict] = []
    if session.material_ids:
        try:
            from aristotle.ingestion.paper_ingestor import (
                retrieve_relevant_chunks, get_structural_map,
            )
            # Build retrieval query from conversation context
            query_parts = [session.current_focus]
            if student_input:
                query_parts.append(student_input)
            if session.extracted.get("subject"):
                query_parts.append(session.extracted["subject"])
            retrieval_query = " ".join(query_parts)

            rag_chunks = await retrieve_relevant_chunks(
                container, retrieval_query, top_k=5,
                material_ids=session.material_ids,
            )
            # Get the structural map (TOC + concepts) for each material —
            # this is compact (~2k tokens) and shown every turn so the LLM
            # always knows the paper's structure.
            for mid in session.material_ids:
                smap = await get_structural_map(container, mid)
                if smap.get("toc"):
                    structural_maps.append(smap)

            logger.info(
                "rag_retrieved chunks=%d structural_maps=%d query=%r",
                len(rag_chunks), len(structural_maps), retrieval_query[:80],
            )
        except Exception as exc:
            logger.warning(
                "rag_retrieval_failed error=%s:%s — falling back to legacy truncation",
                type(exc).__name__, exc,
            )

    # Resolve material_preview_chars from the extension config (if the
    # container exposes it) — defaults to 20000 if not configured.
    # Used only for the legacy fallback path.
    preview_chars = 20000
    ext_config = getattr(container, "extension_config", None)
    if ext_config is not None:
        ar_settings = ext_config.get("aristotle", {}) if isinstance(ext_config, dict) else {}
        preview_chars = ar_settings.get("material_preview_chars", 20000)

    # Build the prompts. If RAG retrieval succeeded, use the RAG path
    # (structural map + retrieved chunks). Otherwise fall back to legacy
    # truncation of the full material text.
    if rag_chunks or structural_maps:
        user_prompt = _build_rag_intake_prompt(
            session, student_input, structural_maps, rag_chunks,
        )
    else:
        user_prompt = _build_intake_user_prompt(
            session, student_input, materials, material_preview_chars=preview_chars,
        )

    try:
        # Retry on transient network failures (DNS, connection reset, etc.).
        # OpenRouter free-tier models can have intermittent connectivity
        # issues — a single retry usually succeeds. Without this, the user
        # sees "Something went wrong" + has to manually re-type their reply.
        max_retries = 2
        last_exc: Exception | None = None
        raw = ""
        for attempt in range(max_retries + 1):
            try:
                result = await model_provider.call(
                    slot_name="beast",
                    messages=[
                        {"role": "system", "content": _build_intake_system_prompt(session.deep_intake)},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                raw = result.get("content", "") if isinstance(result, dict) else ""
                if raw and raw.strip():
                    break  # got a response, stop retrying
                # Empty response — treat as failure + retry if attempts remain
                logger.info("intake_model_empty_response attempt=%d", attempt + 1)
                last_exc = RuntimeError("empty model response")
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "intake_model_call_attempt_failed attempt=%d error=%s:%s",
                    attempt + 1, type(exc).__name__, exc,
                )
                # Brief delay before retry (1s, then 2s)
                if attempt < max_retries:
                    import asyncio as _asyncio
                    await _asyncio.sleep(1.0 * (attempt + 1))

        if not raw or not raw.strip():
            # All retries exhausted. Give the user a clear, actionable message
            # instead of the generic "trouble thinking" — tell them it's a
            # network issue + to retry their message.
            logger.warning(
                "intake_model_all_retries_exhausted last_error=%s:%s",
                type(last_exc).__name__ if last_exc else "None",
                last_exc,
            )
            return {
                "state": session.state.value,
                "prompt": (
                    "I'm having trouble reaching my language model right now "
                    "(network error — this is usually temporary). Please wait "
                    "a moment and send your message again. Your conversation "
                    "is saved — I won't lose context."
                ),
            }
    except Exception as exc:
        logger.warning("intake_model_call_failed error=%s:%s", type(exc).__name__, exc)
        return {
            "state": session.state.value,
            "prompt": (
                "I had trouble thinking just now (network error). "
                "Please send your message again — I'll pick up where we left off."
            ),
        }

    # Parse the JSON response. The model may wrap JSON in markdown
    # fences or include preamble — extract the first { ... } block.
    parsed = _parse_json_response(raw)
    if parsed is None:
        # The model returned plain text (not JSON). This happens with
        # models that don't reliably follow structured output instructions.
        # Rather than failing, treat the plain text as the conversational
        # response and keep the current focus/extracted unchanged. The
        # learner still gets a reply — they just don't get state-machine
        # advancement on this turn. On the next turn, the model may
        # produce JSON (the system prompt repeats the instruction).
        logger.info("intake_model_response_plain_text (non-JSON) len=%d", len(raw))
        if raw and raw.strip():
            return {
                "state": session.state.value,
                "prompt": raw.strip(),
            }
        # Truly empty response — the model call returned nothing.
        logger.warning("intake_model_response_empty")
        return {
            "state": session.state.value,
            "prompt": (
                "I'm having trouble connecting to my language model right now. "
                "Please check that your model provider is configured "
                "(OpenRouter API key, local model, etc.) and try again."
            ),
        }

    # Update session from the parsed response.
    response_text = parsed.get("response", "")
    next_focus = parsed.get("next_focus", session.current_focus)
    extracted = parsed.get("extracted") or {}
    draft_plan = parsed.get("draft_plan")

    # BUG-001 fix Part A: track turns_in_focus. If the model returned the
    # same focus as the current one, increment; otherwise reset to 0.
    # This is VISIBILITY ONLY — passed to the model so it knows how many
    # turns it's spent in the current focus. The server does NOT override
    # the model's focus choice (a custom curriculum legitimately needs
    # many questions to gauge the student's state).
    if next_focus == session.current_focus:
        session.turns_in_focus += 1
    else:
        session.turns_in_focus = 0

    session.current_focus = next_focus
    if extracted:
        # Merge extracted into session.extracted (don't overwrite with empty).
        for k, v in extracted.items():
            if v not in (None, "", 0):
                session.extracted[k] = v
        # Sync the legacy fields from extracted for backwards compat.
        if session.extracted.get("subject"):
            session.subject = session.extracted["subject"]
        if session.extracted.get("prior_knowledge"):
            session.prior_knowledge = session.extracted["prior_knowledge"]
        if session.extracted.get("goals"):
            session.goals = session.extracted["goals"]
        if session.extracted.get("schedule_minutes"):
            try:
                session.schedule_minutes = int(session.extracted["schedule_minutes"])
            except (ValueError, TypeError):
                pass

    if draft_plan:
        session.draft_plan = draft_plan

    # Map next_focus to IntakeState for backwards compat.
    focus_to_state = {
        "SUBJECT": IntakeState.SUBJECT,
        "PRIOR_KNOWLEDGE": IntakeState.PRIOR_KNOWLEDGE,
        "GOALS": IntakeState.GOALS,
        "SCHEDULE": IntakeState.SCHEDULE,
        "MATERIALS": IntakeState.SCHEDULE,  # no MATERIALS state in enum; use SCHEDULE
        "PLAN_DRAFT": IntakeState.GENERATING_PLAN,
        "COMPLETE": IntakeState.COMPLETE,
    }

    # BUG-001-REDUX (Task 15): scoped server-side forcing function.
    # deep_intake=True (opted in, e.g. Moses/NBCM) → unchanged behavior,
    # the model paces itself with no server override, same as Task 12.
    # deep_intake=False (default, e.g. Sameer/Rameez/Freedom Generation
    # students) → the server enforces what the guided system prompt
    # promises, even if the model ignores its instructions. Both cases
    # below require turns_in_focus >= 2 (same threshold as the original
    # Task 11 fix) so the model always gets at least two tries to notice
    # and advance on its own before the server steps in:
    #   (a) all 4 extracted fields filled + stuck 2+ turns → force PLAN_DRAFT;
    #   (b) not yet all filled but stuck 2+ turns in one focus area →
    #       force onward to the next stage in the flow, so no single
    #       dimension can be re-asked indefinitely.
    if not session.deep_intake and next_focus not in ("PLAN_DRAFT", "COMPLETE") \
            and session.turns_in_focus >= 2:
        all_four_filled = all(
            session.extracted.get(k) not in (None, "", 0)
            for k in ("subject", "prior_knowledge", "goals", "schedule_minutes")
        )
        if all_four_filled:
            logger.info(
                "intake_force_advance reason=all_fields_filled from=%s", next_focus,
            )
            next_focus = "PLAN_DRAFT"
            session.turns_in_focus = 0
        else:
            cur_state = focus_to_state.get(next_focus, session.state)
            idx = _state_index(cur_state)
            if idx + 1 < len(_FLOW_ORDER):
                forced_state = _FLOW_ORDER[idx + 1]
                forced_focus = (
                    "PLAN_DRAFT" if forced_state == IntakeState.GENERATING_PLAN
                    else forced_state.value
                )
                logger.info(
                    "intake_force_advance reason=turn_cap from=%s to=%s",
                    next_focus, forced_focus,
                )
                next_focus = forced_focus
                session.turns_in_focus = 0
        session.current_focus = next_focus

    session.state = focus_to_state.get(next_focus, session.state)

    # If the model says COMPLETE and we have a draft plan, kick off the
    # multi-step plan generation pipeline (ADR-003 Phase 3). The pipeline
    # runs as a background job — retrieve structural map, retrieve foundational
    # chunks, LLM gap analysis, retrieve gap-specific chunks, LLM plan design,
    # LLM concept detail + ingest. Returns immediately with a plan_job_id
    # the GUI can poll.
    if next_focus == "COMPLETE" and session.draft_plan:
        # Task 21 Fix 5 — idempotency guard (post-COMPLETE, must-have):
        # If the pipeline already completed for this session (state==COMPLETE
        # AND plan_id is set), return the existing plan_id without
        # re-launching. This blocks the duplicate-plan bug observed in the
        # dogfood session: a re-confirmation message ~20 minutes after the
        # first COMPLETE fired a SECOND background pipeline for the same
        # session+material, producing a 22-concept plan that was never used
        # (placement had already started on the first 36-concept plan).
        #
        # The legacy-fallback path below sets session.state=COMPLETE +
        # session.plan_id on success, so a genuine first-time successful
        # completion is NOT blocked by this guard — only a SECOND
        # successful completion is. The first pipeline-start path returns
        # without setting plan_id (the pipeline runs in background); see
        # the "Known limitation" note below for that case.
        if session.state == IntakeState.COMPLETE and session.plan_id:
            logger.info(
                "plan_pipeline_trigger_skipped_already_complete session_plan_id=%s",
                session.plan_id,
            )
            return {
                "state": "COMPLETE",
                "prompt": response_text,
                "plan_id": session.plan_id,
            }
        # Task 21 Fix 5 — Known limitation (flagged back, NOT closed in
        # this task): the IN-FLIGHT race. If the first COMPLETE trigger
        # took the pipeline-start path (not the legacy fallback), the
        # background pipeline is running but session.plan_id is NOT set
        # (the pipeline hasn't finished). A second COMPLETE trigger would
        # slip past guard #1 (no plan_id) and launch a DUPLICATE pipeline.
        #
        # The Task 21 prompt asked me to check whether session.state gets
        # persisted as GENERATING_PLAN when the background job starts.
        # Answer: NO — it's only in the returned dict, not on the session
        # object. So I attempted to close the race by:
        #   (a) adding `session.state = IntakeState.GENERATING_PLAN` before
        #       supervised_task below, AND
        #   (b) adding a guard #2: `if pre_call_state == GENERATING_PLAN:
        #       return in-flight`.
        #
        # That attempt was AMBIGUOUS and broke the existing tests
        # (test_llm_driven_complete_with_draft_plan_triggers_pipeline and
        # test_full_intake_loop_with_upload_and_draft_plan). Reason:
        # IntakeState.GENERATING_PLAN is ALSO the state mapped from
        # current_focus="PLAN_DRAFT" (see focus_to_state above). The
        # existing tests pre-seed state=GENERATING_PLAN to simulate the
        # legitimate "model is in PLAN_DRAFT phase, about to produce its
        # first COMPLETE" scenario — which my guard #2 would have
        # incorrectly blocked.
        #
        # Disambiguating "PLAN_DRAFT focus, GENERATING_PLAN state"
        # (legitimate first-time COMPLETE) from "pipeline in-flight,
        # GENERATING_PLAN state" (re-trigger) requires more than a
        # one-line guard. The clean fixes are:
        #   (1) A new IntakeState value (e.g. PLAN_PIPELINE_RUNNING) —
        #       schema change, requires a migration + serializer update.
        #   (2) A new boolean field on IntakeSession (e.g.
        #       plan_pipeline_started) — schema change, requires
        #       _session_to_dict / _session_from_dict updates.
        #   (3) Track in-flight tasks by session_id (the container's
        #       _aristotle_plan_tasks dict is keyed by plan_job_id, not
        #       session) — structural change.
        # All three exceed the "one-line guard" scope the Task 21 prompt
        # allows for this secondary gap. Flagging back per the prompt's
        # instruction. The must-have behavior (blocking re-trigger AFTER
        # completion, guard #1 above) is implemented and tested.
        # Try the multi-step pipeline first. Falls back to the legacy
        # single-call generate_plan() if the pipeline fails to start
        # (e.g., no ingested paper, no model provider, or the plan_generator
        # module can't be imported).
        try:
            from aristotle.actors.plan_generator import (
                create_plan_job, generate_plan_pipeline,
            )
            from aip.adapter.extensions.supervision import supervised_task

            material_id = session.material_ids[0] if session.material_ids else None
            plan_job_id = await create_plan_job(ctx.container, session, material_id)

            # Start the background pipeline
            task = supervised_task(
                f"aristotle:plan:{plan_job_id}",
                generate_plan_pipeline(session, ctx.container, plan_job_id),
            )
            if not hasattr(ctx.container, "_aristotle_plan_tasks"):
                ctx.container._aristotle_plan_tasks = {}
            ctx.container._aristotle_plan_tasks[plan_job_id] = task

            logger.info(
                "plan_pipeline_started plan_job_id=%s material_id=%s — "
                "multi-step retrieval-driven plan generation running in background",
                plan_job_id, material_id,
            )

            # Return immediately — the GUI polls /aristotle/plan/{plan_job_id}/status
            # The learner sees "Designing your learning plan..." while the pipeline runs.
            return {
                "state": "GENERATING_PLAN",
                "prompt": response_text or (
                    "I'm now designing your learning plan. This involves analyzing "
                    "the paper's structure, identifying your knowledge gaps, and "
                    "building a phased curriculum. This takes 1-2 minutes — I'll "
                    "let you know when it's ready."
                ),
                "plan_job_id": plan_job_id,
                "plan_job_status": "PENDING",
            }
        except Exception as exc:
            # Pipeline failed to start — fall back to the legacy single-call path.
            # This ensures the learner still gets a plan even if the multi-step
            # pipeline has a bug or the paper wasn't ingested.
            logger.warning(
                "plan_pipeline_start_failed error=%s:%s — falling back to legacy generate_plan",
                type(exc).__name__, exc,
            )
            actor = IntakeActor()
            plan_result = await actor.generate_plan(ctx, session)
            if plan_result.ok and plan_result.data:
                session.plan_id = plan_result.data.get("plan_id", "")
                session.state = IntakeState.COMPLETE
                return {
                    "state": "COMPLETE",
                    "prompt": response_text,
                    "plan_id": session.plan_id,
                    "concept_count": plan_result.data.get("concept_count", len(session.draft_plan)),
                }
            else:
                return {
                    "state": "GENERATING_PLAN",
                    "prompt": response_text or "I had trouble saving the plan. Could you confirm again?",
                    "error": plan_result.error,
                }

    return {
        "state": session.state.value,
        "prompt": response_text,
    }


def _parse_json_response(raw: str) -> dict | None:
    """Extract the first JSON object from a model response.

    Handles: pure JSON, JSON wrapped in ```json fences, JSON with
    preamble text before it. Returns None if no valid JSON found.
    """
    if not raw:
        return None
    raw = raw.strip()

    # Try direct parse first.
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from markdown code fences.
    import re

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding the first { ... } block (greedy, handles nested braces).
    brace_match = re.search(r"\{.*\}", raw, re.S)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


async def _run_intake_step_deterministic(
    session: IntakeSession,
    student_input: str,
    ctx: ActorContext,
) -> dict:
    """Deterministic intake dispatcher (fallback when no model_provider).

    This is the original Phase D implementation — fixed templates with
    verbatim subject interpolation, one turn per state. Kept as the
    fallback path so the system works in tests / offline mode. The
    LLM-driven path (run_intake_step above) is the primary path when
    a model is configured.
    """
    actor = IntakeActor()

    # If the current state is before entry_state (mid-flow entry), jump
    # to entry_state on the first call.
    if _state_index(session.state) < _state_index(session.entry_state):
        session.state = session.entry_state

    if session.state == IntakeState.COMPLETE:
        return {"state": "COMPLETE", "plan_id": session.plan_id}

    if session.state == IntakeState.GENERATING_PLAN:
        # Single-phase: generate the plan + advance to COMPLETE.
        result = await actor.generate_plan(ctx, session)
        if result.ok and result.data:
            session.plan_id = result.data.get("plan_id", "")
            session.state = IntakeState.COMPLETE
            return {
                "state": "COMPLETE",
                "plan_id": session.plan_id,
                "concept_count": result.data.get("concept_count", 0),
            }
        else:
            return {
                "state": "GENERATING_PLAN",
                "error": result.error or "plan generation failed",
            }

    # Two-phase states: check if we have student_input (phase 2) or not (phase 1).
    # Phase 1: generate the prompt for the current state.
    # Phase 2: record the answer + advance to the next state.

    if not student_input and session.state != IntakeState.GREETING:
        # Phase 1: generate the prompt (except GREETING which is the entry
        # point — its prompt is generated on the first call regardless).
        pass

    # Dispatch based on session.state.
    if session.state == IntakeState.GREETING:
        if not student_input:
            # Phase 1: generate greeting.
            result = await actor.greet(ctx)
            session.state = IntakeState.SUBJECT
            session.current_focus = "SUBJECT"
            return {"state": "GREETING", "prompt": result.data["prompt"]}
        else:
            # Phase 2: record subject + advance.
            session.subject = student_input
            session.responses.append(student_input)
            session.state = IntakeState.PRIOR_KNOWLEDGE
            session.current_focus = "PRIOR_KNOWLEDGE"
            result = await actor.ask_prior_knowledge(ctx, session.subject)
            return {"state": "SUBJECT", "prompt": result.data["prompt"]}

    elif session.state == IntakeState.SUBJECT:
        if not student_input:
            result = await actor.greet(ctx)
            return {"state": "GREETING", "prompt": result.data["prompt"]}
        else:
            session.subject = student_input
            session.responses.append(student_input)
            session.state = IntakeState.PRIOR_KNOWLEDGE
            session.current_focus = "PRIOR_KNOWLEDGE"
            result = await actor.ask_prior_knowledge(ctx, session.subject)
            return {"state": "PRIOR_KNOWLEDGE", "prompt": result.data["prompt"]}

    elif session.state == IntakeState.PRIOR_KNOWLEDGE:
        if not student_input:
            result = await actor.ask_prior_knowledge(ctx, session.subject)
            return {"state": "PRIOR_KNOWLEDGE", "prompt": result.data["prompt"]}
        else:
            session.prior_knowledge = student_input
            session.responses.append(student_input)
            session.state = IntakeState.GOALS
            session.current_focus = "GOALS"
            result = await actor.ask_goals(ctx, session.subject)
            return {"state": "GOALS", "prompt": result.data["prompt"]}

    elif session.state == IntakeState.GOALS:
        if not student_input:
            result = await actor.ask_goals(ctx, session.subject)
            return {"state": "GOALS", "prompt": result.data["prompt"]}
        else:
            session.goals = student_input
            session.responses.append(student_input)
            session.state = IntakeState.SCHEDULE
            session.current_focus = "SCHEDULE"
            result = await actor.ask_schedule(ctx)
            return {"state": "SCHEDULE", "prompt": result.data["prompt"]}

    elif session.state == IntakeState.SCHEDULE:
        if not student_input:
            result = await actor.ask_schedule(ctx)
            return {"state": "SCHEDULE", "prompt": result.data["prompt"]}
        else:
            # Parse the schedule (try int, default 30).
            try:
                session.schedule_minutes = int(
                    "".join(c for c in student_input if c.isdigit()) or "30"
                )
            except (ValueError, TypeError):
                session.schedule_minutes = 30
            session.responses.append(student_input)
            session.state = IntakeState.GENERATING_PLAN
            session.current_focus = "PLAN_DRAFT"
            # Immediately generate the plan (single-phase).
            return await _run_intake_step_deterministic(session, "", ctx)

    # Fallback (should not reach here).
    return {"state": str(session.state), "prompt": ""}


# ---------------------------------------------------------------------------
# Serialization helpers (used by api.py)
# ---------------------------------------------------------------------------


def intake_session_to_dict(session: IntakeSession) -> dict:
    """Serialize an IntakeSession to a JSON-safe dict."""
    return {
        "state": session.state.value,
        "entry_state": session.entry_state.value,
        "plan_id": session.plan_id,
        "subject": session.subject,
        "prior_knowledge": session.prior_knowledge,
        "goals": session.goals,
        "schedule_minutes": session.schedule_minutes,
        "responses": list(session.responses),
        # Phase D brain-transplant fields
        "material_ids": list(session.material_ids),
        "extracted": dict(session.extracted),
        "draft_plan": list(session.draft_plan),
        "current_focus": session.current_focus,
        "plan_confirmed": session.plan_confirmed,
        # turns_in_focus persists across API calls so the counter
        # survives the round-trip through /intake/step.
        "turns_in_focus": session.turns_in_focus,
        "deep_intake": session.deep_intake,
        # Task 18 (ADR-004): student_id round-trips so the value set at
        # /intake/start reaches generate_plan() at /intake/step's COMPLETE
        # transition. Default 'definer' for legacy sessions.
        "student_id": session.student_id,
    }


def intake_session_from_dict(d: dict) -> IntakeSession:
    """Deserialize an IntakeSession from a dict."""
    return IntakeSession(
        state=IntakeState(d.get("state", "GREETING")),
        entry_state=IntakeState(d.get("entry_state", "GREETING")),
        plan_id=d.get("plan_id", ""),
        subject=d.get("subject", ""),
        prior_knowledge=d.get("prior_knowledge", ""),
        goals=d.get("goals", ""),
        schedule_minutes=d.get("schedule_minutes", 30),
        responses=d.get("responses", []),
        # Phase D brain-transplant fields
        material_ids=d.get("material_ids", []),
        extracted=d.get("extracted", {}),
        draft_plan=d.get("draft_plan", []),
        current_focus=d.get("current_focus", "SUBJECT"),
        plan_confirmed=d.get("plan_confirmed", False),
        # Restore turns_in_focus (default 0 for legacy sessions).
        turns_in_focus=d.get("turns_in_focus", 0),
        # Restore deep_intake (default False — legacy sessions and new
        # sessions both get the guided/fast path unless opted in).
        deep_intake=d.get("deep_intake", False),
        # Restore student_id (default 'definer' for legacy sessions
        # serialized before Task 18).
        student_id=d.get("student_id", "definer"),
    )


# ---------------------------------------------------------------------------
# PLACER — placement calibration (ADR-002 §9 stage 5, §11)
# ---------------------------------------------------------------------------


@dataclass
class PlacerSession:
    """Per-placement-calibration state (passed between steps).

    PLACER probes a sample of concepts across the plan to determine what
    the learner already knows, then sets the starting point
    (learning_plan.current_concept_idx). Reuses ExaminerActor — no new
    model slots needed.
    """

    plan_id: str = ""
    concepts_to_assess: list = field(default_factory=list)
    current_idx: int = 0
    current_question: str = ""
    question_generated: bool = False
    results: list = field(default_factory=list)
    # Each entry in results: {concept_id, score, mastery_achieved}
    state: str = "PROBING"  # or "COMPLETE"


def _sample_concepts_for_placement(concept_ids: list, n: int = 7) -> list:
    """Select n concepts distributed evenly across the list.

    Distributes the sample across beginning, middle, and end — not just
    the first n. If len(concept_ids) <= n, returns all.

    Pure function, no DB. Used by run_placer_step to select which concepts
    to probe during placement calibration.
    """
    if len(concept_ids) <= n:
        return list(concept_ids)

    # Evenly spaced indices: i * len / n for i in range(n).
    step = len(concept_ids) / n
    indices = [int(i * step) for i in range(n)]
    # Deduplicate (can happen if step < 1, though we guard against that above).
    seen = set()
    result = []
    for idx in indices:
        if idx not in seen and idx < len(concept_ids):
            seen.add(idx)
            result.append(concept_ids[idx])
    return result


async def run_placer_step(
    session: PlacerSession,
    student_input: str,
    ctx: ActorContext,
) -> dict:
    """Dispatch one placement step based on session state.

    Two-phase per concept (same pattern as QUIZ):
      Phase 1 (question_generated=False): call examiner.probe(ctx,
        concept_id) for the current concept. Set question_generated=True.
        Return {"state": "PROBING", "question": ...}.
      Phase 2 (question_generated=True, student_input provided): call
        examiner.evaluate(ctx, concept_id, student_input). Parse score +
        mastery_achieved from result.data. Write one row to
        aristotle_placement_event (best-effort). Append to session.results.
        If mastery_achieved: upsert aristotle_mastery (repetitions=3 so
        SM-2 treats it as known). Advance to next concept. If all concepts
        assessed: call _finalize_placement + set state="COMPLETE".

    Returns: {"state": "PROBING"|"COMPLETE", "question": ...,
              "concepts_placed": len(results)} or
              {"state": "COMPLETE", "concepts_placed": N,
               "concepts_known": M, "starting_concept_idx": K}.
    """
    from aristotle.actors.examiner import ExaminerActor

    examiner = ExaminerActor()
    logger = ctx.logger

    if session.state == "COMPLETE":
        return {
            "state": "COMPLETE",
            "concepts_placed": len(session.results),
            "concepts_known": sum(
                1 for r in session.results if r.get("mastery_achieved")
            ),
        }

    if session.current_idx >= len(session.concepts_to_assess):
        # All concepts assessed — finalize.
        next_concept_id = await _finalize_placement(session, ctx)
        session.state = "COMPLETE"
        return {
            "state": "COMPLETE",
            "concepts_placed": len(session.results),
            "concepts_known": sum(
                1 for r in session.results if r.get("mastery_achieved")
            ),
            "next_concept_id": next_concept_id,
        }

    concept_id = session.concepts_to_assess[session.current_idx]

    if not session.question_generated:
        # Phase 1: generate the probe question.
        result = await examiner.probe(ctx, concept_id)
        if result.ok and result.data:
            session.current_question = result.data.get("question", "")
            session.question_generated = True
            logger.info(
                "placer_probe_generated concept=%s idx=%d",
                concept_id,
                session.current_idx,
            )
            return {
                "state": "PROBING",
                "question": session.current_question,
                "concepts_placed": len(session.results),
            }
        else:
            return {
                "state": "PROBING",
                "error": result.error or "probe failed",
                "concepts_placed": len(session.results),
            }
    else:
        # Phase 2: evaluate the learner's answer.
        #
        # Task 24 Fix 3: before sending student_input to examiner.evaluate()
        # (which grades it as a content answer), classify it using the
        # existing _classify_student_input system (ADR-002 Amendment A1).
        # If the student asked a QUESTION, raised a TANGENT, or sent CHAT,
        # route to _step_curiosity / _step_chat instead — answer the
        # question or acknowledge the chat WITHOUT advancing session state
        # or grading anything. The same probe question stays pending
        # afterward, exactly like the regular session loop leaves
        # SessionState unchanged.
        #
        # This fixes the live bug where a student asking "give me a rundown
        # on our learning plan" during placement had their question sent
        # to examiner.evaluate() as a content answer, graded, and then the
        # placer advanced to the next concept — ignoring the question
        # entirely and skipping the concept the student was being probed on.
        from aristotle.session import (
            _classify_student_input,
            _step_chat,
            _step_curiosity,
        )

        intent_class = _classify_student_input(student_input)
        if intent_class in ("QUESTION", "TANGENT"):
            # Answer the question / address the tangent. Do NOT advance
            # current_idx, do NOT append to results, do NOT change
            # question_generated or current_question — the same probe
            # question must still be pending afterward.
            #
            # Task 24 Fix 3: pass concept_id + session_id explicitly so
            # _step_curiosity + _log_curiosity_event work with a
            # PlacerSession (which has no concept_id or student_id field).
            # The placer_session_id is derived from plan_id + concept_id
            # so multiple placement curiosity events are distinguishable.
            placer_session_id = f"placer:{session.plan_id}:{concept_id}"
            curiosity_result = await _step_curiosity(
                ctx,
                session,  # PlacerSession — _step_curiosity tolerates it via Fix 1
                student_input,
                intent_class,
                concept_id=concept_id,
                session_id=placer_session_id,
            )

            # Return without advancing. The response text is in
            # curiosity_result.data["response"] (or curiosity_result.error
            # for backward compat). Match the existing placer return shape
            # so the API/GUI can render it.
            response_text = ""
            if curiosity_result.ok:
                if curiosity_result.data and isinstance(curiosity_result.data, dict):
                    response_text = curiosity_result.data.get("response", "")
                else:
                    response_text = curiosity_result.error or ""
            else:
                response_text = (
                    "I had trouble with that just now — could you say that again?"
                )
            return {
                "state": "PROBING",
                "question": session.current_question,
                "response": response_text,
                "intent_class": intent_class,
                "concepts_placed": len(session.results),
            }
        elif intent_class == "CHAT":
            # Acknowledge the chat. Same non-advancing behavior.
            chat_result = await _step_chat(ctx, session, student_input)
            response_text = ""
            if chat_result.ok:
                if chat_result.data and isinstance(chat_result.data, dict):
                    response_text = chat_result.data.get("response", "")
                else:
                    response_text = chat_result.error or ""
            else:
                response_text = (
                    "I had trouble with that just now — could you say that again?"
                )
            return {
                "state": "PROBING",
                "question": session.current_question,
                "response": response_text,
                "intent_class": "CHAT",
                "concepts_placed": len(session.results),
            }
        # else: ANSWER — fall through to the existing examiner.evaluate() path.

        eval_result = await examiner.evaluate(
            ctx,
            concept_id,
            student_answer=student_input,
            quiz_question=session.current_question,
        )

        if not eval_result.ok:
            return {
                "state": "PROBING",
                "error": eval_result.error or "evaluate failed",
                "concepts_placed": len(session.results),
            }

        # Parse score + mastery from result.data.
        eval_data = eval_result.data or {}
        score = float(eval_data.get("score", 0.0))
        mastery_achieved = bool(eval_data.get("mastery_achieved", False))

        # Record the result.
        session.results.append(
            {
                "concept_id": concept_id,
                "score": score,
                "mastery_achieved": mastery_achieved,
            }
        )

        # Write placement_event row (best-effort).
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is not None:
            try:
                stores = await registry.get_stores("aristotle:textbook")
                conn = stores.connection_manager.write_conn
                now = datetime.now(timezone.utc).isoformat()
                # Task 23 Fix 3 — include the raw student_answer text in
                # the placement_event row. M010 added the student_answer
                # column for audit-trail purposes: without it, there's no
                # way to retroactively distinguish a corrupted mastery row
                # (model self-reported mastery_achieved=true on a refusal)
                # from a legitimately-scored one. The column is nullable,
                # so old DBs that pre-date M010 would tolerate a NULL here
                # — but the migration runner applies M010 before this code
                # path runs, so the column always exists in practice.
                await conn.execute(
                    "INSERT INTO aristotle_placement_event "
                    "(plan_id, concept_id, score, mastery_achieved, assessed_at, student_answer) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session.plan_id,
                        concept_id,
                        score,
                        1 if mastery_achieved else 0,
                        now,
                        student_input,
                    ),
                )
                await conn.commit()
            except Exception as exc:
                logger.warning(
                    "placer_placement_event_write_failed concept=%s error=%s:%s",
                    concept_id,
                    type(exc).__name__,
                    exc,
                )

            # If mastered, upsert aristotle_mastery (repetitions=3 so SM-2
            # treats it as known — skips the early TEACH steps).
            if mastery_achieved:
                try:
                    stores = await registry.get_stores("aristotle:textbook")
                    conn = stores.connection_manager.write_conn
                    now = datetime.now(timezone.utc).isoformat()
                    await conn.execute(
                        "INSERT OR REPLACE INTO aristotle_mastery "
                        "(student_id, concept_id, easiness_factor, interval_days, "
                        "repetitions, next_review_at, last_score, mastered, updated_at) "
                        "VALUES (?, ?, 2.5, 6, 3, ?, ?, 1, ?)",
                        (
                            "definer",
                            concept_id,
                            now,  # next_review_at = now (due immediately for cold-start)
                            score,
                            now,
                        ),
                    )
                    await conn.commit()
                    logger.info(
                        "placer_mastery_upserted concept=%s score=%.2f",
                        concept_id,
                        score,
                    )
                except Exception as exc:
                    logger.warning(
                        "placer_mastery_upsert_failed concept=%s error=%s:%s",
                        concept_id,
                        type(exc).__name__,
                        exc,
                    )

        # Advance to the next concept.
        session.current_idx += 1
        session.question_generated = False
        session.current_question = ""

        if session.current_idx >= len(session.concepts_to_assess):
            # All concepts assessed — finalize.
            next_concept_id = await _finalize_placement(session, ctx)
            session.state = "COMPLETE"
            return {
                "state": "COMPLETE",
                "concepts_placed": len(session.results),
                "concepts_known": sum(
                    1 for r in session.results if r.get("mastery_achieved")
                ),
                "next_concept_id": next_concept_id,
            }

        # More concepts to assess — generate the next question immediately
        # (non-interactive mode: the caller can provide the next answer on
        # the next call).
        next_concept = session.concepts_to_assess[session.current_idx]
        next_result = await examiner.probe(ctx, next_concept)
        if next_result.ok and next_result.data:
            session.current_question = next_result.data.get("question", "")
            session.question_generated = True
        return {
            "state": "PROBING",
            "question": session.current_question if session.question_generated else "",
            "concepts_placed": len(session.results),
        }


async def _finalize_placement(session: PlacerSession, ctx: ActorContext) -> str | None:
    """Find the first non-mastered concept + set current_concept_idx on the plan.

    Reads aristotle_learning_plan.concept_ids_json, finds the first concept
    that did NOT achieve mastery in placement, updates
    current_concept_idx to that position. If all concepts mastered, sets
    status='complete'.

    Task 17: returns the resolved next_concept_id (or None if the plan is
    already fully mastered) so callers can hand tutoring the CORRECT
    starting concept directly, instead of the GUI falling back to
    GET /aristotle/concepts and taking whatever happens to be first in the
    entire shared, unscoped table (see ask.py's _start_tutoring — that
    fallback is where "newton_first_law", a leftover dogfood-bootstrap
    concept from concepts_sample.yaml, was silently returned for every
    student's very first tutoring concept, since it was the oldest row in
    aristotle_concept regardless of which plan was actually active).

    Best-effort — non-fatal on DB error (returns None).
    """
    logger = ctx.logger
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return None

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # Read the plan's concept sequence.
        cur = await conn.execute(
            "SELECT concept_ids_json FROM aristotle_learning_plan WHERE id = ?",
            (session.plan_id,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is None:
            return None

        concept_ids = json.loads(row[0]) if row[0] else []

        # Build a set of mastered concept_ids from placement results.
        mastered_ids = {
            r["concept_id"] for r in session.results if r.get("mastery_achieved")
        }

        # Find the first non-mastered concept.
        starting_idx = 0
        all_mastered = True
        for idx, cid in enumerate(concept_ids):
            if cid not in mastered_ids:
                starting_idx = idx
                all_mastered = False
                break

        if all_mastered:
            # All concepts mastered — mark plan complete.
            await conn.execute(
                "UPDATE aristotle_learning_plan SET status = 'complete' WHERE id = ?",
                (session.plan_id,),
            )
            await conn.commit()
            logger.info(
                "placer_finalized plan=%s — all %d concepts mastered, plan complete",
                session.plan_id,
                len(concept_ids),
            )
            return None
        else:
            await conn.execute(
                "UPDATE aristotle_learning_plan SET current_concept_idx = ? WHERE id = ?",
                (starting_idx, session.plan_id),
            )
            await conn.commit()
            next_concept_id = (
                concept_ids[starting_idx] if starting_idx < len(concept_ids) else None
            )
            logger.info(
                "placer_finalized plan=%s — starting at idx=%d (concept=%s)",
                session.plan_id,
                starting_idx,
                next_concept_id if next_concept_id else "?",
            )
            return next_concept_id
    except Exception as exc:
        logger.warning(
            "placer_finalize_failed plan=%s error=%s:%s",
            session.plan_id,
            type(exc).__name__,
            exc,
        )


# ---------------------------------------------------------------------------
# PLACER serialization helpers (used by api.py)
# ---------------------------------------------------------------------------


def placer_session_to_dict(session: PlacerSession) -> dict:
    """Serialize a PlacerSession to a JSON-safe dict."""
    return {
        "plan_id": session.plan_id,
        "concepts_to_assess": list(session.concepts_to_assess),
        "current_idx": session.current_idx,
        "current_question": session.current_question,
        "question_generated": session.question_generated,
        "results": list(session.results),
        "state": session.state,
    }


def placer_session_from_dict(d: dict) -> PlacerSession:
    """Deserialize a PlacerSession from a dict."""
    return PlacerSession(
        plan_id=d.get("plan_id", ""),
        concepts_to_assess=d.get("concepts_to_assess", []),
        current_idx=d.get("current_idx", 0),
        current_question=d.get("current_question", ""),
        question_generated=d.get("question_generated", False),
        results=d.get("results", []),
        state=d.get("state", "PROBING"),
    )
