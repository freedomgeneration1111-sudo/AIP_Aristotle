"""Tests for the INTAKE actor + intent detection + trigger checking + API routes.

Phase D (ADR-002 Rev 2 §9, §11). Tests the INTAKE conversation flow,
intent detection (keyword-based), trigger checking (system-side re-surfacing),
and the two API routes (/intake/start, /intake/step).

Run:  pytest tests/test_aristotle_intake.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aip.foundation.protocols.actors import ActorContext
from aristotle.actors.intake import (
    IntakeActor,
    IntakeSession,
    IntakeState,
    _detect_intake_intent,
    check_intake_triggers,
    run_intake_step,
    intake_session_to_dict,
)


# ---------------------------------------------------------------------------
# Fakes (same pattern as test_aristotle_tutoring.py)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Fake aiosqlite.Connection for testing."""

    def __init__(self, rows: list[tuple] | None = None):
        self._rows = rows or []
        self._executed: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple = ()):
        self._executed.append((sql, params))
        return _FakeCursor(self._rows)

    async def commit(self):
        pass


class _FakeModelProvider:
    """Fake ModelProvider that returns canned responses by slot."""

    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, list[dict]]] = []

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        content = self._responses.get(slot_name, f"[fake {slot_name} response]")
        return {
            "content": content,
            "model": "fake-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "latency_ms": 5,
        }


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def close(self):
        pass


class _FakeStores:
    def __init__(self, write_conn):
        self.connection_manager = type("CM", (), {"write_conn": write_conn})()


class _FakeRegistry:
    def __init__(self, stores):
        self._stores = stores

    async def get_stores(self, corpus_id: str, **kwargs):
        return self._stores


def _make_ctx(
    model_provider: Any | None = None,
    config: Any | None = None,
    stores: Any | None = None,
) -> ActorContext:
    """Build a minimal ActorContext for testing."""
    container = type(
        "C",
        (),
        {
            "model_provider": model_provider,
            "corpus_registry": _FakeRegistry(stores) if stores else None,
        },
    )()
    return ActorContext(
        container=container,
        config=config,
        logger=__import__("logging").getLogger("test"),
        cancel_event=asyncio.Event(),
    )


# ---------------------------------------------------------------------------
# Actor-level tests
# ---------------------------------------------------------------------------


