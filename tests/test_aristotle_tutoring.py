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

from aip.foundation.protocols.actors import ActorContext
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

    # ------------------------------------------------------------------
    # Phase B.5 item 8: mastery_probability (BKT-inspired extension)
    # ------------------------------------------------------------------

    def test_mastery_probability_pure_correct(self):
        """mastery_probability with pure unassisted correct answers = 1.0.

        No hints, no transfers, no slips → probability is 1.0 (full credit
        for independent recall).
        """
        from aristotle.sm2 import mastery_probability

        p = mastery_probability(
            repetitions=3, hint_assisted_correct=0, transfer_correct=0, slip_count=0
        )
        assert p == 1.0

    def test_mastery_probability_penalizes_slips(self):
        """mastery_probability with slips is below 1.0.

        A slip (correct answer that scored below 0.85) signals fragile
        mastery. The slip_rate penalty reduces the probability.
        """
        from aristotle.sm2 import mastery_probability

        p_no_slip = mastery_probability(repetitions=4, slip_count=0)
        p_with_slip = mastery_probability(repetitions=4, slip_count=2)
        assert p_with_slip < p_no_slip
        assert p_with_slip < 1.0

    def test_mastery_probability_boosts_transfer(self):
        """mastery_probability with transfer_correct boosts the score.

        Correct transfer questions (applying concept to new situation)
        give a 1.5x bonus — stronger evidence of mastery than recognition.
        With 3 repetitions + 1 transfer correct, the unclamped probability
        is (3 + 1.5) / 3 = 1.5, which clamps to 1.0. Without the transfer
        bonus, it's 3/3 = 1.0. To see the boost below the clamp, test with
        a lower repetition count where the bonus pushes the probability up
        but not to 1.0.
        """
        from aristotle.sm2 import mastery_probability

        # With 2 reps + 0 transfer: (2 + 0) / 2 = 1.0
        # With 2 reps + 1 transfer: (2 + 1.5) / 2 = 1.75 → clamped to 1.0
        # Both clamp to 1.0, so test with a scenario where the boost is
        # visible below the clamp: 1 rep + 1 transfer = (1 + 1.5) / 1 = 2.5
        # → clamped to 1.0. Still clamps. Use slip penalty to pull below 1.0:
        # 3 reps + 0 transfer + 2 slips = (3/3) - 0.15*(2/3) = 1.0 - 0.1 = 0.9
        # 3 reps + 1 transfer + 2 slips = (3 + 1.5)/3 - 0.1 = 1.5 - 0.1 = 1.4 → 1.0
        # The transfer bonus offsets the slip penalty:
        p_no_transfer = mastery_probability(
            repetitions=3, transfer_correct=0, slip_count=2
        )
        p_with_transfer = mastery_probability(
            repetitions=3, transfer_correct=1, slip_count=2
        )
        assert p_with_transfer > p_no_transfer, (
            f"transfer bonus should boost probability: "
            f"no_transfer={p_no_transfer}, with_transfer={p_with_transfer}"
        )


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
        fake = _FakeModelProvider(
            responses={"beast": "Here is a full worked example..."}
        )
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
        fake = _FakeModelProvider(
            responses={"beast": "Here is a conceptual deep-dive..."}
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        socrates = SocratesActor()
        result = await socrates.teach(ctx, "c1", mastery_level=3)
        assert result.ok
        assert result.data["fading_mode"] == "conceptual_only"
        system_msg = fake.calls[0][1][0]["content"]
        assert "conceptual explanation only" in system_msg.lower()
        assert "worked example" not in system_msg.lower().replace(
            "do not include a worked example", ""
        )

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
        conn = _FakeConn(
            rows=[("c1", "Newton's First Law", None, "content", None, None, None, 3)]
        )
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
        assert any(
            phrase in prompt
            for phrase in [
                "wrong guess is fine",
                "just say what comes to mind",
                "what do you think",
            ]
        ), f"prompt should use warm framing, got: {result.data['prompt']}"


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
        fake = _FakeModelProvider(
            responses={"evaluation": "Explain inertia in your own words."}
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.probe(ctx, "c1")
        assert result.ok
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "evaluation"
        # Phase B.5: probe() returns data={"question": ...}, not error=
        assert result.data is not None
        assert "inertia" in result.data["question"].lower()

    @pytest.mark.asyncio
    async def test_quiz_calls_evaluation_slot(self):
        fake = _FakeModelProvider(
            responses={"evaluation": "What is the SI unit of force?"}
        )
        conn = _FakeConn(rows=[("c1", "Force", None, "content", None, None, None, 4)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.quiz(ctx, "c1")
        assert result.ok
        # Phase B.5: quiz() returns data={"question": ...}, not error=
        assert result.data is not None
        assert "force" in result.data["question"].lower()

    @pytest.mark.asyncio
    async def test_evaluate_returns_json(self):
        """evaluate() returns ok=True with data dict (Phase B.5: data, not error-as-payload)."""
        eval_json = json.dumps(
            {"score": 0.8, "mastery_achieved": True, "feedback": "Good"}
        )
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Force", None, "content", None, None, None, 4)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "1 Newton", "What is the SI unit of force?"
        )
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
        eval_json = json.dumps(
            {
                "score": 0.3,
                "mastery_achieved": False,
                "feedback": "Not quite — see diagnosis.",
                "diagnosis": {
                    "misconception": "You seem to think a force is needed to sustain motion",
                    "why_wrong": "Objects keep moving on their own — force changes motion, not sustains it",
                    "corrective": "No force is needed to keep something moving; force changes motion",
                },
            }
        )
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "a force keeps it going", "What is inertia?"
        )
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
        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Exactly — you identified that inertia resists change in motion.",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        assert result.ok
        assert result.data is not None
        assert result.data["mastery_achieved"] is True
        # Diagnosis must be None when correct
        assert result.data["diagnosis"] is None
        # Feedback should be present + non-empty
        assert len(result.data["feedback"]) > 0


# --------------------------------------------------------------------------
# Task 21 — examiner resilience tests (fence-strip + error/empty + retry)
# --------------------------------------------------------------------------


class _FakeSequenceModelProvider:
    """Fake ModelProvider that returns responses in sequence per slot.

    For testing retry logic: each slot has a list of responses; calls
    return them in order. Each response is either a string (treated as
    successful content) or a dict (returned as-is, can include error=True).
    """

    def __init__(self, responses_by_slot: dict[str, list]):
        self._responses_by_slot = responses_by_slot
        self.calls: list[tuple[str, list[dict]]] = []
        self._call_idx_by_slot: dict[str, int] = {}

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        idx = self._call_idx_by_slot.get(slot_name, 0)
        responses = self._responses_by_slot.get(slot_name, [])
        if idx >= len(responses):
            return {
                "error": True,
                "content": "",
                "error_message": "no more canned responses",
            }
        response = responses[idx]
        self._call_idx_by_slot[slot_name] = idx + 1
        if isinstance(response, str):
            return {
                "content": response,
                "model": "fake-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "latency_ms": 5,
            }
        # dict — return as-is (allows error=True)
        return response


class TestExaminerResilience:
    """Task 21: examiner resilience — JSON-fence stripping, error/empty
    call detection, and retry-with-backoff.

    These tests pin the Task 21 fixes:
      - Fix 1: evaluate() strips ```json fences before parsing
      - Fix 2: _generate_question() detects error=True / empty content
        and retries (shared by probe() and quiz())
      - Fix 3: evaluate() detects error=True / empty content, retries
        before falling into the score=0.0 fallback
    """

    @pytest.mark.asyncio
    async def test_evaluate_strips_json_fences(self, monkeypatch):
        """Fix 1: evaluate() parses a ```json-fenced response correctly.

        Reproduces the production bug: the evaluation model wraps its JSON
        in a markdown code fence, json.loads() fails on the raw text, and
        the student is scored 0.0. After the fix, the fence is stripped
        and the JSON parses into the normalized dict.
        """
        # Avoid actually sleeping during retry tests (we won't trigger a
        # retry here, but patch defensively in case the test evolves).
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        # Model wraps its JSON in a ```json fence — exactly what the
        # production log showed (examiner_evaluate_parse_failed raw=```json...).
        fenced_response = (
            "```json\n"
            + json.dumps({
                "score": 0.85,
                "mastery_achieved": True,
                "feedback": "Solid — you connected inertia to resistance to change.",
                "diagnosis": None,
            })
            + "\n```"
        )
        fake = _FakeModelProvider(responses={"evaluation": fenced_response})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        assert result.ok, f"fenced JSON should parse; error={result.error}"
        assert result.data is not None
        assert result.data["score"] == 0.85
        assert result.data["mastery_achieved"] is True
        # The fence was stripped — the parse_failed fallback did NOT trigger
        assert result.data["feedback"] != "[parse error — model did not return valid JSON]"

    @pytest.mark.asyncio
    async def test_evaluate_strips_bare_triple_backtick_fences(self, monkeypatch):
        """Fix 1: evaluate() also handles bare ``` fences (no `json` language tag)."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        fenced_response = (
            "```\n"
            + json.dumps({
                "score": 0.5,
                "mastery_achieved": False,
                "feedback": "Partial.",
                "diagnosis": {
                    "misconception": "x",
                    "why_wrong": "y",
                    "corrective": "z",
                },
            })
            + "\n```"
        )
        fake = _FakeModelProvider(responses={"evaluation": fenced_response})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "a guess", "What is inertia?"
        )
        assert result.ok
        assert result.data["score"] == 0.5
        assert result.data["diagnosis"] is not None
        assert result.data["diagnosis"]["corrective"] == "z"

    @pytest.mark.asyncio
    async def test_evaluate_genuinely_bad_json_still_falls_back(self, monkeypatch):
        """Fix 1: genuinely malformed JSON (not just fenced) still hits the
        score=0.0 fallback. The fence-stripping doesn't paper over real
        parse failures."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        # Not JSON at all, not fenced — a plain-text model output.
        fake = _FakeModelProvider(responses={"evaluation": "I think the answer is correct."})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "a guess", "What is inertia?"
        )
        # The existing fallback path: ok=True with score=0.0 (so the session
        # keeps moving — coordinator treats score=0.0 as "not mastered").
        assert result.ok
        assert result.data["score"] == 0.0
        assert result.data["mastery_achieved"] is False
        assert "parse error" in result.data["feedback"]

    @pytest.mark.asyncio
    async def test_probe_with_error_response_returns_failure(self, monkeypatch):
        """Fix 2: probe() detects error=True and returns ok=False.

        Before the fix, model_provider.call() returning
        {"error": True, "content": ""} (e.g. 429 rate-limit) fell through
        to `question = result.get("content", "")` → empty string → logged
        as `examiner_probe_ok ... question_len=0` (success!). Now it
        returns ok=False with a clear error.
        """
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        # Always returns error — simulates persistent rate-limit.
        fake = _FakeSequenceModelProvider(
            responses_by_slot={
                "evaluation": [
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                ],
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.probe(ctx, "c1")
        assert not result.ok, (
            "probe() should return ok=False when the model call returns error=True"
        )
        assert "model call failed" in result.error
        # Retries: max_retries=2 means up to 3 total attempts (1 + 2 retries).
        assert len(fake.calls) == 3, (
            f"probe() should retry up to 2 times (3 total calls); got {len(fake.calls)}"
        )

    @pytest.mark.asyncio
    async def test_quiz_with_empty_content_returns_failure(self, monkeypatch):
        """Fix 2: quiz() detects empty content (no error key, but content is
        whitespace-only) and returns ok=False. This is the OpenRouter free-tier
        pattern: 200 OK with empty content on rate-limit."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        fake = _FakeSequenceModelProvider(
            responses_by_slot={
                "evaluation": [
                    {"content": "   ", "model": "fake", "usage": {}, "latency_ms": 1},
                    {"content": "", "model": "fake", "usage": {}, "latency_ms": 1},
                    {"content": "\n\t", "model": "fake", "usage": {}, "latency_ms": 1},
                ],
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.quiz(ctx, "c1")
        assert not result.ok, (
            "quiz() should return ok=False when the model returns empty content"
        )
        assert "empty content" in result.error

    @pytest.mark.asyncio
    async def test_probe_retries_then_succeeds(self, monkeypatch):
        """Fix 2: probe() retries on error, then succeeds when the model recovers.

        First call: error=True (transient 429). Second call: success.
        Verify the retry happened (call count = 2) and the success path
        returned the question.
        """
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        fake = _FakeSequenceModelProvider(
            responses_by_slot={
                "evaluation": [
                    {"error": True, "content": "", "error_message": "429"},
                    "What does inertia mean in your own words?",
                ],
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.probe(ctx, "c1")
        assert result.ok, f"probe() should succeed after retry; error={result.error}"
        assert result.data is not None
        assert "inertia" in result.data["question"].lower()
        assert len(fake.calls) == 2, (
            f"probe() should have called the model twice (1 fail + 1 success); "
            f"got {len(fake.calls)}"
        )

    @pytest.mark.asyncio
    async def test_evaluate_with_error_response_retries_then_fails(self, monkeypatch):
        """Fix 3: evaluate() retries on error=True before returning ok=False.

        Before the fix, an error response from the evaluation slot was
        indistinguishable from genuinely-malformed JSON — both fell into
        the score=0.0 fallback. The fix distinguishes them: an error
        response triggers a retry (up to 2), and only after retries are
        exhausted does it return ok=False (NOT ok=True with score=0.0).

        This test verifies the retry happens (call count = 3) and the
        final result is ok=False (not the score=0.0 fallback).
        """
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        fake = _FakeSequenceModelProvider(
            responses_by_slot={
                "evaluation": [
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                    {"error": True, "content": "", "error_message": "429 rate limit"},
                ],
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        # Fix 3: ok=False (not the score=0.0 fallback) when the call failed.
        assert not result.ok, (
            "evaluate() should return ok=False when the model call returns "
            "error=True after retries — NOT ok=True with score=0.0"
        )
        assert "model call failed" in result.error
        # Verify retries happened: max_retries=2 → 3 total calls.
        assert len(fake.calls) == 3, (
            f"evaluate() should retry up to 2 times (3 total calls); "
            f"got {len(fake.calls)}"
        )

    @pytest.mark.asyncio
    async def test_evaluate_retries_then_succeeds(self, monkeypatch):
        """Fix 3: evaluate() retries on error, then succeeds with valid JSON.

        First call: error=True (transient 429). Second call: valid JSON.
        Verify the retry happened (call count = 2) and the success path
        returned the parsed score (NOT score=0.0).
        """
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        good_json = json.dumps({
            "score": 0.9,
            "mastery_achieved": True,
            "feedback": "Excellent.",
            "diagnosis": None,
        })
        fake = _FakeSequenceModelProvider(
            responses_by_slot={
                "evaluation": [
                    {"error": True, "content": "", "error_message": "429"},
                    good_json,
                ],
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        assert result.ok, f"evaluate() should succeed after retry; error={result.error}"
        assert result.data["score"] == 0.9, (
            "evaluate() should return the actual model score, not the 0.0 fallback"
        )
        assert result.data["mastery_achieved"] is True
        assert len(fake.calls) == 2


# --------------------------------------------------------------------------
# Task 23 — decline-to-answer gate + mastery-scoring integrity
# --------------------------------------------------------------------------


class TestExaminerDeclineGate:
    """Task 23 Fix 1: examiner.evaluate() gates on decline-to-answer phrases
    BEFORE calling the model. A live placement run showed the tutor walking
    through 7 concepts while the student answered "no. teach me about it."
    to every single one — and the system kept advancing to a NEW concept
    each time instead of teaching any of them. Root cause: the free-tier
    model mishandled the refusal-style non-content answer and self-reported
    mastery_achieved=true. The gate lives INSIDE evaluate() so every caller
    (placement, quiz-check, any future caller) gets it for free.
    """

    @pytest.mark.asyncio
    async def test_decline_phrases_skip_model_call(self, monkeypatch):
        """Each string in _DECLINE_PATTERNS returns mastery_achieved=False
        WITHOUT calling the model. The fake model is wired but should
        receive zero calls."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        from aristotle.actors.examiner import _DECLINE_PATTERNS

        # Use a few representative decline phrases (not the whole set —
        # the test would be slow + brittle). Cover: bare "no", "idk",
        # "teach me about it", "no. teach me about it" (the exact phrase
        # from the production transcript).
        test_phrases = [
            "no",
            "idk",
            "i don't know",
            "teach me",
            "teach me about it",
            "no. teach me about it",
            "no, teach me about it",
            "just teach me",
            "pass",
            "skip",
            "No. Teach me about it.",  # mixed case + trailing period
            "  teach me  ",  # leading/trailing whitespace
        ]
        for phrase in test_phrases:
            fake = _FakeModelProvider(
                responses={"evaluation": json.dumps({"score": 1.0, "mastery_achieved": True, "feedback": "should not be called", "diagnosis": None})}
            )
            conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
            ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
            examiner = ExaminerActor()
            result = await examiner.evaluate(
                ctx, "c1", phrase, "What is inertia?"
            )
            assert result.ok, (
                f"decline phrase {phrase!r} should return ok=True (not a failure)"
            )
            assert result.data["mastery_achieved"] is False, (
                f"decline phrase {phrase!r} must return mastery_achieved=False — "
                f"the student explicitly declined, so don't grade"
            )
            assert result.data["score"] == 0.0, (
                f"decline phrase {phrase!r} must return score=0.0"
            )
            assert len(fake.calls) == 0, (
                f"decline phrase {phrase!r} must NOT call the model — "
                f"the gate should short-circuit before the model call. "
                f"Got {len(fake.calls)} call(s)."
            )

    @pytest.mark.asyncio
    async def test_genuine_answer_starting_with_no_is_not_gated(self, monkeypatch):
        """REGRESSION TEST (the one that matters most): a genuine content
        answer that happens to start with "no" (e.g. "no, it binds
        non-covalently to the receptor") must NOT trigger the gate — it
        must reach the model and be graded normally.

        This is the false-positive case the gate is designed to avoid.
        If this test fails, the gate is too loose and is silently
        dropping real answers.
        """
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        # Model returns a real grade for a real answer.
        eval_json = json.dumps({
            "score": 0.85,
            "mastery_achieved": True,
            "feedback": "Yes — non-covalent binding is correct.",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        # The answer starts with "no" but is clearly a content attempt.
        result = await examiner.evaluate(
            ctx, "c1",
            "no, it binds non-covalently to the receptor",
            "How does the ligand bind?",
        )
        assert result.ok, f"genuine answer should reach the model; error={result.error}"
        assert result.data["score"] == 0.85, (
            "genuine answer should be graded by the model, not gated"
        )
        assert len(fake.calls) == 1, (
            "genuine answer MUST reach the model — the gate must not fire "
            "on a real content answer that happens to start with 'no'"
        )

    @pytest.mark.asyncio
    async def test_long_teach_me_question_is_not_gated(self, monkeypatch):
        """A long message that includes 'teach me' (e.g. a real question
        like "Can you teach me more about how the receptor binds? I think
        it's covalent") is NOT a pure decline — it's a content attempt
        with a question. The 40-char ceiling on the 'teach me' substring
        check ensures this reaches the model."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        eval_json = json.dumps({
            "score": 0.4,
            "mastery_achieved": False,
            "feedback": "Partial — you mentioned covalent, but it's non-covalent.",
            "diagnosis": {
                "misconception": "thought it was covalent",
                "why_wrong": "it's non-covalent",
                "corrective": "non-covalent binding",
            },
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        long_question = (
            "Can you teach me more about how the receptor binds? I think it's covalent"
        )
        result = await examiner.evaluate(
            ctx, "c1", long_question, "How does the ligand bind?",
        )
        assert result.ok
        assert len(fake.calls) == 1, (
            "long question containing 'teach me' must reach the model — "
            "the 40-char ceiling prevents the substring match from gating "
            "real questions"
        )
        assert result.data["score"] == 0.4

    @pytest.mark.asyncio
    async def test_decline_feedback_is_gentle_not_grading(self, monkeypatch):
        """The feedback string on a decline is gentle ("No problem — let's
        move on to teaching this one."), NOT a grading judgment. The
        student explicitly declined, so the message should not imply the
        answer was wrong."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        fake = _FakeModelProvider()
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "no. teach me about it.", "What is inertia?"
        )
        assert result.ok
        feedback = result.data["feedback"]
        # Gentle: does NOT contain grading words like "wrong", "incorrect",
        # "not quite". DOES contain a "let's move on" signal.
        assert "wrong" not in feedback.lower()
        assert "incorrect" not in feedback.lower()
        assert "not quite" not in feedback.lower()
        assert "no problem" in feedback.lower() or "let's move on" in feedback.lower(), (
            f"decline feedback should be gentle, not a grading judgment; got: {feedback!r}"
        )


class TestExaminerMasteryDerivation:
    """Task 23 Fix 2: evaluate() derives mastery_achieved IN CODE from
    score >= mastery_threshold, ignoring the model's self-reported boolean.

    A live placement run showed the model self-reporting
    mastery_achieved=true on refusal-style non-content answers. Even with
    Fix 1's gate, we can't trust the model's boolean — it could return an
    internally inconsistent response (score=0.3 + mastery_achieved=true).
    The code now overrides with the deterministic threshold check.
    """

    @pytest.mark.asyncio
    async def test_inconsistent_low_score_high_mastery_overridden(self, monkeypatch):
        """Model returns score=0.3 + mastery_achieved=true (inconsistent).
        Code must override mastery_achieved to False (0.3 < 0.7 threshold)."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        eval_json = json.dumps({
            "score": 0.3,
            "mastery_achieved": True,  # inconsistent with score
            "feedback": "should be overridden",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "a guess", "What is inertia?"
        )
        assert result.ok
        assert result.data["score"] == 0.3
        assert result.data["mastery_achieved"] is False, (
            "mastery_achieved must be DERIVED from score (0.3 < 0.7), "
            "not trusted from the model's self-report (which said True)"
        )

    @pytest.mark.asyncio
    async def test_inconsistent_high_score_low_mastery_overridden(self, monkeypatch):
        """Model returns score=0.9 + mastery_achieved=false (inconsistent).
        Code must override mastery_achieved to True (0.9 >= 0.7 threshold)."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        eval_json = json.dumps({
            "score": 0.9,
            "mastery_achieved": False,  # inconsistent with score
            "feedback": "should be overridden",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        assert result.ok
        assert result.data["score"] == 0.9
        assert result.data["mastery_achieved"] is True, (
            "mastery_achieved must be DERIVED from score (0.9 >= 0.7), "
            "not trusted from the model's self-report (which said False)"
        )

    @pytest.mark.asyncio
    async def test_consistent_response_passes_through(self, monkeypatch):
        """When the model's score + mastery_achieved are consistent, the
        derived value matches the model's report. Regression check that
        Fix 2 didn't break the happy path."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        eval_json = json.dumps({
            "score": 0.8,
            "mastery_achieved": True,  # consistent
            "feedback": "Good.",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "objects resist changes in motion", "What is inertia?"
        )
        assert result.ok
        assert result.data["score"] == 0.8
        assert result.data["mastery_achieved"] is True

    @pytest.mark.asyncio
    async def test_mastery_threshold_from_config(self, monkeypatch):
        """The derived mastery_achieved uses the config's mastery_threshold
        (default 0.7). Verify a custom threshold is respected."""
        async def _no_sleep(_seconds):
            pass
        monkeypatch.setattr(
            "aristotle.actors.examiner.asyncio.sleep", _no_sleep
        )

        eval_json = json.dumps({
            "score": 0.6,
            "mastery_achieved": False,
            "feedback": "ok",
            "diagnosis": None,
        })
        fake = _FakeModelProvider(responses={"evaluation": eval_json})
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        # Custom config with mastery_threshold=0.5
        config = type("C", (), {"mastery_threshold": 0.5})()
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn), config=config)
        examiner = ExaminerActor()
        result = await examiner.evaluate(
            ctx, "c1", "a guess", "What is inertia?"
        )
        assert result.ok
        assert result.data["score"] == 0.6
        # 0.6 >= 0.5 (custom threshold) → True, even though the model said False
        assert result.data["mastery_achieved"] is True, (
            "derived mastery should use the config's mastery_threshold (0.5), "
            "so score=0.6 → mastery_achieved=True"
        )


# --------------------------------------------------------------------------
# Task 21 — SOCRATES prompt length + register (Fix 4)
# --------------------------------------------------------------------------


class TestSocratesPromptLength:
    """Task 21 Fix 4: SOCRATES._build_system_prompt includes an explicit
    length ceiling + plain-language register instruction.

    Without it, the `full_worked_example` fading mode's "Show every step"
    instruction produced ~5000-character single explanations in production.
    We can't unit-test LLM output length, but we can pin the prompt string
    so a future refactor can't silently drop the constraint.
    """

    def test_base_prompt_contains_length_instruction(self):
        """The base prompt (applies to all fading modes) carries an explicit
        length ceiling: 2-4 short paragraphs maximum."""
        socrates = SocratesActor()
        prompt = socrates._build_system_prompt(mastery_level=0)
        assert "LENGTH AND REGISTER" in prompt, (
            "Base prompt must include the LENGTH AND REGISTER section header"
        )
        assert "2 to 4 short paragraphs" in prompt, (
            "Base prompt must specify the 2-4 paragraph ceiling"
        )
        assert "plain, everyday English" in prompt, (
            "Base prompt must specify plain-language register "
            "(many learners study in a second language)"
        )
        assert "short sentences" in prompt, (
            "Base prompt must prefer short sentences over long ones"
        )

    def test_length_instruction_applies_across_all_fading_modes(self):
        """The length instruction is in the base prompt, so it appears in
        every fading mode (level 0, 2, and 3+)."""
        socrates = SocratesActor()
        for level in [0, 1, 2, 3, 5]:
            prompt = socrates._build_system_prompt(mastery_level=level)
            assert "LENGTH AND REGISTER" in prompt, (
                f"Length instruction must appear at mastery_level={level}"
            )

    def test_full_worked_example_has_per_step_verbosity_bound(self):
        """The full_worked_example branch softens 'Show every step — do not
        skip anything' to bound per-step verbosity. Production bug: without
        the bound, the model produced ~5000-char single explanations."""
        socrates = SocratesActor()
        prompt = socrates._build_system_prompt(mastery_level=0)
        assert "full worked example" in prompt.lower()
        # The new bound: each step kept to 1-2 sentences.
        assert "1-2 sentences" in prompt or "1 to 2 sentences" in prompt, (
            "full_worked_example branch must bound per-step verbosity "
            "(1-2 sentences per step), not just say 'show every step'"
        )
        # The completeness-of-steps instruction is preserved.
        assert "completeness of steps" in prompt.lower(), (
            "The 'completeness of steps' instruction must be preserved — "
            "we bound per-step verbosity, not the number of steps shown"
        )


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
        fake = _FakeModelProvider(
            responses={
                "evaluation": "Think about what happens to a passenger when a bus brakes suddenly.",
            }
        )
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
        fake = _FakeModelProvider(
            responses={
                "evaluation": "The answer relates to inertia — objects tend to keep doing what they're doing unless a force acts on them. What's the specific term?",
            }
        )
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
        fake = _FakeModelProvider(
            responses={"sexton": "Learner struggles with vector decomposition."}
        )
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
# EXAMINER quiz transfer tests (Phase B.5 item 6)
# --------------------------------------------------------------------------


class TestExaminerQuizTransfer:
    """Phase B.5: ExaminerActor.quiz() supports recognition vs. transfer.

    Transfer questions apply the concept to a new situation; recognition
    questions test identification/recall. The session coordinator selects
    based on mastery_level (recognition for <2, transfer for >=2).
    """

    @pytest.mark.asyncio
    async def test_quiz_recognition_type_for_low_mastery(self):
        """quiz(question_type='recognition') sends a recognition prompt.

        The system prompt should mention 'RECOGNITION' — a definition/
        identification check, not an application scenario.
        """
        fake = _FakeModelProvider(
            responses={"evaluation": "What is Newton's First Law?"}
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.quiz(ctx, "c1", question_type="recognition")
        assert result.ok
        assert result.data["question_type"] == "recognition"
        system_msg = fake.calls[0][1][0]["content"]
        assert "RECOGNITION" in system_msg

    @pytest.mark.asyncio
    async def test_quiz_transfer_type_for_high_mastery(self):
        """quiz(question_type='transfer') sends a transfer prompt.

        The system prompt should mention 'TRANSFER' + 'NEW situation' —
        an application scenario, not a definition check.
        """
        fake = _FakeModelProvider(
            responses={"evaluation": "A spacecraft is coasting..."}
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        examiner = ExaminerActor()
        result = await examiner.quiz(ctx, "c1", question_type="transfer")
        assert result.ok
        assert result.data["question_type"] == "transfer"
        system_msg = fake.calls[0][1][0]["content"]
        assert "TRANSFER" in system_msg
        assert "NEW situation" in system_msg

    @pytest.mark.asyncio
    async def test_quiz_transfer_increments_transfer_attempted(self):
        """_step_quiz with mastery_level >= 2 issues an UPDATE on transfer_attempted.

        Phase B.5: when the coordinator selects a transfer question, it
        increments transfer_attempted on aristotle_mastery (column from M003).
        This test uses a routing fake conn that returns a high mastery level
        for the mastery query + the concept row for the concept query.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        class _RoutingConn:
            """Fake conn that returns different rows based on the SQL query."""

            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "repetitions" in sql.lower():
                    # mastery query → return (3,) = 3 repetitions → level 3 → transfer
                    return _FakeCursor([(3,)])
                else:
                    # concept query → return 8-column concept row
                    return _FakeCursor(
                        [("c1", "Inertia", None, "content", None, None, None, 3)]
                    )

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(
            responses={
                "evaluation": "A spacecraft is coasting through space...",
            }
        )
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(concept_id="c1", state=SessionState.QUIZ)
        result = await run_session_step(ctx, session)
        assert result.ok
        # The coordinator should have selected transfer (mastery_level=3 >= 2)
        assert session.last_question_type == "transfer"
        # And issued an UPDATE on transfer_attempted
        update_calls = [
            sql
            for sql, _ in conn._executed
            if "transfer_attempted" in sql and "UPDATE" in sql.upper()
        ]
        assert len(update_calls) == 1, "expected one UPDATE on transfer_attempted"

    @pytest.mark.asyncio
    async def test_correct_transfer_answer_increments_transfer_correct(self):
        """_step_evaluate with a correct transfer answer increments transfer_correct.

        Phase B.5: when the answer is correct AND the last question was a
        transfer question, the coordinator increments transfer_correct on
        aristotle_mastery (column from M003).
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "repetitions" in sql.lower():
                    return _FakeCursor([(3,)])
                else:
                    return _FakeCursor(
                        [("c1", "Inertia", None, "content", None, None, None, 3)]
                    )

            async def commit(self):
                pass

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )
        conn = _RoutingConn()
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner does well.",
            }
        )
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            last_question_type="transfer",  # the last quiz was a transfer question
            last_quiz_question="A spacecraft is coasting...",
            last_student_answer="It keeps coasting — no force needed",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.NEXT_CONCEPT  # correct → next concept
        # The coordinator should have issued an UPDATE on transfer_correct
        update_calls = [
            sql
            for sql, _ in conn._executed
            if "transfer_correct" in sql and "UPDATE" in sql.upper()
        ]
        assert len(update_calls) == 1, "expected one UPDATE on transfer_correct"


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
            ctx,
            concept_id="c1",
            session_id="sess-1",
            diagnosis=diagnosis,
        )
        assert result.ok
        # data confirms the write
        assert result.data is not None
        assert result.data["logged"] is True
        assert result.data["concept_id"] == "c1"
        # The INSERT was executed on the fake conn
        insert_calls = [
            (sql, params)
            for sql, params in conn._executed
            if "INSERT INTO aristotle_misconception_log" in sql
        ]
        assert len(insert_calls) == 1, (
            "expected one INSERT into aristotle_misconception_log"
        )
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

        container = type(
            "C",
            (),
            {
                "corpus_registry": _ExplodingRegistry(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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
            ctx,
            concept_id="c1",
            session_id="sess-1",
            diagnosis=diagnosis,
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
            ctx,
            concept_id="c1",
            session_id="sess-1",
            diagnosis=diagnosis,
        )
        assert result.ok
        assert result.data["logged"] is True
        # The INSERT should have empty string for the missing corrective
        insert_calls = [
            (sql, params)
            for sql, params in conn._executed
            if "INSERT INTO aristotle_misconception_log" in sql
        ]
        assert len(insert_calls) == 1
        _sql, params = insert_calls[0]
        assert params[2] == "partial thought"  # misconception_text
        assert params[3] == ""  # corrective_text (missing → empty string)


# --------------------------------------------------------------------------
# MENTOR pattern recognition tests (ADR-002 §7, Phase D)
# --------------------------------------------------------------------------


class TestMentorPatternRecognition:
    """Phase D: MENTOR synthesizes struggle patterns from misconception log.

    After 3+ misconception entries for the same concept, MENTOR calls the
    model to synthesize the underlying pattern. Fires at count 3, 6, 9 —
    not at 1, 2, 4, 5, etc.
    """

    @pytest.mark.asyncio
    async def test_mentor_synthesize_pattern_returns_pattern_string(self):
        """synthesize_struggle_pattern() returns ok=True with data.pattern as a string."""
        fake = _FakeModelProvider(
            responses={
                "mentor": "The learner consistently confuses force as a cause of motion rather than a change in motion.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        mentor = MentorActor()
        result = await mentor.synthesize_struggle_pattern(
            ctx,
            "c1",
            ["thinks force sustains motion", "confuses inertia with friction"],
        )
        assert result.ok
        assert result.data is not None
        assert "pattern" in result.data
        assert isinstance(result.data["pattern"], str)
        assert len(result.data["pattern"]) > 0
        # The model should have been called with the mentor slot
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "mentor"

    @pytest.mark.asyncio
    async def test_mentor_synthesize_pattern_concept_not_found_returns_ok(self):
        """synthesize_struggle_pattern() returns ok=True even when concept is not found.

        Best-effort: returns ok=True with data.pattern (the model still
        generates a pattern — it just uses the concept_id as the name
        instead of the topic).
        """
        fake = _FakeModelProvider(
            responses={
                "mentor": "Some pattern sentence.",
            }
        )
        conn = _FakeConn(rows=None)  # no concept row → concept_id used as name
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        mentor = MentorActor()
        result = await mentor.synthesize_struggle_pattern(
            ctx,
            "nonexistent",
            ["some misconception"],
        )
        assert result.ok
        assert result.data["pattern"] == "Some pattern sentence."

    @pytest.mark.asyncio
    async def test_check_synthesize_fires_at_count_3(self):
        """_check_and_synthesize_pattern fires when count=3 (3 % 3 == 0)."""
        from aristotle.session import _check_and_synthesize_pattern

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "COUNT(*)" in sql:
                    return _FakeCursor([(3,)])  # count = 3
                if "misconception_text" in sql.lower():
                    return _FakeCursor([("m1",), ("m2",), ("m3",)])
                if "topic" in sql.lower():
                    return _FakeCursor([("Inertia",)])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(responses={"mentor": "Underlying pattern."})
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        await _check_and_synthesize_pattern(ctx, "c1")
        # Should have issued an INSERT OR REPLACE into struggle_pattern
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_struggle_pattern" in sql
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_check_synthesize_fires_at_count_6(self):
        """_check_and_synthesize_pattern fires when count=6 (6 % 3 == 0)."""
        from aristotle.session import _check_and_synthesize_pattern

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "COUNT(*)" in sql:
                    return _FakeCursor([(6,)])  # count = 6
                if "misconception_text" in sql.lower():
                    return _FakeCursor([("m1",), ("m2",), ("m3",)])
                if "topic" in sql.lower():
                    return _FakeCursor([("Inertia",)])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(responses={"mentor": "Pattern at 6."})
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        await _check_and_synthesize_pattern(ctx, "c1")
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_struggle_pattern" in sql
        ]
        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_check_synthesize_skips_at_count_4(self):
        """_check_and_synthesize_pattern does NOT fire when count=4 (4 % 3 != 0)."""
        from aristotle.session import _check_and_synthesize_pattern

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "COUNT(*)" in sql:
                    return _FakeCursor([(4,)])  # count = 4 — NOT a multiple of 3
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(responses={"mentor": "Should not fire."})
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        await _check_and_synthesize_pattern(ctx, "c1")
        # Should NOT have issued any INSERT OR REPLACE — count=4 is not a multiple of 3
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_struggle_pattern" in sql
        ]
        assert len(insert_calls) == 0

    @pytest.mark.asyncio
    async def test_check_synthesize_skips_below_threshold(self):
        """_check_and_synthesize_pattern does NOT fire when count < 3."""
        from aristotle.session import _check_and_synthesize_pattern

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "COUNT(*)" in sql:
                    return _FakeCursor([(2,)])  # count = 2 — below threshold
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(responses={"mentor": "Should not fire."})
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        await _check_and_synthesize_pattern(ctx, "c1")
        insert_calls = [
            sql
            for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_struggle_pattern" in sql
        ]
        assert len(insert_calls) == 0


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

        fake = _FakeModelProvider(
            responses={
                "beast": "Newton's First Law states...",
                "evaluation": "Explain in your own words.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        # The coordinator checks `container.extensions.registry` — provide a
        # non-None registry so it passes the availability check. The
        # coordinator then imports aristotle.actors directly (the actors are
        # stateless for Phase A), so the registry doesn't need real actors.
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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

        eval_json = json.dumps(
            {
                "score": 0.3,
                "mastery_achieved": False,
                "feedback": "Not quite.",
                "diagnosis": {
                    "misconception": "You seem to think a force is needed to sustain motion",
                    "why_wrong": "Objects keep moving on their own",
                    "corrective": "Force changes motion, not sustains it",
                },
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner struggles with inertia.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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
        assert (
            session.last_diagnosis["misconception"]
            == "You seem to think a force is needed to sustain motion"
        )

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

        eval_json = json.dumps(
            {
                "score": 0.3,
                "mastery_achieved": False,
                "feedback": "Not quite.",
                "diagnosis": {
                    "misconception": "You seem to think a force is needed to sustain motion",
                    "why_wrong": "Objects keep moving on their own",
                    "corrective": "Force changes motion, not sustains it",
                },
            }
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner struggles with inertia.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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
            sql
            for sql, _params in conn._executed
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
        eval_json = json.dumps(
            {"score": 0.3, "mastery_achieved": False, "feedback": "Try again"}
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner struggles with inertia.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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
        eval_json = json.dumps(
            {"score": 0.3, "mastery_achieved": False, "feedback": "Try again"}
        )
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner struggles with inertia.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
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

    # ------------------------------------------------------------------
    # Phase B.5 item 5: session interleaving (concept_queue)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_session_queue_includes_due_review_concepts(self):
        """_build_concept_queue returns [primary, review1, review2?] when reviews are due.

        Phase B.5 item 5: the session queue interleaves the primary concept
        with up to 2 due review concepts. This test uses a routing fake conn
        that returns 2 due review concepts for the mastery query.
        """
        from aristotle.session import _build_concept_queue

        class _RoutingConn:
            async def execute(self, sql, params=()):
                if (
                    "aristotle_mastery" in sql.lower()
                    and "next_review_at" in sql.lower()
                ):
                    # Due review concepts query → return 2 rows
                    return _FakeCursor(
                        [
                            ("review_1", 6, 0),
                            ("review_2", 14, 0),
                        ]
                    )
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        queue, cold_start = await _build_concept_queue(ctx, "primary_concept")
        assert len(queue) == 3  # primary + 2 reviews
        assert queue[0] == "primary_concept"
        assert "review_1" in queue
        assert "review_2" in queue

    @pytest.mark.asyncio
    async def test_session_queue_primary_only_when_nothing_due(self):
        """_build_concept_queue returns [primary] when no reviews are due."""
        from aristotle.session import _build_concept_queue

        conn = _FakeConn(rows=None)  # no due reviews
        ctx = _make_ctx(stores=_FakeStores(conn))
        queue, cold_start = await _build_concept_queue(ctx, "primary_concept")
        assert len(queue) == 1
        assert queue[0] == "primary_concept"
        assert cold_start == set()

    @pytest.mark.asyncio
    async def test_session_advances_to_next_concept_in_queue(self):
        """After NEXT_CONCEPT with a non-empty queue, concept_id changes + state resets.

        Phase B.5 item 5: when the current concept is mastered, the session
        pops concept_queue[0] and advances to the next concept. The state
        resets to PREDICT (or PROBE if cold-start pending), hint_count
        resets to 0, etc.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="concept_a",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=["concept_a", "concept_b"],
            hint_count=2,
            last_diagnosis={"misconception": "x"},
            last_question_type="transfer",
            predict_generated=True,
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        # concept_id should have advanced to concept_b
        assert session.concept_id == "concept_b"
        assert session.state == SessionState.PREDICT  # reset to PREDICT
        assert session.hint_count == 0  # reset
        assert session.last_diagnosis is None  # reset
        assert session.last_question_type == "recognition"  # reset
        assert session.predict_generated is False  # reset

    @pytest.mark.asyncio
    async def test_session_completes_when_queue_empty(self):
        """After the last concept in the queue, state is SESSION_COMPLETE."""
        from aristotle.session import SessionContext, SessionState, run_session_step

        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="last_concept",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=[
                "last_concept"
            ],  # only one concept — will be empty after pop
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.SESSION_COMPLETE

    # ------------------------------------------------------------------
    # Phase B.5 item 9: cold-start check
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cold_start_skips_predict_goes_to_probe(self):
        """When a concept is in cold_start_pending, NEXT_CONCEPT sets state to PROBE.

        Phase B.5 item 9: a cold-start concept (SM-2 interval >= 7 days,
        cold_start_passed == 0) skips PREDICT/TEACH and goes directly to
        PROBE — unassisted retrieval. This test verifies the queue-advance
        logic sets PROBE (not PREDICT) when the next concept is cold-start
        pending.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        fake = _FakeModelProvider()
        conn = _FakeConn(rows=None)
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="concept_a",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=["concept_a", "concept_b"],
            cold_start_pending={"concept_b"},  # concept_b needs a cold-start check
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.concept_id == "concept_b"
        # Cold-start: state should be PROBE, not PREDICT
        assert session.state == SessionState.PROBE, (
            f"Cold-start concept should go to PROBE (not PREDICT), got {session.state}"
        )

    @pytest.mark.asyncio
    async def test_cold_start_correct_marks_passed(self):
        """A correct cold-start answer marks cold_start_passed = 1 + removes from pending.

        Phase B.5 item 9: when the learner answers correctly on a cold-start
        concept, the coordinator updates cold_start_passed = 1 on
        aristotle_mastery + removes the concept from cold_start_pending.
        This test verifies the UPDATE is issued + the concept is removed.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        eval_json = json.dumps(
            {
                "score": 0.9,
                "mastery_achieved": True,
                "feedback": "Good",
                "diagnosis": None,
            }
        )

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "repetitions" in sql.lower():
                    return _FakeCursor([(3,)])
                return _FakeCursor(
                    [("c1", "Inertia", None, "content", None, None, None, 3)]
                )

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider(
            responses={
                "evaluation": eval_json,
                "sexton": "Learner does well.",
            }
        )
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="c1",
            state=SessionState.EVALUATE,
            cold_start_pending={"c1"},  # c1 is a cold-start concept
            last_quiz_question="What is inertia?",
            last_student_answer="objects resist changes in motion",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.NEXT_CONCEPT  # correct → next concept
        # c1 should be removed from cold_start_pending
        assert "c1" not in session.cold_start_pending
        # An UPDATE setting cold_start_passed = 1 should have been issued
        update_calls = [
            sql
            for sql, _ in conn._executed
            if "cold_start_passed = 1" in sql and "UPDATE" in sql.upper()
        ]
        assert len(update_calls) == 1, (
            "expected one UPDATE setting cold_start_passed = 1"
        )

    # ------------------------------------------------------------------
    # Phase D: plan executor bridge (plan_id on SessionContext)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_concept_queue_reads_from_plan_when_plan_id_set(self):
        """When plan_id provided, primary concept comes from plan not caller arg.

        Phase D: _build_concept_queue reads the plan's concept_ids_json
        [current_concept_idx] as the primary concept, ignoring the
        caller-supplied primary_concept_id.
        """
        from aristotle.session import _build_concept_queue

        class _RoutingConn:
            async def execute(self, sql, params=()):
                if "aristotle_learning_plan" in sql.lower():
                    # Plan: 3 concepts, current_idx=1 → primary = "c2"
                    import json as _json

                    return _FakeCursor([(_json.dumps(["c1", "c2", "c3"]), 1, "active")])
                if (
                    "aristotle_mastery" in sql.lower()
                    and "next_review_at" in sql.lower()
                ):
                    return _FakeCursor(None)  # no due reviews
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        # Caller passes "ignored_concept" but plan says primary is "c2"
        queue, cold_start = await _build_concept_queue(
            ctx, "ignored_concept", plan_id="plan-1"
        )
        assert queue[0] == "c2", f"primary should be c2 (from plan), got {queue[0]}"
        assert "ignored_concept" not in queue

    @pytest.mark.asyncio
    async def test_next_concept_advances_plan_cursor(self):
        """After completing a concept with plan attached, plan cursor moves.

        Phase D: _step_next_concept calls _advance_plan_cursor which
        increments current_concept_idx on the plan.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if (
                    "aristotle_learning_plan" in sql.lower()
                    and "concept_ids_json" in sql.lower()
                ):
                    import json as _json

                    return _FakeCursor([(_json.dumps(["c1", "c2", "c3"]), 0, "active")])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider()
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="c1",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=["c1", "c2"],
            plan_id="plan-1",
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        # The cursor should have been advanced — look for the UPDATE.
        update_calls = [
            sql
            for sql, _ in conn._executed
            if "UPDATE aristotle_learning_plan SET current_concept_idx" in sql
        ]
        assert len(update_calls) == 1, "expected one UPDATE on current_concept_idx"

    @pytest.mark.asyncio
    async def test_long_arc_continues_to_next_plan_concept(self):
        """When queue empties but plan has more concepts, session continues.

        Phase D (long-arc executor): after the queue empties, if the plan
        has more concepts, the session rebuilds the queue from the plan
        instead of going to SESSION_COMPLETE.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if (
                    "aristotle_learning_plan" in sql.lower()
                    and "concept_ids_json" in sql.lower()
                ):
                    import json as _json

                    return _FakeCursor([(_json.dumps(["c1", "c2", "c3"]), 1, "active")])
                if (
                    "aristotle_mastery" in sql.lower()
                    and "next_review_at" in sql.lower()
                ):
                    return _FakeCursor(None)  # no due reviews
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider()
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="c1",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=["c1"],  # only 1 concept — will be empty after pop
            plan_id="plan-1",  # plan has c2, c3 remaining (current_idx was 0, now 1)
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        # Should NOT be SESSION_COMPLETE — should have rebuilt from plan
        assert session.state != SessionState.SESSION_COMPLETE, (
            "long-arc should continue, not complete"
        )
        assert session.concept_id == "c2", (
            f"should have advanced to c2 from plan, got {session.concept_id}"
        )

    @pytest.mark.asyncio
    async def test_session_completes_when_plan_exhausted(self):
        """When the plan is complete, session goes to SESSION_COMPLETE.

        Phase D: if the plan's status='complete' (all concepts done),
        _build_concept_queue returns an empty queue, and the session
        goes to SESSION_COMPLETE.
        """
        from aristotle.session import SessionContext, SessionState, run_session_step

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if (
                    "aristotle_learning_plan" in sql.lower()
                    and "concept_ids_json" in sql.lower()
                ):
                    import json as _json

                    return _FakeCursor(
                        [(_json.dumps(["c1", "c2", "c3"]), 3, "complete")]
                    )
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        fake = _FakeModelProvider()
        container = type(
            "C",
            (),
            {
                "model_provider": fake,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
                "extensions": type("H", (), {"registry": "fake"})(),
            },
        )()
        ctx = ActorContext(
            container=container,
            config=None,
            logger=__import__("logging").getLogger("test"),
            cancel_event=asyncio.Event(),
        )
        session = SessionContext(
            concept_id="c3",
            state=SessionState.NEXT_CONCEPT,
            concept_queue=["c3"],  # last concept — will be empty after pop
            plan_id="plan-1",  # plan status='complete'
        )
        result = await run_session_step(ctx, session)
        assert result.ok
        assert session.state == SessionState.SESSION_COMPLETE
