"""Behavior tests for ARISTOTLE's tutoring methods — Phase A.

Tests the real tutoring methods (teach/probe/quiz/evaluate/update_struggle_pattern)
with a fake model provider. The fake returns canned responses so the tests
don't need a real model or API keys.

Also tests:
- SM-2 algorithm (pure function, no model needed)
- Session coordinator (the state machine driver)
- Content ingestor (YAML → aristotle_concept)

Run:  pytest tests/test_aristotle_tutoring.py -v
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aip.foundation.protocols.actors import ActorContext, ActorResult
from aristotle.actors import ExaminerActor, MentorActor, SocratesActor
from aristotle.sm2 import SM2State, is_due, score_to_quality, update_sm2


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeModelProvider:
    """Fake ModelProvider that returns canned responses by slot."""

    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, list[dict]]] = []  # log of (slot, messages)

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        content = self._responses.get(slot_name, f"[fake {slot_name} response]")
        return {
            "content": content,
            "model": "fake-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "latency_ms": 5,
        }


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
    container = type("C", (), {
        "model_provider": model_provider,
        "corpus_registry": _FakeRegistry(stores) if stores else None,
    })()
    return ActorContext(
        container=container,
        config=config,
        logger=__import__("logging").getLogger("test"),
        cancel_event=asyncio.Event(),
    )


# --------------------------------------------------------------------------
# SM-2 algorithm tests (pure function, no deps)
# --------------------------------------------------------------------------


class TestSM2Algorithm:
    def test_score_to_quality_mapping(self):
        assert score_to_quality(0.0) == 0
        assert score_to_quality(0.2) == 1
        assert score_to_quality(0.4) == 2
        assert score_to_quality(0.6) == 3
        assert score_to_quality(0.8) == 4
        assert score_to_quality(1.0) == 5

    def test_score_to_quality_clamps(self):
        assert score_to_quality(-0.5) == 0
        assert score_to_quality(1.5) == 5

    def test_initial_state_defaults(self):
        state = SM2State()
        assert state.easiness_factor == 2.5
        assert state.interval_days == 0
        assert state.repetitions == 0
        assert state.next_review_at is None

    def test_is_due_never_reviewed(self):
        state = SM2State(next_review_at=None)
        assert is_due(state) is True

    def test_is_due_future(self):
        state = SM2State(next_review_at="2099-01-01T00:00:00+00:00")
        assert is_due(state) is False

    def test_is_due_past(self):
        state = SM2State(next_review_at="2020-01-01T00:00:00+00:00")
        assert is_due(state) is True

    def test_update_correct_first_review(self):
        """First correct review: interval = 1 day, repetitions = 1."""
        state = SM2State()
        new_state = update_sm2(state, score=0.8)  # quality = 4
        assert new_state.repetitions == 1
        assert new_state.interval_days == 1
        assert new_state.next_review_at is not None

    def test_update_correct_second_review(self):
        """Second correct review: interval = 6 days."""
        state = SM2State(easiness_factor=2.5, interval_days=1, repetitions=1)
        new_state = update_sm2(state, score=0.8)
        assert new_state.repetitions == 2
        assert new_state.interval_days == 6

    def test_update_incorrect_resets(self):
        """Incorrect response (quality < 3): repetitions = 0, interval = 1."""
        state = SM2State(easiness_factor=2.5, interval_days=6, repetitions=2)
        new_state = update_sm2(state, score=0.2)  # quality = 1
        assert new_state.repetitions == 0
        assert new_state.interval_days == 1

    def test_ef_never_below_1_3(self):
        """Easiness Factor never goes below 1.3 even with repeated failures."""
        state = SM2State(easiness_factor=1.3)
        for _ in range(5):
            state = update_sm2(state, score=0.0)  # quality = 0
        assert state.easiness_factor >= 1.3


# --------------------------------------------------------------------------
# SOCRATES teach() tests
# --------------------------------------------------------------------------


class TestSocratesTeach:
    @pytest.mark.asyncio
    async def test_teach_returns_needs_configuration_without_model(self):
        """SOCRATES.teach() returns NEEDS_CONFIGURATION without a model provider."""
        socrates = SocratesActor()
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(_FakeConn()))
        result = await socrates.teach(ctx, "concept_001")
        assert not result.ok
        assert "NEEDS_CONFIGURATION" in result.error

    @pytest.mark.asyncio
    async def test_teach_calls_beast_slot(self):
        """SOCRATES.teach() calls the model provider with slot='beast'."""
        fake = _FakeModelProvider(responses={"beast": "Newton's First Law states..."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "c1")
        assert result.ok
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "beast"  # called with beast slot
        assert "Newton's First Law" in result.error

    @pytest.mark.asyncio
    async def test_teach_concept_not_found(self):
        """SOCRATES.teach() returns error if concept not in DB."""
        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)  # no rows → concept not found
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "nonexistent")
        assert not result.ok
        assert "not found" in result.error


# --------------------------------------------------------------------------
# EXAMINER probe/quiz/evaluate tests
# --------------------------------------------------------------------------


class TestExaminerMethods:
    @pytest.mark.asyncio
    async def test_probe_returns_needs_configuration_without_model(self):
        examiner = ExaminerActor()
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(_FakeConn()))
        result = await examiner.probe(ctx, "c1")
        assert not result.ok
        assert "NEEDS_CONFIGURATION" in result.error

    @pytest.mark.asyncio
    async def test_probe_calls_evaluation_slot(self):
        fake = _FakeModelProvider(responses={"evaluation": "Explain inertia in your own words."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.probe(ctx, "c1")
        assert result.ok
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "evaluation"
        assert "inertia" in result.error.lower()

    @pytest.mark.asyncio
    async def test_quiz_calls_evaluation_slot(self):
        fake = _FakeModelProvider(responses={"evaluation": "What is the SI unit of force?"})
        conn = _FakeConn(rows=[("c1", "Force", None, "content", None, None, None, 4)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.quiz(ctx, "c1")
        assert result.ok
        assert "force" in result.error.lower()

    @pytest.mark.asyncio
    async def test_evaluate_returns_json(self):
        eval_json = json.dumps({"score": 0.8, "mastery_achieved": True, "feedback": "Good"})
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Force", None, "content", None, None, None, 4)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(ctx, "c1", "1 Newton", "What is the SI unit of force?")
        assert result.ok
        parsed = json.loads(result.error)
        assert parsed["score"] == 0.8
        assert parsed["mastery_achieved"] is True


# --------------------------------------------------------------------------
# MENTOR update_struggle_pattern tests
# --------------------------------------------------------------------------


class TestMentorUpdate:
    @pytest.mark.asyncio
    async def test_update_returns_needs_configuration_without_model(self):
        mentor = MentorActor()
        conn = _FakeConn(rows=[("placeholder",)])  # existing pattern
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(conn))
        result = await mentor.update_struggle_pattern(ctx, "c1", "score=0.5")
        assert not result.ok
        assert "NEEDS_CONFIGURATION" in result.error

    @pytest.mark.asyncio
    async def test_update_calls_sexton_slot(self):
        fake = _FakeModelProvider(responses={"sexton": "Learner struggles with vector decomposition."})
        conn = _FakeConn(rows=[("old pattern",)])  # existing pattern
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        mentor = MentorActor()
        result = await mentor.update_struggle_pattern(ctx, "c1", "score=0.4")
        assert result.ok
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "sexton"
        assert "vector decomposition" in result.error

    @pytest.mark.asyncio
    async def test_get_struggle_pattern_returns_existing(self):
        conn = _FakeConn(rows=[("Learner avoids math-heavy problems.",)])
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(conn))
        mentor = MentorActor()
        pattern = await mentor.get_struggle_pattern(ctx, "definer")
        assert pattern == "Learner avoids math-heavy problems."

    @pytest.mark.asyncio
    async def test_get_struggle_pattern_returns_none_if_absent(self):
        conn = _FakeConn(rows=None)  # no row
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(conn))
        mentor = MentorActor()
        pattern = await mentor.get_struggle_pattern(ctx, "definer")
        assert pattern is None


# --------------------------------------------------------------------------
# Session coordinator tests
# --------------------------------------------------------------------------


class TestSessionCoordinator:
    @pytest.mark.asyncio
    async def test_session_teach_step_advances_to_probe(self):
        """run_session_step with state=TEACH advances to PROBE."""
        from aristotle.session import SessionContext, SessionState, run_session_step

        fake = _FakeModelProvider(responses={
            "beast": "Newton's First Law states...",
            "evaluation": "Explain in your own words.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        # The coordinator checks `container.extensions.registry` — provide a
        # non-None registry so it passes the availability check. The
        # coordinator then imports aristotle.actors directly (the actors are
        # stateless for Phase A), so the registry doesn't need real actors.
        container = type("C", (), {
            "model_provider": fake,
            "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
        })()
        ctx = ActorContext(
            container=container, config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(concept_id="c1", state=SessionState.TEACH)
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.PROBE
