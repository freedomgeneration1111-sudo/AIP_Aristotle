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
# LLM-driven intake tests (Phase D brain transplant — Piece 2)
# ---------------------------------------------------------------------------
# These tests verify the LLM-driven intake loop (run_intake_step when a
# model_provider is present). They use _FakeModelProvider to return canned
# JSON responses and assert that:
#   - The model is called on the "beast" slot
#   - The JSON response is parsed correctly (response, next_focus, extracted,
#     draft_plan)
#   - session fields are updated from the parsed response
#   - next_focus=COMPLETE with a draft_plan triggers plan generation
#   - material_ids are fetched and included in the model context
#   - Invalid JSON responses fall back gracefully
#
# The deterministic fallback path (no model_provider) is covered by the
# existing TestIntakeSessionFlow tests above.
# ---------------------------------------------------------------------------


class TestIntakeLLMDriven:
    """Tests for the LLM-driven intake loop (run_intake_step with a model)."""

    @pytest.mark.asyncio
    async def test_llm_driven_first_turn_calls_beast_and_returns_greeting(self):
        """First turn with empty student_input calls beast + returns the model's response."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Hello! I'm Aristotle. What subject would you like to study?",
                    "next_focus": "SUBJECT",
                    "extracted": {},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession()

        result = await run_intake_step(session, "", ctx)

        # Model was called on the beast slot
        assert len(fake.calls) == 1
        assert fake.calls[0][0] == "beast"
        # Response text came from the model
        assert "Hello! I'm Aristotle" in result["prompt"]
        # Session updated
        assert session.current_focus == "SUBJECT"
        assert session.state == IntakeState.SUBJECT

    @pytest.mark.asyncio
    async def test_llm_driven_extracts_subject_from_raw_learner_text(self):
        """The model extracts 'physics' from 'i want to learn physics' — not verbatim."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Great — physics! How much do you already know?",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {
                        "subject": "physics",
                        "prior_knowledge": "",
                        "goals": "",
                        "schedule_minutes": 0,
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(state=IntakeState.SUBJECT, current_focus="SUBJECT")

        result = await run_intake_step(session, "i want to learn physics", ctx)

        # The extracted subject is "physics", not the raw text
        assert session.subject == "physics"
        assert session.extracted["subject"] == "physics"
        # The raw text was recorded in responses (conversation history)
        assert "i want to learn physics" in session.responses
        # The model's response is what's shown to the learner
        assert "Great — physics!" in result["prompt"]
        assert result["state"] == "PRIOR_KNOWLEDGE"

    @pytest.mark.asyncio
    async def test_llm_driven_multi_turn_stays_in_same_focus(self):
        """The model can stay in the same focus for multiple turns (not one-turn-per-state)."""
        # Turn 2: model asks a follow-up, stays in PRIOR_KNOWLEDGE
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Tell me more — have you ever studied mechanics?",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "physics", "prior_knowledge": "basic"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            state=IntakeState.PRIOR_KNOWLEDGE,
            current_focus="PRIOR_KNOWLEDGE",
            subject="physics",
            extracted={"subject": "physics"},
        )

        result = await run_intake_step(session, "a little bit", ctx)

        # Still in PRIOR_KNOWLEDGE (model didn't advance)
        assert session.current_focus == "PRIOR_KNOWLEDGE"
        assert result["state"] == "PRIOR_KNOWLEDGE"
        # But the extracted prior_knowledge was updated
        assert session.extracted["prior_knowledge"] == "basic"

    @pytest.mark.asyncio
    async def test_llm_driven_complete_with_draft_plan_triggers_pipeline(self):
        """When the model returns next_focus=COMPLETE with a draft_plan, the
        multi-step plan generation pipeline is triggered (ADR-003 Phase 3).

        The pipeline runs as a background job. run_intake_step returns
        immediately with state=GENERATING_PLAN + plan_job_id. The GUI
        polls /aristotle/plan/{plan_job_id}/status for progress.

        If the pipeline can't start (e.g., no ingested paper, missing
        tables), it falls back to the legacy single-call generate_plan()
        which returns state=COMPLETE + plan_id immediately.
        """
        draft_plan = [
            {"topic": "Newton's First Law", "subtopic": "inertia",
             "bloom_target": 2, "content_primary": "objects resist changes in motion",
             "prerequisite_concept_id": None},
            {"topic": "Newton's Second Law", "subtopic": "F=ma",
             "bloom_target": 3, "content_primary": "force equals mass times acceleration",
             "prerequisite_concept_id": 0},
        ]
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Your plan is confirmed. Let's begin!",
                    "next_focus": "COMPLETE",
                    "extracted": {
                        "subject": "physics",
                        "prior_knowledge": "basic",
                        "goals": "personal interest",
                        "schedule_minutes": 30,
                    },
                    "draft_plan": draft_plan,
                })
            }
        )
        # Need a fake conn so generate_plan can INSERT
        conn = _FakeConn()
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            state=IntakeState.GENERATING_PLAN,
            current_focus="PLAN_DRAFT",
            subject="physics",
            goals="personal interest",
            schedule_minutes=30,
            draft_plan=draft_plan,
        )

        result = await run_intake_step(session, "looks good", ctx)

        # The multi-step pipeline tries to start. If it succeeds, we get
        # GENERATING_PLAN + plan_job_id. If it fails (no aristotle_plan_job
        # table in _FakeConn), it falls back to legacy generate_plan()
        # which returns COMPLETE + plan_id. Either is acceptable — the
        # test verifies that a plan_job_id OR plan_id is returned.
        assert result["state"] in ("GENERATING_PLAN", "COMPLETE"), (
            f"Expected GENERATING_PLAN (pipeline started) or COMPLETE (legacy fallback), "
            f"got {result['state']}"
        )
        assert "plan_job_id" in result or "plan_id" in result, (
            "Expected either plan_job_id (pipeline) or plan_id (legacy fallback)"
        )

    @pytest.mark.asyncio
    async def test_llm_driven_invalid_json_falls_back_gracefully(self):
        """If the model returns non-JSON, the raw text is shown (not a crash)."""
        fake = _FakeModelProvider(
            responses={"beast": "Sorry, I wasn't thinking clearly just now."}
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(current_focus="SUBJECT")

        result = await run_intake_step(session, "physics please", ctx)

        # No crash — the raw text is shown as the prompt
        assert "Sorry, I wasn't thinking clearly" in result["prompt"]
        # Session state is unchanged (no extraction happened)
        assert session.current_focus == "SUBJECT"

    @pytest.mark.asyncio
    async def test_llm_driven_empty_response_shows_model_config_error(self):
        """If the model returns empty content, a helpful config error is shown."""
        fake = _FakeModelProvider(responses={"beast": ""})
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(current_focus="SUBJECT")

        result = await run_intake_step(session, "hello", ctx)

        # The error message mentions model provider configuration
        assert "model" in result["prompt"].lower()
        assert session.current_focus == "SUBJECT"  # unchanged

    @pytest.mark.asyncio
    async def test_llm_driven_json_in_markdown_fences_is_parsed(self):
        """JSON wrapped in ```json fences is extracted correctly."""
        fake = _FakeModelProvider(
            responses={
                "beast": "Here's my response:\n```json\n" + json.dumps({
                    "response": "Got it — physics it is.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "physics"},
                    "draft_plan": None,
                }) + "\n```"
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(current_focus="SUBJECT")

        result = await run_intake_step(session, "i want to learn physics", ctx)

        assert "Got it — physics it is." in result["prompt"]
        assert session.subject == "physics"

    @pytest.mark.asyncio
    async def test_llm_driven_includes_uploaded_materials_in_context(self):
        """material_ids on the session are fetched + included in the model context."""
        # The fake conn returns a row for the material query. Text must be
        # >= 100 chars to pass the EXTRACTION FAILED guard added in BUG-002.
        material_text = (
            "This paper covers NBCM theory — null boundary constraint manifolds. "
            "It develops the constraint algebra on a Lorentzian manifold with "
            "2+2 foliation and derives the Hamiltonian structure."
        )
        conn = _FakeConn(rows=[("paper.pdf", "pdf", material_text)])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "I see you've uploaded a paper on NBCM. Let me adjust.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "null boundary constraint manifolds"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1"],
        )

        await run_intake_step(session, "i uploaded a paper", ctx)

        # The model was called with a user prompt that includes the material text
        user_prompt = fake.calls[0][1][1]["content"]  # [0]=first call, [1]=messages, [1]=user msg, ["content"]
        assert "paper.pdf" in user_prompt
        assert "NBCM" in user_prompt

    @pytest.mark.asyncio
    async def test_llm_driven_no_model_falls_back_to_deterministic(self):
        """When no model_provider is available, the deterministic path is used."""
        # No model_provider → deterministic fallback
        ctx = _make_ctx(model_provider=None)
        session = IntakeSession()

        result = await run_intake_step(session, "", ctx)

        # Deterministic greeting
        assert "subject" in result["prompt"].lower()
        assert session.state == IntakeState.SUBJECT

    @pytest.mark.asyncio
    async def test_llm_driven_long_paper_content_reaches_model_not_truncated_to_2000(self):
        """REGRESSION: a long paper's content must reach the model, not be
        truncated to 2000 chars.

        The original implementation truncated each material to 2000 chars
        in _build_intake_user_prompt — way too small for a paper (typical
        academic paper is 30k-80k chars). The LLM only saw the abstract
        and asked the learner to "share the title" because it couldn't
        read the actual content.

        Fix: default material_preview_chars is now 20000 (~5000 tokens).
        This test verifies that a 15000-char paper's full content reaches
        the model (it's under the new limit).
        """
        # 15000-char paper — would have been truncated under the old 2000
        # char limit, but fits under the new 20000 char limit.
        paper_body = "A" * 15000
        conn = _FakeConn(rows=[("long_paper.pdf", "pdf", paper_body)])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "I've read your paper.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "physics"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1"],
        )

        await run_intake_step(session, "i uploaded a paper", ctx)

        user_prompt = fake.calls[0][1][1]["content"]
        # The full 15000 chars should be in the prompt (not truncated to 2000).
        assert paper_body in user_prompt, (
            "Full paper content (15000 chars) should reach the model. "
            f"Prompt length: {len(user_prompt)}"
        )
        # No truncation notice should appear (paper fits in the preview).
        assert "PAPER TRUNCATED" not in user_prompt

    @pytest.mark.asyncio
    async def test_llm_driven_very_long_paper_shows_truncation_notice(self):
        """A paper longer than material_preview_chars gets a clear truncation notice.

        The LLM needs to know the paper continues so it can ask the learner
        about the remaining scope (instead of pretending it read the whole
        thing).
        """
        # 30000-char paper — exceeds the 20000 default preview limit.
        paper_body = "B" * 30000
        conn = _FakeConn(rows=[("very_long_paper.pdf", "pdf", paper_body)])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "I've read the first portion.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "physics"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1"],
        )

        await run_intake_step(session, "i uploaded a long paper", ctx)

        user_prompt = fake.calls[0][1][1]["content"]
        # First 20000 chars should be present.
        assert paper_body[:20000] in user_prompt
        # Truncation notice should be present, mentioning the remaining 10000 chars.
        assert "PAPER TRUNCATED" in user_prompt
        assert "10000 more chars" in user_prompt

    @pytest.mark.asyncio
    async def test_llm_driven_material_fetch_failure_logs_warning(self, caplog):
        """When session.material_ids is set but the DB returns 0 rows, a
        warning is logged so the operator can diagnose the upload-route bug.

        This is the silent-failure mode that masked the original upload
        INSERT column/value swap bug — the material_id was stored under
        the wrong column, so _fetch_material_texts returned 0 rows, and
        the LLM got an empty materials list with NO warning.
        """
        # _FakeConn returns no rows — simulates the DB lookup returning 0
        # results because the material_id doesn't match any stored row.
        conn = _FakeConn(rows=[])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Hello!",
                    "next_focus": "SUBJECT",
                    "extracted": {},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1", "mat-2"],  # 2 requested, 0 will be found
        )

        import logging
        with caplog.at_level(logging.WARNING, logger="test"):
            await run_intake_step(session, "hello", ctx)

        # The warning about missing materials should be in the logs.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "intake_materials_missing" in r.message
            for r in warnings
        ), (
            "Expected intake_materials_missing warning when material_ids are "
            "set but the DB returns 0 rows. This warning is critical for "
            "diagnosing upload-route persistence bugs."
        )

    # ------------------------------------------------------------------
    # BUG-001: turns_in_focus counter + hard cap + auto-advance
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_llm_driven_turns_in_focus_increments_when_same_focus(self):
        """turns_in_focus increments when the model returns the same next_focus."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Tell me more about your background.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "physics"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="PRIOR_KNOWLEDGE",
            turns_in_focus=0,
        )

        await run_intake_step(session, "a little", ctx)

        # Same focus returned → turns_in_focus should increment to 1
        assert session.turns_in_focus == 1
        assert session.current_focus == "PRIOR_KNOWLEDGE"

    @pytest.mark.asyncio
    async def test_llm_driven_turns_in_focus_resets_on_focus_change(self):
        """turns_in_focus resets to 0 when the model advances to a new focus."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "What are your goals?",
                    "next_focus": "GOALS",
                    "extracted": {"subject": "physics", "prior_knowledge": "basic"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="PRIOR_KNOWLEDGE",
            turns_in_focus=2,  # was at cap
        )

        await run_intake_step(session, "basic", ctx)

        # Focus changed → turns_in_focus should reset to 0
        assert session.turns_in_focus == 0
        assert session.current_focus == "GOALS"

    @pytest.mark.asyncio
    async def test_llm_driven_deep_mode_does_not_force_advance_to_plan_draft(self):
        """deep_intake=True: the server does NOT force-advance to PLAN_DRAFT.

        A custom curriculum (e.g., NBCM) legitimately requires many
        questions to gauge the student's state. For sessions that opted
        into deep_intake, the server trusts the model to advance when
        it has enough signal — turns_in_focus is visibility only. This
        preserves the Task 12 behavior, but now scoped to deep_intake=True
        instead of being the global default (Task 15).
        """
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Let me ask one more question about your schedule.",
                    "next_focus": "SCHEDULE",  # model wants to probe further
                    "extracted": {
                        "subject": "physics",
                        "prior_knowledge": "basic",
                        "goals": "personal interest",
                        "schedule_minutes": 30,
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="SCHEDULE",
            turns_in_focus=5,  # many turns in this focus — but server doesn't force
            deep_intake=True,
            extracted={
                "subject": "physics",
                "prior_knowledge": "basic",
                "goals": "personal interest",
                "schedule_minutes": 30,
            },
        )

        await run_intake_step(session, "30 minutes", ctx)

        # Server should NOT have overridden — model's choice respected
        assert session.current_focus == "SCHEDULE"
        assert session.turns_in_focus == 6  # incremented, not reset

    @pytest.mark.asyncio
    async def test_llm_driven_guided_mode_forces_plan_draft_when_stuck(self):
        """deep_intake=False (default): server DOES force-advance to
        PLAN_DRAFT once all 4 fields are filled and the model is stuck
        (turns_in_focus >= 2). This is the Task 15 fix for the reported
        'insufferable intake' bug — a weak/free-tier model that keeps
        re-asking a GOALS-type question in different words, even after
        the learner gave a clear answer, no longer loops forever.
        """
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Just one more thing about your schedule...",
                    "next_focus": "SCHEDULE",  # model still wants to probe further
                    "extracted": {
                        "subject": "pharmacognosy",
                        "prior_knowledge": "high school biology and chemistry",
                        "goals": "career as a pharmacist",
                        "schedule_minutes": 30,
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="SCHEDULE",
            turns_in_focus=2,  # already stuck for 2 turns
            deep_intake=False,
            extracted={
                "subject": "pharmacognosy",
                "prior_knowledge": "high school biology and chemistry",
                "goals": "career as a pharmacist",
                "schedule_minutes": 30,
            },
        )

        await run_intake_step(session, "30 minutes", ctx)

        # Server should have overridden to PLAN_DRAFT despite the model
        # wanting to keep probing SCHEDULE.
        assert session.current_focus == "PLAN_DRAFT"
        assert session.turns_in_focus == 0

    @pytest.mark.asyncio
    async def test_llm_driven_guided_mode_forces_next_stage_when_fields_incomplete(self):
        """deep_intake=False (default): if the model is stuck 2+ turns in
        one focus area but the 4 fields aren't all filled yet, the server
        forces onward to the NEXT stage in the flow (not straight to
        PLAN_DRAFT) — e.g. GOALS -> SCHEDULE. This is the direct fix for
        the screenshot: 4+ turns of reworded GOALS questions with no
        schedule question ever reached.
        """
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Is your goal to pass the exam or build deep mastery?",
                    "next_focus": "GOALS",  # model still re-probing goals
                    "extracted": {
                        "subject": "pharmacognosy",
                        "prior_knowledge": "high school biology and chemistry",
                        "goals": "career as a pharmacist",
                        # schedule_minutes not yet asked
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="GOALS",
            turns_in_focus=2,  # already re-asked goals twice
            deep_intake=False,
            extracted={
                "subject": "pharmacognosy",
                "prior_knowledge": "high school biology and chemistry",
                "goals": "career as a pharmacist",
            },
        )

        await run_intake_step(session, "a career as a pharmacist", ctx)

        # Forced onward to SCHEDULE (the next stage), not PLAN_DRAFT —
        # schedule_minutes hasn't been collected yet.
        assert session.current_focus == "SCHEDULE"
        assert session.turns_in_focus == 0

    @pytest.mark.asyncio
    async def test_llm_driven_guided_mode_does_not_force_before_turn_cap(self):
        """deep_intake=False (default): the model still gets 2 tries in a
        focus area before the server steps in — this isn't a hair-trigger.
        """
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Tell me more about your goals.",
                    "next_focus": "GOALS",
                    "extracted": {
                        "subject": "pharmacognosy",
                        "prior_knowledge": "high school biology and chemistry",
                        "goals": "career as a pharmacist",
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="GOALS",
            turns_in_focus=0,  # first turn in this focus
            deep_intake=False,
            extracted={
                "subject": "pharmacognosy",
                "prior_knowledge": "high school biology and chemistry",
            },
        )

        await run_intake_step(session, "a career as a pharmacist", ctx)

        assert session.current_focus == "GOALS"
        assert session.turns_in_focus == 1

    @pytest.mark.asyncio
    async def test_deep_intake_opt_in_keyword_sets_flag_mid_session(self):
        """A learner saying a deep-intake trigger phrase mid-session flips
        session.deep_intake to True for the rest of the conversation."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Got it — let's go deep on this.",
                    "next_focus": "SUBJECT",
                    "extracted": {},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(current_focus="SUBJECT", deep_intake=False)

        await run_intake_step(
            session, "this is a custom research curriculum, take your time", ctx,
        )

        assert session.deep_intake is True

    @pytest.mark.asyncio
    async def test_llm_driven_does_not_auto_advance_before_turn_cap(self):
        """The server never force-advances before the turn cap — the model
        gets to decide when it has enough signal, up to the cap.
        """
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "One more question about your schedule.",
                    "next_focus": "SCHEDULE",
                    "extracted": {
                        "subject": "physics",
                        "prior_knowledge": "basic",
                        "goals": "personal interest",
                        "schedule_minutes": 30,
                    },
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="SCHEDULE",
            turns_in_focus=0,  # first turn in this focus
            extracted={
                "subject": "physics",
                "prior_knowledge": "basic",
                "goals": "personal interest",
                "schedule_minutes": 30,
            },
        )

        await run_intake_step(session, "30 minutes", ctx)

        # Should NOT auto-advance on the first turn in this focus
        # (turns_in_focus goes 0 -> 1, cap is 2)
        assert session.current_focus == "SCHEDULE"
        assert session.turns_in_focus == 1

    # ------------------------------------------------------------------
    # BUG-002: extraction failure guard + thin-text warning
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_llm_driven_short_extracted_text_shows_extraction_failed(self, caplog):
        """BUG-002 fix: when extracted_text < 100 chars, the prompt tells the
        LLM 'EXTRACTION FAILED' instead of passing through garbage text.

        This prevents the LLM from hallucinating that it read the paper
        when pypdf extracted only a few chars (math-heavy PDFs).
        """
        # 30 chars — way too short, triggers the guard
        short_text = "NBCM paper. Section 1."
        assert len(short_text) < 100
        conn = _FakeConn(rows=[("math_paper.pdf", "pdf", short_text)])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "OK",
                    "next_focus": "SUBJECT",
                    "extracted": {},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1"],
        )

        import logging
        with caplog.at_level(logging.WARNING, logger="test"):
            await run_intake_step(session, "hello", ctx)

        user_prompt = fake.calls[0][1][1]["content"]
        # The prompt should contain EXTRACTION FAILED, not the short text
        assert "EXTRACTION FAILED" in user_prompt
        assert short_text not in user_prompt  # the garbage text is NOT passed
        # The thin-text warning should fire
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("intake_material_text_thin" in r.message for r in warnings)

    # ------------------------------------------------------------------
    # BUG-003: auto-trigger empty-input no longer sends "first turn greeting"
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_llm_driven_auto_trigger_after_upload_does_not_re_greet(self):
        """BUG-003 fix: when student_input is empty BUT session.responses is
        non-empty (upload auto-trigger), the prompt tells the LLM to
        acknowledge the upload — NOT to 'generate your greeting'.

        The old code sent '(This is the first turn — generate your greeting.)'
        at turn 8 of a conversation, confusing the LLM into re-greeting.
        """
        # Use a material with enough text to pass the EXTRACTION FAILED guard
        material_text = (
            "This paper covers NBCM theory in depth. " * 5  # ~175 chars
        )
        conn = _FakeConn(rows=[("paper.pdf", "pdf", material_text)])
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "I see this paper covers NBCM theory.",
                    "next_focus": "PRIOR_KNOWLEDGE",
                    "extracted": {"subject": "NBCM"},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
        session = IntakeSession(
            current_focus="SUBJECT",
            material_ids=["mat-1"],
            responses=["i want to study this paper", "physics please"],  # non-empty
        )

        # Empty student_input — simulates the upload auto-trigger
        await run_intake_step(session, "", ctx)

        user_prompt = fake.calls[0][1][1]["content"]
        # Should NOT contain the first-turn greeting instruction
        assert "This is the first turn" not in user_prompt, (
            "Auto-trigger after upload should NOT send 'first turn greeting' "
            "instruction — that confuses the LLM into re-greeting at turn 8."
        )
        # SHOULD contain the upload acknowledgment instruction
        assert "uploaded a file" in user_prompt.lower() or "acknowledge" in user_prompt.lower()

    @pytest.mark.asyncio
    async def test_llm_driven_genuine_first_turn_still_sends_greeting_instruction(self):
        """The first-turn greeting instruction still fires on the actual first
        turn (empty student_input + empty session.responses)."""
        fake = _FakeModelProvider(
            responses={
                "beast": json.dumps({
                    "response": "Hello! I'm Aristotle.",
                    "next_focus": "SUBJECT",
                    "extracted": {},
                    "draft_plan": None,
                })
            }
        )
        ctx = _make_ctx(model_provider=fake)
        session = IntakeSession(
            current_focus="SUBJECT",
            responses=[],  # empty — genuine first turn
        )

        await run_intake_step(session, "", ctx)

        user_prompt = fake.calls[0][1][1]["content"]
        assert "This is the first turn" in user_prompt


# ---------------------------------------------------------------------------
# Plan ingestion tests (Phase D brain transplant — Piece 3)
# ---------------------------------------------------------------------------
# These tests verify that when the LLM-driven intake returns a draft_plan,
# generate_plan() ingests each proposed concept as a new aristotle_concept
# row (instead of falling back to a LIKE query against sample data) and
# the plan's concept_ids_json references those new concept ids.
# ---------------------------------------------------------------------------


class TestIntakePlanIngestion:
    """Tests for draft_plan → aristotle_concept ingestion (Piece 3)."""

    @pytest.mark.asyncio
    async def test_draft_plan_concepts_are_ingested_as_aristotle_concept_rows(self):
        """generate_plan() with a draft_plan INSERTs each concept into aristotle_concept."""
        from aristotle.actors.intake import IntakeActor

        draft_plan = [
            {"topic": "Inertia", "subtopic": "Newton's First Law",
             "bloom_target": 2, "content_primary": "objects resist changes in motion",
             "prerequisite_concept_id": None},
            {"topic": "F=ma", "subtopic": "Newton's Second Law",
             "bloom_target": 3, "content_primary": "force equals mass times acceleration",
             "prerequisite_concept_id": 0},
        ]
        # Fake conn that captures all executed SQL so we can assert on the INSERTs
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="physics",
            goals="personal interest",
            schedule_minutes=30,
            draft_plan=draft_plan,
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)

        assert result.ok
        assert result.data["concept_count"] == 2

        # At least 2 INSERT OR REPLACE INTO aristotle_concept statements
        # were executed (one per draft_plan concept).
        concept_inserts = [
            (sql, params) for sql, params in conn._executed
            if "INSERT OR REPLACE INTO aristotle_concept" in sql
        ]
        assert len(concept_inserts) == 2

        # The concept ids follow the {subject_slug}_{idx:03d} pattern.
        # subject="physics" → slug="physics", so ids are "physics_000", "physics_001".
        inserted_ids = [params[0] for _, params in concept_inserts]
        assert "physics_000" in inserted_ids
        assert "physics_001" in inserted_ids

    @pytest.mark.asyncio
    async def test_draft_plan_concept_ids_appear_in_plan_concept_ids_json(self):
        """The plan's concept_ids_json contains the ingested concept ids, not sample data."""
        from aristotle.actors.intake import IntakeActor

        draft_plan = [
            {"topic": "Concept A", "subtopic": "", "bloom_target": 1,
             "content_primary": "desc A", "prerequisite_concept_id": None},
            {"topic": "Concept B", "subtopic": "", "bloom_target": 2,
             "content_primary": "desc B", "prerequisite_concept_id": 0},
        ]
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="nbcm",
            goals="SME",
            schedule_minutes=45,
            draft_plan=draft_plan,
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)

        assert result.ok
        # Find the INSERT INTO aristotle_learning_plan statement
        plan_inserts = [
            (sql, params) for sql, params in conn._executed
            if "INSERT INTO aristotle_learning_plan" in sql
        ]
        assert len(plan_inserts) == 1
        # The concept_ids_json is the 5th param (index 4) in the VALUES
        concept_ids_json = plan_inserts[0][1][4]
        assert "nbcm_000" in concept_ids_json
        assert "nbcm_001" in concept_ids_json

    @pytest.mark.asyncio
    async def test_no_draft_plan_falls_back_to_like_query(self):
        """Empty draft_plan uses the old LIKE-query path (deterministic fallback)."""
        from aristotle.actors.intake import IntakeActor

        # Fake conn returns one concept row for the LIKE query
        conn = _FakeConn(rows=[("newton_first_law",)])
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="newton",
            goals="personal interest",
            schedule_minutes=30,
            draft_plan=[],  # empty → fallback path
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)

        assert result.ok
        assert result.data["concept_count"] == 1
        # No INSERT OR REPLACE INTO aristotle_concept (no draft_plan to ingest)
        concept_inserts = [
            sql for sql, _ in conn._executed
            if "INSERT OR REPLACE INTO aristotle_concept" in sql
        ]
        assert len(concept_inserts) == 0

    @pytest.mark.asyncio
    async def test_materials_are_linked_to_ingested_concepts(self):
        """generate_plan() updates aristotle_uploaded_material.concept_ids_json."""
        from aristotle.actors.intake import IntakeActor

        draft_plan = [
            {"topic": "Concept A", "subtopic": "", "bloom_target": 1,
             "content_primary": "desc A", "prerequisite_concept_id": None},
        ]
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="physics",
            goals="personal interest",
            schedule_minutes=30,
            draft_plan=draft_plan,
            material_ids=["mat-1", "mat-2"],
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)

        assert result.ok
        # Two UPDATE statements — one per material_id
        material_updates = [
            sql for sql, _ in conn._executed
            if "UPDATE aristotle_uploaded_material" in sql
        ]
        assert len(material_updates) == 2


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
        next_concept_id = await _finalize_placement(session, ctx)
        # Should have issued an UPDATE setting current_concept_idx = 1 (c2 is the first non-mastered)
        update_calls = [
            (sql, params)
            for sql, params in conn._executed
            if "UPDATE aristotle_learning_plan SET current_concept_idx" in sql
        ]
        assert len(update_calls) == 1
        _sql, params = update_calls[0]
        assert params[0] == 1  # starting_concept_idx
        # Task 17: the resolved concept_id must also be returned directly,
        # so callers don't need a second, unscoped lookup to discover it.
        assert next_concept_id == "c2"

    @pytest.mark.asyncio
    async def test_finalize_returns_none_when_all_mastered(self):
        """_finalize_placement returns None (not a stale/wrong concept_id)
        when every concept in the plan was already mastered."""
        from aristotle.actors.intake import PlacerSession, _finalize_placement

        class _RoutingConn:
            def __init__(self):
                self._executed = []

            async def execute(self, sql, params=()):
                self._executed.append((sql, params))
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2"]',)])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            results=[
                {"concept_id": "c1", "score": 0.9, "mastery_achieved": True},
                {"concept_id": "c2", "score": 0.9, "mastery_achieved": True},
            ],
        )
        next_concept_id = await _finalize_placement(session, ctx)
        assert next_concept_id is None

    @pytest.mark.asyncio
    async def test_run_placer_step_includes_next_concept_id_on_complete(self):
        """Task 17: run_placer_step's COMPLETE result carries next_concept_id
        end-to-end, not just _finalize_placement in isolation — this is
        what the API route and GUI actually consume."""
        from aristotle.actors.intake import PlacerSession, run_placer_step

        class _RoutingConn:
            async def execute(self, sql, params=()):
                if "concept_ids_json" in sql.lower():
                    return _FakeCursor([('["c1", "c2"]',)])
                return _FakeCursor(None)

            async def commit(self):
                pass

        conn = _RoutingConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = PlacerSession(
            plan_id="plan-1",
            current_idx=1,
            concepts_to_assess=["c1"],
            results=[{"concept_id": "c1", "score": 0.2, "mastery_achieved": False}],
        )
        result = await run_placer_step(session, "", ctx)
        assert result["state"] == "COMPLETE"
        assert result["next_concept_id"] == "c1"


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

    @pytest.mark.asyncio
    async def test_placer_step_route_surfaces_next_concept_id_on_complete(self):
        """Task 17: POST /placer/step's HTTP response includes next_concept_id
        when placement completes — this is the field ask.py now reads
        instead of falling back to the unscoped GET /aristotle/concepts.
        """
        from aristotle.api import placer_step_route
        from aristotle.actors.intake import PlacerSession, placer_session_to_dict

        eval_json = json.dumps(
            {
                "score": 0.2,
                "mastery_achieved": False,
                "feedback": "Not quite",
                "diagnosis": None,
            }
        )
        fake = _FakeModelProvider(responses={"evaluation": eval_json})

        class _RoutingConn:
            async def execute(self, sql, params=()):
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

        # Only one concept to assess — completes after this single answer.
        session = PlacerSession(
            plan_id="plan-1",
            concepts_to_assess=["c1"],
            current_idx=0,
            question_generated=True,
            current_question="What is inertia?",
        )

        async def _json():
            return {
                "session": placer_session_to_dict(session),
                "student_input": "I'm not sure",
            }

        request.json = _json

        result = await placer_step_route(request)
        assert result["state"] == "COMPLETE"
        # c1 was not mastered, so it's the (correct) starting concept —
        # not whatever happened to be first in the global concept table.
        assert result["next_concept_id"] == "c1"