class TestIntakeActor:
    @pytest.mark.asyncio
    async def test_intake_greeting_returns_prompt(self):
        """IntakeActor.greet() returns ok=True with data.prompt as a string."""
        ctx = _make_ctx(stores=_FakeStores(_FakeConn()))
        actor = IntakeActor()
        result = await actor.greet(ctx)
        assert result.ok
        assert result.data is not None
        assert "prompt" in result.data
        assert isinstance(result.data["prompt"], str)
        assert len(result.data["prompt"]) > 0
        assert "subject" in result.data["prompt"].lower()

    @pytest.mark.asyncio
    async def test_intake_generate_plan_creates_learning_plan_row(self):
        """generate_plan() writes one row to aristotle_learning_plan."""
        # Fake conn returns 2 concept rows for the subject query.
        conn = _FakeConn(rows=[("c1",), ("c2",)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        actor = IntakeActor()
        session = IntakeSession(
            subject="Physics",
            prior_knowledge="some high school",
            goals="pass the exam",
            schedule_minutes=30,
        )
        result = await actor.generate_plan(ctx, session)
        assert result.ok
        assert result.data["plan_id"]  # UUID generated
        # Verify the INSERT into aristotle_learning_plan was issued.
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT INTO aristotle_learning_plan" in sql
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_intake_generate_plan_creates_intake_session_row(self):
        """generate_plan() writes one row to aristotle_intake_session (status=complete)."""
        conn = _FakeConn(rows=[("c1",), ("c2",)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        actor = IntakeActor()
        session = IntakeSession(
            subject="Physics",
            prior_knowledge="none",
            goals="personal interest",
            schedule_minutes=20,
        )
        result = await actor.generate_plan(ctx, session)
        assert result.ok
        # Verify the INSERT into aristotle_intake_session was issued.
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT INTO aristotle_intake_session" in sql
        ]
        assert len(insert_calls) == 1
        # The INSERT should include status='complete'.
        sql, params = [
            (sql, params)
            for sql, params in conn._executed
            if "INSERT INTO aristotle_intake_session" in sql
        ][0]
        assert "complete" in sql or "complete" in str(params)


# ---------------------------------------------------------------------------
# Intent detection tests (pure unit, no async)
# ---------------------------------------------------------------------------


class TestDetectIntakeIntent:
    def test_detect_intent_exam_routes_to_goals(self):
        """'exam' keyword triggers partial re-INTAKE at GOALS."""
        trigger = _detect_intake_intent("I have an exam coming up")
        assert trigger is not None
        assert trigger.level == "partial"
        assert trigger.entry_state == IntakeState.GOALS

    def test_detect_intent_schedule_routes_to_schedule(self):
        """'busier' keyword triggers partial re-INTAKE at SCHEDULE."""
        trigger = _detect_intake_intent("I'm busier now")
        assert trigger is not None
        assert trigger.level == "partial"
        assert trigger.entry_state == IntakeState.SCHEDULE

    def test_detect_intent_new_topic_routes_to_full(self):
        """'new topic' keyword triggers full re-INTAKE."""
        trigger = _detect_intake_intent("I want to learn a new topic")
        assert trigger is not None
        assert trigger.level == "full"
        assert trigger.entry_state == IntakeState.GREETING

    def test_detect_intent_no_match_returns_none(self):
        """No keyword match returns None (no trigger)."""
        trigger = _detect_intake_intent("I understand inertia now")
        assert trigger is None


# ---------------------------------------------------------------------------
# Trigger checking tests
# ---------------------------------------------------------------------------


class TestCheckIntakeTriggers:
    @pytest.mark.asyncio
    async def test_check_triggers_no_plan_returns_full(self):
        """plan_id=None → full trigger."""
        ctx = _make_ctx(stores=_FakeStores(_FakeConn()))
        trigger = await check_intake_triggers(ctx, None)
        assert trigger is not None
        assert trigger.level == "full"
        assert trigger.entry_state == IntakeState.GREETING

    @pytest.mark.asyncio
    async def test_check_triggers_complete_plan_returns_full(self):
        """status='complete' → full trigger with 'completed' prompt."""
        conn = _FakeConn(rows=[("Physics", "complete", None, 0)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        trigger = await check_intake_triggers(ctx, "plan-123")
        assert trigger is not None
        assert trigger.level == "full"
        assert "completed" in trigger.prompt.lower()

    @pytest.mark.asyncio
    async def test_check_triggers_long_absence_returns_checkin(self):
        """days since last_session > 14 → checkin trigger at GREETING."""
        from datetime import datetime, timezone, timedelta

        old_date = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        conn = _FakeConn(rows=[("Physics", "active", old_date, 0)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        trigger = await check_intake_triggers(ctx, "plan-123")
        assert trigger is not None
        assert trigger.level == "checkin"
        assert trigger.entry_state == IntakeState.GREETING
        assert "welcome back" in trigger.prompt.lower()

    @pytest.mark.asyncio
    async def test_check_triggers_missed_sessions_returns_schedule(self):
        """consecutive_missed_sessions > 3 → checkin at SCHEDULE."""
        conn = _FakeConn(rows=[("Physics", "active", None, 5)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        trigger = await check_intake_triggers(ctx, "plan-123")
        assert trigger is not None
        assert trigger.level == "checkin"
        assert trigger.entry_state == IntakeState.SCHEDULE
        assert "schedule" in trigger.prompt.lower()


# ---------------------------------------------------------------------------
# Session flow tests
# ---------------------------------------------------------------------------


class TestIntakeSessionFlow:
    @pytest.mark.asyncio
    async def test_intake_advances_through_all_states(self):
        """A full intake advances GREETING → SUBJECT → PRIOR → GOALS → SCHEDULE → COMPLETE."""
        ctx = _make_ctx(stores=_FakeStores(_FakeConn(rows=[("c1",)])))
        session = IntakeSession()

        # Step 1: GREETING (no input → generates greeting, advances to SUBJECT)
        result = await run_intake_step(session, "", ctx)
        assert "prompt" in result
        assert session.state == IntakeState.SUBJECT

        # Step 2: SUBJECT (input="Physics" → records, advances to PRIOR_KNOWLEDGE)
        result = await run_intake_step(session, "Physics", ctx)
        assert session.subject == "Physics"
        assert session.state == IntakeState.PRIOR_KNOWLEDGE

        # Step 3: PRIOR_KNOWLEDGE (input="some high school" → advances to GOALS)
        result = await run_intake_step(session, "some high school", ctx)
        assert session.prior_knowledge == "some high school"
        assert session.state == IntakeState.GOALS

        # Step 4: GOALS (input="pass the exam" → advances to SCHEDULE)
        result = await run_intake_step(session, "pass the exam", ctx)
        assert session.goals == "pass the exam"
        assert session.state == IntakeState.SCHEDULE

        # Step 5: SCHEDULE (input="30 minutes" → advances to GENERATING_PLAN → COMPLETE)
        result = await run_intake_step(session, "30 minutes", ctx)
        assert session.schedule_minutes == 30
        assert session.state == IntakeState.COMPLETE
        assert session.plan_id  # UUID generated

    @pytest.mark.asyncio
    async def test_intake_partial_entry_skips_to_goals(self):
        """Partial re-INTAKE with entry_state=GOALS skips directly to GOALS."""
        ctx = _make_ctx(stores=_FakeStores(_FakeConn(rows=[("c1",)])))
        session = IntakeSession(
            state=IntakeState.GREETING,
            entry_state=IntakeState.GOALS,
            subject="Physics",  # already known from prior intake
        )
        # First call: should jump to GOALS (skip GREETING/SUBJECT/PRIOR).
        result = await run_intake_step(session, "", ctx)
        assert session.state == IntakeState.GOALS
        assert "prompt" in result

    @pytest.mark.asyncio
    async def test_intake_start_route_returns_greeting(self):
        """POST /intake/start with no plan_id returns a greeting prompt."""
        from aristotle.api import intake_start_route

        container = type(
            "C",
            (),
            {
                "corpus_registry": _FakeRegistry(_FakeStores(_FakeConn())),
            },
        )()
        request = type(
            "R",
            (),
            {
                "app": type(
                    "A",
                    (),
                    {
                        "state": type("S", (), {"container": container})(),
                    },
                )(),
            },
        )()

        async def _json():
            return {"plan_id": None}

        request.json = _json

        result = await intake_start_route(request)
        assert result["trigger"] == "full"
        assert result["prompt"] is not None
        assert "subject" in result["prompt"].lower()

    @pytest.mark.asyncio
    async def test_intake_step_route_advances_state(self):
        """POST /intake/step with a subject input advances to PRIOR_KNOWLEDGE."""
        from aristotle.api import intake_step_route

        container = type(
            "C",
            (),
            {
                "corpus_registry": _FakeRegistry(
                    _FakeStores(_FakeConn(rows=[("c1",)]))
                ),
            },
        )()
        request = type(
            "R",
            (),
            {
                "app": type(
                    "A",
                    (),
                    {
                        "state": type("S", (), {"container": container})(),
                    },
                )(),
            },
        )()

        # Start with a session at SUBJECT state (after GREETING was done).
        session_dict = intake_session_to_dict(IntakeSession(state=IntakeState.SUBJECT))

        async def _json():
            return {"session": session_dict, "student_input": "Physics"}

        request.json = _json

        result = await intake_step_route(request)
        assert result["state"] == "PRIOR_KNOWLEDGE"
        assert result["prompt"] is not None
        assert result["pivot"] is None  # no intent detected


# ---------------------------------------------------------------------------
# PLACER tests (Phase D — placement calibration, ADR-002 §9 stage 5)
# ---------------------------------------------------------------------------


class TestPlacerSampling:
    """Tests for _sample_concepts_for_placement (pure function, no async)."""

    def test_sample_concepts_distributed_evenly(self):
        """20 concepts, n=7 → returns 7 spaced indices, not first 7."""
        from aristotle.actors.intake import _sample_concepts_for_placement

        concept_ids = [f"c{i}" for i in range(20)]
        sampled = _sample_concepts_for_placement(concept_ids, n=7)
        assert len(sampled) == 7
        # Should NOT be the first 7
        assert sampled != concept_ids[:7]
        # Should include concepts from the end of the list
        assert any(int(cid[1:]) >= 15 for cid in sampled)
        # Should include concepts from the beginning
        assert any(int(cid[1:]) <= 5 for cid in sampled)

    def test_sample_concepts_small_list(self):
        """4 concepts, n=7 → returns all 4."""
        from aristotle.actors.intake import _sample_concepts_for_placement

        concept_ids = [f"c{i}" for i in range(4)]
        sampled = _sample_concepts_for_placement(concept_ids, n=7)
        assert len(sampled) == 4
        assert set(sampled) == set(concept_ids)


class TestPlacerStep:
    """Tests for run_placer_step (uses ExaminerActor via fake model)."""

    @pytest.mark.asyncio
    async def test_placer_step_phase1_returns_question(self):
        """Phase 1: run_placer_step with no student_input generates a probe question."""
        from aristotle.actors.intake import PlacerSession, run_placer_step

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": "Explain inertia in your own words.",  # probe question
            }
        )
        # FakeConn returns concept rows for examiner._fetch_concept
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1", "c2"],
        )
        result = await run_placer_step(session, "", ctx)
        assert result["state"] == "PROBING"
        assert "question" in result
        assert len(result["question"]) > 0
        assert session.question_generated is True

    @pytest.mark.asyncio
    async def test_placer_step_phase2_writes_placement_event(self):
        """Phase 2: evaluates the answer + writes a placement_event row."""
        from aristotle.actors.intake import PlacerSession, run_placer_step

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1", "c2"],
            current_idx=0,
            question_generated=True,
            current_question="What is inertia?",
        )
        result = await run_placer_step(session, "objects resist changes in motion", ctx)
        assert result["concepts_placed"] == 1
        # Verify the INSERT into aristotle_placement_event was issued.
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT INTO aristotle_placement_event" in sql
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_placer_step_phase2_mastered_upserts_mastery(self):
        """Phase 2: when mastery_achieved, upserts aristotle_mastery (repetitions=3)."""
        from aristotle.actors.intake import PlacerSession, run_placer_step

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1", "c2"],
            current_idx=0,
            question_generated=True,
            current_question="What is inertia?",
        )
        result = await run_placer_step(session, "objects resist changes in motion", ctx)
        # Verify the INSERT OR REPLACE into aristotle_mastery was issued.
        mastery_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_mastery" in sql
        ]
        assert len(mastery_calls) == 1

    @pytest.mark.asyncio
    async def test_placer_advances_to_complete_after_last_concept(self):
        """After the last concept is assessed, state becomes COMPLETE."""
        from aristotle.actors.intake import PlacerSession, run_placer_step

        eval_json = json.dumps(
            {
                "score": 0.3,
                "mastery_achieved": False,
                "feedback": "Try again",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
            }
        )

        # Routing conn: returns concept row for fetch_concept, plan row for finalize
        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2"]',)])
                return _FakeCursor(
                    [("c1", "Inertia", None, "content", None, None, None, 3)]
                )

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1"],  # only 1 concept — will complete after this
            current_idx=0,
            question_generated=True,
            current_question="What is inertia?",
        )
        result = await run_placer_step(session, "I don't know", ctx)
        assert result["state"] == "COMPLETE"
        assert result["concepts_placed"] == 1
        assert session.state == "COMPLETE"

    @pytest.mark.asyncio
    async def test_finalize_sets_current_concept_idx(self):
        """_finalize_placement sets current_concept_idx to the first non-mastered concept."""
        from aristotle.actors.intake import PlacerSession, _finalize_placement

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2", "c3"]',)])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            results=[
                {"concept_id": "c1", "score": 0.9, "mastery_achieved": True},
                {"concept_id": "c3", "score": 0.3, "mastery_achieved": False},
            ],
        )
        await _finalize_placement(session, ctx)
        # Should have issued an UPDATE setting current_concept_idx = 1 (c2 is the first non-mastered)
        update_calls = [
            (sql, params)
            for sql, params in conn._executed
            if "UPDATE aristotle_learning_plan SET current_concept_idx" in sql
        ]
        assert len(update_calls) == 1
        _sql, params = update_calls[0]
        assert params[0] == 1  # starting_concept_idx


