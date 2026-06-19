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
    """
    state: IntakeState = IntakeState.GREETING
    entry_state: IntakeState = IntakeState.GREETING
    plan_id: str = ""
    subject: str = ""
    prior_knowledge: str = ""
    goals: str = ""
    schedule_minutes: int = 30
    responses: list = field(default_factory=list)


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
                return ActorResult(ok=False, error="corpus aristotle:textbook not found")
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

    async def ask_prior_knowledge(
        self, ctx: ActorContext, subject: str
    ) -> ActorResult:
        """Asks what the learner already knows about the subject.

        Low-pressure framing — "none at all is a perfectly fine answer."
        """
        prompt = (
            f"Great — {subject}. Before we start, how much do you already "
            f"know about {subject}? None at all is a perfectly fine answer "
            f"— I just want to know where to start."
        )
        return ActorResult(ok=True, data={"prompt": prompt})

    async def ask_goals(
        self, ctx: ActorContext, subject: str
    ) -> ActorResult:
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
        """Generate a learning plan from the intake responses.

        Queries aristotle_concept for concepts matching the subject (or all
        concepts if the subject is broad / no match), orders by prerequisite
        depth or insertion order, writes one row to aristotle_learning_plan,
        writes one row to aristotle_intake_session (status='complete',
        completed_at=now). Returns ActorResult(ok=True, data={"plan_id": ...}).
        """
        logger = ctx.logger
        container: Any = ctx.container
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return ActorResult(ok=False, error="corpus_registry not available")

        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn

            # Query concepts matching the subject. If the subject is broad
            # or no exact match, fall back to all concepts ordered by
            # insertion order (id ASC).
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
                cur = await conn.execute(
                    "SELECT id FROM aristotle_concept ORDER BY id"
                )
                rows = await cur.fetchall()
                await cur.close()
                concept_ids = [row[0] for row in rows] if rows else []

            # Generate a UUID for the plan.
            plan_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            concept_ids_json = json.dumps(concept_ids)

            # Write the learning_plan row.
            await conn.execute(
                "INSERT INTO aristotle_learning_plan "
                "(id, subject, goals, schedule_minutes_per_day, "
                "concept_ids_json, current_concept_idx, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    session.subject,
                    session.goals,
                    session.schedule_minutes,
                    concept_ids_json,
                    0,
                    "active",
                    now,
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

            await conn.commit()
            logger.info(
                "intake_plan_generated plan_id=%s subject=%s concept_count=%d",
                plan_id, session.subject, len(concept_ids),
            )
            return ActorResult(
                ok=True,
                data={"plan_id": plan_id, "concept_count": len(concept_ids)},
            )
        except Exception as exc:
            logger.warning(
                "intake_plan_generation_failed error=%s:%s",
                type(exc).__name__, exc,
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
    "new topic", "start over", "different subject",
    "learn something else", "reset", "something new",
]
_GOALS_KEYWORDS = [
    "exam", "deadline", "goal changed", "target",
    "need to pass", "interview",
]
_SCHEDULE_KEYWORDS = [
    "busier", "less time", "only have", "minutes a day",
    "schedule", "not enough time",
]
_PLAN_KEYWORDS = [
    "add more topics", "go deeper", "extend my plan", "more advanced",
]


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
                prompt=(
                    "You've completed your plan. Want to start something new?"
                ),
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
                    subject_phrase = f"still working on {subject}" if subject else "ready for something new"
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


async def run_intake_step(
    session: IntakeSession,
    student_input: str,
    ctx: ActorContext,
) -> dict:
    """Dispatch one intake step based on session.state.

    Two-phase per state (first call = generate prompt, second call with
    student_input = record answer + advance). GENERATING_PLAN is
    single-phase (calls generate_plan + advances to COMPLETE). COMPLETE
    returns {"state": "COMPLETE", "plan_id": session.plan_id}.

    Supports mid-flow entry via session.entry_state — skip states before
    entry_state on first call. For partial re-INTAKE, entry_state is set
    to the mid-flow state (GOALS, SCHEDULE, etc.) and the dispatcher
    jumps directly there.

    Returns: {"state": ..., "prompt": ...} or {"state": "COMPLETE",
    "plan_id": ...}.
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
            return {"state": "GREETING", "prompt": result.data["prompt"]}
        else:
            # Phase 2: record subject + advance.
            session.subject = student_input
            session.responses.append(student_input)
            session.state = IntakeState.PRIOR_KNOWLEDGE
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
            # Immediately generate the plan (single-phase).
            return await run_intake_step(session, "", ctx)

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
    )
