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
        # Phase B.5: teach() now returns data={"explanation": ...}, not error=
        assert result.data is not None
        assert "Newton's First Law" in result.data["explanation"]

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

    @pytest.mark.asyncio
    async def test_teach_mastery_0_includes_worked_example(self):
        """teach(mastery_level=0) sends a prompt requesting a full worked example.

        Phase B.5 (ADR-002 §4): level 0 (new concept) → full worked example.
        We verify the system prompt sent to the model includes the
        'full worked example' fading instruction.
        """
        fake = _FakeModelProvider(responses={"beast": "Here is a full worked example..."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "c1", mastery_level=0)
        assert result.ok
        assert result.data["fading_mode"] == "full_worked_example"
        # The system prompt should mention "full worked example"
        system_msg = fake.calls[0][1][0]["content"]
        assert "full worked example" in system_msg.lower()

    @pytest.mark.asyncio
    async def test_teach_mastery_2_includes_partial_example(self):
        """teach(mastery_level=2) sends a prompt requesting a partial faded example.

        Phase B.5 (ADR-002 §4): level 1-2 (early mastery) → partial faded
        example. The learner completes the final step.
        """
        fake = _FakeModelProvider(responses={"beast": "Here is a partial example..."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "c1", mastery_level=2)
        assert result.ok
        assert result.data["fading_mode"] == "partial_faded_example"
        system_msg = fake.calls[0][1][0]["content"]
        assert "partial faded example" in system_msg.lower()

    @pytest.mark.asyncio
    async def test_teach_mastery_3_is_conceptual_only(self):
        """teach(mastery_level=3) sends a prompt for conceptual explanation only.

        Phase B.5 (ADR-002 §4): level 3+ (near-mastered) → no worked
        example, focus on depth and nuance.
        """
        fake = _FakeModelProvider(responses={"beast": "Here is a conceptual deep-dive..."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "c1", mastery_level=3)
        assert result.ok
        assert result.data["fading_mode"] == "conceptual_only"
        system_msg = fake.calls[0][1][0]["content"]
        assert "conceptual explanation only" in system_msg.lower()
        assert "worked example" not in system_msg.lower().replace("do not include a worked example", "")

    @pytest.mark.asyncio
    async def test_teach_defaults_to_level_0_for_new_concept(self):
        """_get_mastery_level returns 0 when no mastery row exists (new concept).

        Phase B.5: the session coordinator queries aristotle_mastery before
        calling teach(). A new concept has no mastery row → level 0 → full
        worked example. This test pins the default so a future refactor
        can't silently change it.
        """
        from aristotle.session import SessionContext, _get_mastery_level

        # No mastery row → _FakeConn returns None for fetchone
        conn = _FakeConn(rows=None)
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = SessionContext(concept_id="c1")
        level = await _get_mastery_level(ctx, session)
        assert level == 0, "new concept (no mastery row) should default to level 0"


# --------------------------------------------------------------------------
# SOCRATES predict tests (Phase B.5)
# --------------------------------------------------------------------------


class TestSocratesPredict:
    """Phase B.5: SocratesActor.predict() generates the pre-teach prompt.

    The generation effect (ADR-002 Rev 2 §3): asking the learner to guess
    before teaching improves retention regardless of guess correctness.
    predict() does NOT call a model — it's a fixed template with the
    concept name interpolated. Uses the new ActorResult.data field (not
    error-as-payload).
    """

    @pytest.mark.asyncio
    async def test_predict_returns_prompt(self):
        """SocratesActor.predict() returns ok=True with data.prompt as a string.

        Uses the new data field (Brain commit ce44e53), not error-as-payload.
        The prompt should mention the concept topic so it's specific, not
        generic.
        """
        # predict() does NOT need a model provider (unlike teach()) — it's a
        # fixed template. Provide one anyway to confirm it's not called.
        fake = _FakeModelProvider()
        conn = _FakeConn(rows=[("c1", "Newton's First Law", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.predict(ctx, "c1")
        assert result.ok
        # The prompt is in result.data["prompt"], not result.error
        assert result.data is not None
        assert isinstance(result.data, dict)
        assert "prompt" in result.data
        assert isinstance(result.data["prompt"], str)
        assert len(result.data["prompt"]) > 0
        # The prompt should reference the concept topic (specific, not generic)
        assert "Newton's First Law" in result.data["prompt"]
        # predict() must NOT call the model — it's a fixed template
        assert len(fake.calls) == 0

    @pytest.mark.asyncio
    async def test_predict_concept_not_found(self):
        """SocratesActor.predict() returns ok=False with 'not found' in error."""
        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)  # no rows → concept not found
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.predict(ctx, "nonexistent")
        assert not result.ok
        assert "not found" in result.error
        # data should be None on failure (error-as-payload is for errors only)
        assert result.data is None

    @pytest.mark.asyncio
    async def test_predict_prompt_is_warm_and_low_pressure(self):
        """The predict prompt uses warm framing ('a wrong guess is fine').

        ADR-002 §3: the prediction step must reduce affective filter, not
        increase it. The prompt explicitly tells the learner a wrong guess
        is fine. This test pins the framing so a future refactor can't
        accidentally make it intimidating.
        """
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.predict(ctx, "c1")
        assert result.ok
        prompt = result.data["prompt"].lower()
        # Warm framing — at least one of these phrases should appear
        assert any(phrase in prompt for phrase in [
            "wrong guess is fine",
            "just say what comes to mind",
            "what do you think",
        ]), f"prompt should use warm framing, got: {result.data['prompt']}"


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
        """evaluate() returns ok=True with data dict (Phase B.5: data, not error-as-payload)."""
        eval_json = json.dumps({"score": 0.8, "mastery_achieved": True, "feedback": "Good"})
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Force", None, "content", None, None, None, 4)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(ctx, "c1", "1 Newton", "What is the SI unit of force?")
        assert result.ok
        # Phase B.5: read from result.data (not result.error)
        assert result.data is not None
        assert isinstance(result.data, dict)
        assert result.data["score"] == 0.8
        assert result.data["mastery_achieved"] is True
        # When mastery_achieved is True, diagnosis is None
        assert result.data["diagnosis"] is None

    @pytest.mark.asyncio
    async def test_evaluate_returns_diagnosis_on_wrong_answer(self):
        """evaluate() returns a diagnosis dict with all three keys when score < threshold.

        Phase B.5 (ADR-002 §3): when the answer is wrong, EXAMINER produces
        a three-part diagnosis (misconception / why_wrong / corrective).
        The fake model returns the full schema; we verify the data field
        carries it through + all three keys are present.
        """
        eval_json = json.dumps({
            "score": 0.3,
            "mastery_achieved": False,
            "feedback": "Not quite — see diagnosis.",
            "diagnosis": {
                "misconception": "You seem to think a force is needed to sustain motion",
                "why_wrong": "Objects keep moving on their own — force changes motion, not sustains it",
                "corrective": "No force is needed to keep something moving; force changes motion",
            },
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(ctx, "c1", "a force keeps it going", "What is inertia?")
        assert result.ok
        assert result.data is not None
        assert result.data["score"] == 0.3
        assert result.data["mastery_achieved"] is False
        # Diagnosis must be present with all three keys
        assert result.data["diagnosis"] is not None
        assert isinstance(result.data["diagnosis"], dict)
        assert "misconception" in result.data["diagnosis"]
        assert "why_wrong" in result.data["diagnosis"]
        assert "corrective" in result.data["diagnosis"]
        assert len(result.data["diagnosis"]["misconception"]) > 0
        assert len(result.data["diagnosis"]["why_wrong"]) > 0
        assert len(result.data["diagnosis"]["corrective"]) > 0

    @pytest.mark.asyncio
    async def test_evaluate_no_diagnosis_on_correct_answer(self):
        """evaluate() returns diagnosis=None when mastery_achieved is True.

        Phase B.5: diagnosis is only populated for wrong answers. When the
        learner masters the concept, diagnosis is None + feedback names
        why the answer was right.
        """
        eval_json = json.dumps({
            "score": 0.9,
            "mastery_achieved": True,
            "feedback": "Exactly — you identified that inertia resists change in motion.",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(ctx, "c1", "objects resist changes in motion", "What is inertia?")
        assert result.ok
        assert result.data is not None
        assert result.data["mastery_achieved"] is True
        # Diagnosis must be None when correct
        assert result.data["diagnosis"] is None
        # Feedback should be present + non-empty
        assert len(result.data["feedback"]) > 0


# --------------------------------------------------------------------------
# EXAMINER generate_hint tests (Phase B.5 HINT ladder)
# --------------------------------------------------------------------------


class TestExaminerHints:
    """Phase B.5: ExaminerActor.generate_hint() returns graded hints.

    The HINT ladder has 2 rungs: HINT_1 (gentle nudge, hint_count=0) and
    HINT_2 (stronger clue, hint_count=1). Uses the new ActorResult.data
    field (not error-as-payload).
    """

    @pytest.mark.asyncio
    async def test_hint_1_returns_gentle_nudge(self):
        """generate_hint(hint_count=0) returns ok=True with data.hint as a string.

        HINT_1 is a gentle nudge — does not give the answer. The fake model
        returns a canned hint; we verify the data field is used + the hint
        is non-empty.
        """
        fake = _FakeModelProvider(responses={
            "evaluation": "Think about what happens to a passenger when a bus brakes suddenly.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.generate_hint(ctx, "c1", hint_count=0)
        assert result.ok
        # The hint is in result.data["hint"], not result.error
        assert result.data is not None
        assert isinstance(result.data, dict)
        assert "hint" in result.data
        assert isinstance(result.data["hint"], str)
        assert len(result.data["hint"]) > 0
        # The hint should call the evaluation slot
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "evaluation"

    @pytest.mark.asyncio
    async def test_hint_2_returns_stronger_clue(self):
        """generate_hint(hint_count=1) returns ok=True with a stronger-clue hint.

        HINT_2 is a near-direct clue — may name the key term/formula but
        still preserves some effort. We verify the data field is used +
        the system prompt sent to the model mentions 'STRONGER HINT'.
        """
        fake = _FakeModelProvider(responses={
            "evaluation": "The answer relates to inertia — objects tend to keep doing what they're doing unless a force acts on them. What's the specific term?",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.generate_hint(ctx, "c1", hint_count=1)
        assert result.ok
        assert result.data is not None
        assert isinstance(result.data, dict)
        assert "hint" in result.data
        assert len(result.data["hint"]) > 0
        # The system prompt for HINT_2 should mention "STRONGER HINT"
        system_msg = fake.calls[0][1][0]["content"]  # first message = system
        assert "STRONGER HINT" in system_msg

    @pytest.mark.asyncio
    async def test_hint_concept_not_found(self):
        """generate_hint() returns ok=False with 'not found' in error when concept missing."""
        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)  # no rows → concept not found
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.generate_hint(ctx, "nonexistent", hint_count=0)
        assert not result.ok
        assert "not found" in result.error
        # data should be None on failure
        assert result.data is None

    @pytest.mark.asyncio
    async def test_hint_returns_needs_configuration_without_model(self):
        """generate_hint() returns NEEDS_CONFIGURATION without a model provider."""
        examiner = ExaminerActor()
        ctx = _make_ctx(model_provider=None, stores=_FakeStores(_FakeConn()))
        result = await examiner.generate_hint(ctx, "c1", hint_count=0)
        assert not result.ok
        assert "NEEDS_CONFIGURATION" in result.error


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
# MENTOR log_misconception tests (Phase B.5 item 7)
# --------------------------------------------------------------------------


class TestMentorLogMisconception:
    """Phase B.5: MentorActor.log_misconception() writes to aristotle_misconception_log.

    Best-effort: returns ok=True even on DB failure (a misconception-log
    failure must NEVER break the session). Uses ActorResult.data for
    confirmation, not error-as-payload.
    """

    @pytest.mark.asyncio
    async def test_mentor_log_misconception_writes_row(self):
        """log_misconception() returns ok=True + the row is written to the fake DB.

        The fake _FakeConn records every execute() call. We verify the
        INSERT was issued with the correct table + the diagnosis fields
        were extracted into misconception_text + corrective_text.
        """
        conn = _FakeConn(rows=None)
        ctx = _make_ctx(stores=_FakeStores(conn))
        mentor = MentorActor()
        diagnosis = {
            "misconception": "You seem to think a force is needed to sustain motion",
            "why_wrong": "Objects keep moving on their own",
            "corrective": "Force changes motion, not sustains it",
        }
        result = await mentor.log_misconception(
            ctx, concept_id="c1", session_id="sess-1", diagnosis=diagnosis,
        )
        assert result.ok
        # data confirms the write
        assert result.data is not None
        assert result.data["logged"] is True
        assert result.data["concept_id"] == "c1"
        # The INSERT was executed on the fake conn
        insert_calls = [
            (sql, params) for sql, params in conn._executed
            if "INSERT INTO aristotle_misconception_log" in sql
        ]
        assert len(insert_calls) == 1, "expected one INSERT into aristotle_misconception_log"
        sql, params = insert_calls[0]
        # params: (session_id, concept_id, misconception_text, corrective_text)
        assert params[0] == "sess-1"
        assert params[1] == "c1"
        assert params[2] == "You seem to think a force is needed to sustain motion"
        assert params[3] == "Force changes motion, not sustains it"

    @pytest.mark.asyncio
    async def test_mentor_log_misconception_survives_db_error(self):
        """Even if the DB write raises, ok=True is returned (best-effort).

        A misconception-log failure must NEVER break the session. The
        learner's progress through the tutoring loop is more important
        than the analytics row. The error is logged at WARNING for
        observability + ok=True is returned with data.logged=False.
        """
        class _ExplodingConn:
            async def execute(self, sql, params=()):
                raise RuntimeError("simulated DB failure")
            async def commit(self):
                pass

        class _ExplodingStores:
            connection_manager = type("CM", (), {"write_conn": _ExplodingConn()})()

        class _ExplodingRegistry:
            async def get_stores(self, corpus_id, **kwargs):
                return _ExplodingStores()

        container = type("C", (), {
            "corpus_registry": _ExplodingRegistry(),
        })()
        ctx = ActorContext(
            container=container, config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        mentor = MentorActor()
        diagnosis = {
            "misconception": "some misconception",
            "why_wrong": "some why",
            "corrective": "some corrective",
        }
        result = await mentor.log_misconception(
            ctx, concept_id="c1", session_id="sess-1", diagnosis=diagnosis,
        )
        # ok MUST be True — best-effort never breaks the session
        assert result.ok
        # data.logs is False + the error is captured for observability
        assert result.data is not None
        assert result.data["logged"] is False
        assert "error" in result.data

    @pytest.mark.asyncio
    async def test_mentor_log_misconception_handles_partial_diagnosis(self):
        """log_misconception() handles a diagnosis missing keys (defensive).

        If the model returned a partial diagnosis (e.g. missing
        'corrective'), the method should not KeyError — it writes empty
        strings for the missing fields. This is the defensive normalization
        the actor does before the INSERT.
        """
        conn = _FakeConn(rows=None)
        ctx = _make_ctx(stores=_FakeStores(conn))
        mentor = MentorActor()
        # Partial diagnosis — only 'misconception' present
        diagnosis = {"misconception": "partial thought", "why_wrong": "partial why"}
        result = await mentor.log_misconception(
            ctx, concept_id="c1", session_id="sess-1", diagnosis=diagnosis,
        )
        assert result.ok
        assert result.data["logged"] is True
        # The INSERT should have empty string for the missing corrective
        insert_calls = [
            (sql, params) for sql, params in conn._executed
            if "INSERT INTO aristotle_misconception_log" in sql
        ]
        assert len(insert_calls) == 1
        _sql, params = insert_calls[0]
        assert params[2] == "partial thought"  # misconception_text
        assert params[3] == ""  # corrective_text (missing → empty string)


# --------------------------------------------------------------------------
# Session coordinator tests
# --------------------------------------------------------------------------


class TestSessionCoordinator:
    @pytest.mark.asyncio
    async def test_session_starts_in_predict_state(self):
        """A fresh SessionContext's initial state is PREDICT, not TEACH.

        Phase B.5 (ADR-002 Rev 2 §3): sessions start at PREDICT (the
        generation effect — learner guesses before teaching). Was TEACH
        in Phase A. This test pins the default so a future refactor
        can't silently revert it.
        """
        from aristotle.session import SessionContext, SessionState

        session = SessionContext(concept_id="c1")
        assert session.state == SessionState.PREDICT
        assert session.predict_generated is False
        assert session.last_prediction == ""

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

    @pytest.mark.asyncio
    async def test_session_stores_diagnosis_on_wrong_answer(self):
        """After a wrong EVALUATE step, session.last_diagnosis is populated.

        Phase B.5 (error diagnosis): when EXAMINER returns a diagnosis dict
        for a wrong answer, the session coordinator stores it on
        session.last_diagnosis so MENTOR can read it in the next task
        (misconception log wiring). This test pins the storage contract.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        eval_json = json.dumps({
            "score": 0.3,
            "mastery_achieved": False,
            "feedback": "Not quite.",
            "diagnosis": {
                "misconception": "You seem to think a force is needed to sustain motion",
                "why_wrong": "Objects keep moving on their own",
                "corrective": "Force changes motion, not sustains it",
            },
        })
        fake = _FakeModelProvider(responses={
            "evaluation": eval_json,
            "sexton": "Learner struggles with inertia.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
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
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            hint_count=0,
            last_quiz_question="What is inertia?",
            last_student_answer="a force keeps it going",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        # The diagnosis must be stored on the session
        assert session.last_diagnosis is not None
        assert isinstance(session.last_diagnosis, dict)
        assert "misconception" in session.last_diagnosis
        assert "why_wrong" in session.last_diagnosis
        assert "corrective" in session.last_diagnosis
        assert session.last_diagnosis["misconception"] == "You seem to think a force is needed to sustain motion"

    @pytest.mark.asyncio
    async def test_session_logs_misconception_on_wrong_evaluate(self):
        """After a wrong EVALUATE step with diagnosis, the misconception log write is triggered.

        Phase B.5 item 7: the session coordinator fire-and-forgets a
        mentor.log_misconception() call after a wrong EVALUATE. This test
        verifies the INSERT into aristotle_misconception_log is issued
        during the EVALUATE step (the fake conn records every execute()).
        The call is best-effort — a failure wouldn't break the session
        (tested separately in TestMentorLogMisconception).
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        eval_json = json.dumps({
            "score": 0.3,
            "mastery_achieved": False,
            "feedback": "Not quite.",
            "diagnosis": {
                "misconception": "You seem to think a force is needed to sustain motion",
                "why_wrong": "Objects keep moving on their own",
                "corrective": "Force changes motion, not sustains it",
            },
        })
        fake = _FakeModelProvider(responses={
            "evaluation": eval_json,
            "sexton": "Learner struggles with inertia.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
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
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            hint_count=0,
            last_quiz_question="What is inertia?",
            last_student_answer="a force keeps it going",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        # The misconception-log INSERT should have been issued on the fake conn
        insert_calls = [
            sql for sql, _params in conn._executed
            if "INSERT INTO aristotle_misconception_log" in sql
        ]
        assert len(insert_calls) == 1, (
            "expected one INSERT into aristotle_misconception_log after a "
            "wrong EVALUATE with diagnosis"
        )

    @pytest.mark.asyncio
    async def test_session_routes_to_hint1_on_wrong_answer(self):
        """EVALUATE with a failing score + hint_count=0 routes to HINT_1.

        Phase B.5 HINT ladder (ADR-002 Rev 2 §3): on a failed quiz, the
        learner gets a 2-rung hint ladder before REMEDIATE. The first
        wrong answer routes to HINT_1 (not REMEDIATE).
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        # Failing evaluation JSON (score 0.3 < 0.7 mastery_threshold)
        eval_json = json.dumps({"score": 0.3, "mastery_achieved": False, "feedback": "Try again"})
        fake = _FakeModelProvider(responses={
            "evaluation": eval_json,
            "sexton": "Learner struggles with inertia.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
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
        # Start at EVALUATE with hint_count=0 (no hints given yet).
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            hint_count=0,
            last_quiz_question="What is inertia?",
            last_student_answer="I don't know",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.HINT_1, (
            f"EVALUATE with hint_count=0 + failing score should route to "
            f"HINT_1, got {session.state}"
        )
        assert session.hint_count == 0  # hint_count not yet incremented
        assert session.hint_generated is False  # HINT_1 phase 1 not started

    @pytest.mark.asyncio
    async def test_session_routes_to_remediate_after_two_hints(self):
        """After HINT_1 + HINT_2 both fail, the session routes to REMEDIATE.

        Phase B.5 HINT ladder: both hints exhausted → REMEDIATE (not
        NEXT_CONCEPT). This test drives the session through EVALUATE →
        HINT_1 → HINT_2 → REMEDIATE by simulating the two-phase HINT
        steps with a fake model that always returns a failing evaluation.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        # Failing evaluation JSON for every re-evaluation
        eval_json = json.dumps({"score": 0.3, "mastery_achieved": False, "feedback": "Try again"})
        fake = _FakeModelProvider(responses={
            "evaluation": eval_json,
            "sexton": "Learner struggles with inertia.",
        })
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
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
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            hint_count=0,
            last_quiz_question="What is inertia?",
            last_student_answer="I don't know",
        )

        # Step 1: EVALUATE → HINT_1 (hint_count=0, failing score)
        await run_session_step(ctx, session)
        assert session.state == SessionState.HINT_1

        # Step 2: HINT_1 phase 1 — generate hint (no student_input)
        await run_session_step(ctx, session)
        assert session.hint_generated is True

        # Step 3: HINT_1 phase 2 — re-evaluate with student_input (still wrong)
        await run_session_step(ctx, session, student_input="still wrong")
        assert session.state == SessionState.HINT_2, (
            f"After HINT_1 fails, should route to HINT_2, got {session.state}"
        )
        assert session.hint_count == 1

        # Step 4: HINT_2 phase 1 — generate hint (no student_input)
        await run_session_step(ctx, session)
        assert session.hint_generated is True

        # Step 5: HINT_2 phase 2 — re-evaluate with student_input (still wrong)
        await run_session_step(ctx, session, student_input="still wrong again")
        assert session.state == SessionState.REMEDIATE, (
            f"After HINT_2 fails, should route to REMEDIATE, got {session.state}"
        )
        assert session.hint_count == 2
        assert session.retry_count == 1  # REMEDIATE incremented retry_count