class TestPlacerRoutes:
    """Tests for the PLACER API routes."""

    @pytest.mark.asyncio
    async def test_placer_start_route_returns_first_question(self):
        """POST /placer/start reads the plan + returns the first probe question."""
        from aristotle.api import placer_start_route

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": "Explain inertia in your own words.",
            }
        )

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2"]',)])
                return _FakeCursor(
                    [("c1", "Inertia", None, "content", None, None, None, 3)]
                )

            async def commit(self):
                pass

        conn = _RoutingConn()
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            },
        )()
        request = type(
            "R",
            (),
            {
                "app": type(
                    "A",
                    (),
                    {
                        "state": type("S", (), {"container": container})(),
                    },
                )(),
            },
        )()

        async def _json():
            return {"plan_id": "plan-1"}

        request.json = _json

        result = await placer_start_route(request)
        assert result["state"] == "PROBING"
        assert result["question"] is not None
        assert len(result["question"]) > 0

    @pytest.mark.asyncio
    async def test_placer_step_route_advances_session(self):
        """POST /placer/step with an answer advances the session."""
        from aristotle.api import placer_step_route
        from aristotle.actors.intake import PlacerSession, placer_session_to_dict

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
            }
        )

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2"]',)])
                return _FakeCursor(
                    [("c1", "Inertia", None, "content", None, None, None, 3)]
                )

            async def commit(self):
                pass

        conn = _RoutingConn()
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            },
        )()
        request = type(
            "R",
            (),
            {
                "app": type(
                    "A",
                    (),
                    {
                        "state": type("S", (), {"container": container})(),
                    },
                )(),
            },
        )()

        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1", "c2"],
            current_idx=0,
            question_generated=True,
            current_question="What is inertia?",
        )

        async def _json():
            return {
                "session": placer_session_to_dict(session),
                "student_input": "objects resist changes in motion",
            }

        request.json = _json

        result = await placer_step_route(request)
        assert result["concepts_placed"] == 1
        # The session should have advanced (either to next question or COMPLETE)
        assert result["session"]["current_idx"] >= 1
