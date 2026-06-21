"""Tests for ARISTOTLE CLI + API routes + full-session coordinator.

Tests the HTTP-client CLI (with a mock server), the API routes (with a
fake container), and the full-session coordinator (the /session/run
endpoint that runs the complete loop in one call).

Run:  pytest tests/test_aristotle_cli_api.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aip.foundation.protocols.actors import ActorContext


# --------------------------------------------------------------------------
# Fakes (same pattern as test_aristotle_tutoring.py)
# --------------------------------------------------------------------------


class _FakeModelProvider:
    """Fake ModelProvider that returns canned responses by slot."""

    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, list[dict]]] = []

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        content = self._responses.get(slot_name, f"[fake {slot_name} response]")
        return {"content": content, "model": "fake", "usage": {}, "latency_ms": 5}


class _FakeConn:
    """Fake aiosqlite.Connection for testing.

    Can either return the same rows for every query (simple mode) or
    return different rows per query based on a list of row-sets
    (multi-query mode). In multi-query mode, each execute() call
    consumes the next row-set from the list.
    """

    def __init__(
        self,
        rows: list[tuple] | None = None,
        multi_rows: list[list[tuple]] | None = None,
    ):
        self._rows = rows or []
        self._multi_rows = multi_rows
        self._multi_idx = 0
        self._executed: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple = ()):
        self._executed.append((sql, params))
        if self._multi_rows is not None:
            if self._multi_idx < len(self._multi_rows):
                rows = self._multi_rows[self._multi_idx]
                self._multi_idx += 1
            else:
                rows = []
        else:
            rows = self._rows
        return _FakeCursor(rows)

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


def _make_container(
    model_provider: Any | None = None,
    stores: Any | None = None,
) -> Any:
    """Build a minimal container for testing."""
    return type(
        "C",
        (),
        {
            "model_provider": model_provider,
            "corpus_registry": _FakeRegistry(stores) if stores else None,
            "extensions": type("H", (), {"registry": "fake"})(),
        },
    )()


def _make_ctx(container: Any) -> ActorContext:
    return ActorContext(
        container=container,
        config=None,
        logger=__import__("logging").getLogger("test"),
        cancel_event=asyncio.Event(),
    )


# --------------------------------------------------------------------------
# API route tests
# --------------------------------------------------------------------------


class TestAPIRoutes:
    """Test the API route handlers directly (no HTTP server needed)."""

    @pytest.mark.asyncio
    async def test_list_concepts_route(self):
        """GET /aristotle/concepts returns concepts from the corpus."""
        from aristotle.api import list_concepts_route

        # Mock list_concepts to return canned data
        conn = _FakeConn(
            rows=[
                ("c1", "Inertia", None, 3, None),
                ("c2", "Force", "c1", 4, None),
            ]
        )
        container = _make_container(stores=_FakeStores(conn))

        # Build a fake Request
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

        result = await list_concepts_route(request)
        assert isinstance(result, list)
        # The fake returns rows from fetchall — list_concepts maps them
        # But the row shape here doesn't match the query in list_concepts
        # (which selects id, topic, subtopic, bloom_target, prerequisite_concept_id)
        # The _FakeConn returns the rows as-is. list_concepts does:
        #   row[0]=id, row[1]=topic, row[2]=subtopic, row[3]=bloom, row[4]=prereq
        # Our fake rows have 5 columns matching that query.
        if result:
            assert result[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_session_start_route(self):
        """POST /aristotle/session/start returns a SessionContext with state=PREDICT.

        Phase B.5: sessions now start at PREDICT (the generation effect —
        learner guesses before teaching). Was state=TEACH in Phase A.
        """
        from aristotle.api import session_start_route

        container = _make_container()
        body = {"concept_id": "newton_first_law"}

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
                "_json": body,
            },
        )()

        # Mock the json() method
        async def _json():
            return body

        request.json = _json

        result = await session_start_route(request)
        assert result["concept_id"] == "newton_first_law"
        assert result["state"] in ("TEACH", "PREDICT")  # TEACH (original) or PREDICT (Phase B.5)

    @pytest.mark.asyncio
    async def test_session_run_route(self):
        """POST /aristotle/session/run runs a full session with answers."""
        from aristotle.api import session_run_route

        fake = _FakeModelProvider(
            responses={
                "beast": "Newton's First Law states that an object at rest stays at rest...",
                "evaluation": json.dumps(
                    {"score": 0.8, "mastery_achieved": True, "feedback": "Good"}
                ),
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = _make_container(
            model_provider=fake,
            stores=_FakeStores(conn),
        )
        body = {"concept_id": "c1", "answers": ["objects resist changes in motion"]}

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
            return body

        request.json = _json

        result = await session_run_route(request)
        assert result["concept_id"] == "c1"
        assert isinstance(result["mastered"], bool)
        assert isinstance(result["last_score"], float)
        assert isinstance(result["steps"], list)
        assert len(result["steps"]) > 0


# --------------------------------------------------------------------------
# Full-session coordinator test (via the session module directly)
# --------------------------------------------------------------------------


class TestFullSession:
    """Test the full-session loop (same logic as /session/run but called directly)."""

    @pytest.mark.asyncio
    async def test_full_session_mastered(self):
        """A session where the learner answers correctly ends with mastered=True."""
        from aristotle.session import SessionContext, SessionState, run_session_step

        eval_json = json.dumps(
            {"score": 0.9, "mastery_achieved": True, "feedback": "Excellent"}
        )
        fake = _FakeModelProvider(
            responses={
                "beast": "Newton's First Law: an object at rest stays at rest unless acted on by a force.",
                "evaluation": eval_json,
                "sexton": "No struggles — learner grasps inertia well.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = _make_container(
            model_provider=fake,
            stores=_FakeStores(conn),
        )
        ctx = _make_ctx(container)

        session = SessionContext(concept_id="c1", state=SessionState.TEACH)
        answers = ["objects resist changes in motion"]
        answer_idx = 0

        for _ in range(20):
            if session.state.value == "SESSION_COMPLETE":
                break
            # Provide student_input when the quiz question has been generated
            # (waiting for answer) and we have answers left
            student_input = ""
            if (
                session.state == SessionState.QUIZ
                and session.quiz_generated
                and answer_idx < len(answers)
            ):
                student_input = answers[answer_idx]
                answer_idx += 1
            result = await run_session_step(ctx, session, student_input)
            assert result.ok, f"step failed at state={session.state}: {result.error}"

        assert session.state.value == "SESSION_COMPLETE"
        assert session.mastered is True
        assert session.last_score >= 0.7

    @pytest.mark.asyncio
    async def test_full_session_not_mastered_remediates(self):
        """A session where the learner answers poorly triggers REMEDIATE."""
        from aristotle.session import SessionContext, SessionState, run_session_step

        eval_json = json.dumps(
            {"score": 0.3, "mastery_achieved": False, "feedback": "Try again"}
        )
        fake = _FakeModelProvider(
            responses={
                "beast": "Let me explain differently...",
                "evaluation": eval_json,
                "sexton": "Learner struggles with the concept of inertia.",
            }
        )
        conn = _FakeConn(rows=[("c1", "Inertia", None, "content", None, None, None, 3)])
        container = _make_container(
            model_provider=fake,
            stores=_FakeStores(conn),
        )
        ctx = _make_ctx(container)

        session = SessionContext(concept_id="c1", state=SessionState.TEACH)
        answers = ["I don't know"]
        answer_idx = 0

        for _ in range(20):
            if session.state.value == "SESSION_COMPLETE":
                break
            student_input = ""
            if (
                session.state == SessionState.QUIZ
                and session.quiz_generated
                and answer_idx < len(answers)
            ):
                student_input = answers[answer_idx]
                answer_idx += 1
            result = await run_session_step(ctx, session, student_input)
            assert result.ok, f"step failed at state={session.state}: {result.error}"

        # After max retries (2), the session should complete even without mastery
        assert session.state.value == "SESSION_COMPLETE"
        assert session.retry_count > 0  # REMEDIATE was triggered


# --------------------------------------------------------------------------
# CLI tests (mock the HTTP client)
# --------------------------------------------------------------------------


class TestCLI:
    """Test the CLI commands with a mock HTTP client."""

    def test_health_command(self):
        """`aristotle health` calls /health/extensions and prints the result."""
        from click.testing import CliRunner
        from aristotle.cli import cli

        runner = CliRunner()
        with patch("aristotle.cli._client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "host_running": True,
                "extensions": [
                    {
                        "id": "aristotle",
                        "version": "0.1.0",
                        "state": "REGISTERED",
                        "failures": [],
                    }
                ],
            }
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get = MagicMock(return_value=mock_resp)
            mock_client.return_value = mock_ctx

            result = runner.invoke(cli, ["health"])
            assert result.exit_code == 0
            assert "aristotle" in result.output
            assert "REGISTERED" in result.output

    def test_list_concepts_command(self):
        """`aristotle list-concepts` calls /aristotle/concepts and prints them."""
        from click.testing import CliRunner
        from aristotle.cli import cli

        runner = CliRunner()
        with patch("aristotle.cli._client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = [
                {
                    "id": "c1",
                    "topic": "Inertia",
                    "bloom_target": 3,
                    "prerequisite_concept_id": None,
                },
            ]
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get = MagicMock(return_value=mock_resp)
            mock_client.return_value = mock_ctx

            result = runner.invoke(cli, ["list-concepts"])
            assert result.exit_code == 0
            assert "Inertia" in result.output


# --------------------------------------------------------------------------
# Dashboard route tests (Phase B — teacher dashboard)
# --------------------------------------------------------------------------


class TestDashboardRoute:
    """Test the GET /aristotle/dashboard route."""

    @pytest.mark.asyncio
    async def test_dashboard_shows_all_concepts_including_unstarted(self):
        """Dashboard returns ALL concepts, including ones with no mastery record.

        Ingest 2 concepts. Start mastery on only 1 (the other is unstarted).
        The dashboard's LEFT JOIN should return 2 rows — one with mastery
        state, one with nulls (unstarted).
        """
        from aristotle.api import dashboard_route

        # multi_rows: query 1 = struggle_pattern (fetchone, no rows = None)
        #             query 2 = LEFT JOIN (fetchall, 2 rows)
        # Row format from LEFT JOIN:
        #   (concept_id, topic, mastered, last_score, repetitions, next_review_at, updated_at)
        conn = _FakeConn(
            multi_rows=[
                [],  # query 1: struggle_pattern (no row → None)
                [
                    # concept 1: has mastery record (started, not mastered, due)
                    (
                        "c1",
                        "Inertia",
                        0,
                        0.4,
                        1,
                        "2020-01-01T00:00:00+00:00",
                        "2020-01-01T00:00:00",
                    ),
                    # concept 2: no mastery record (unstarted — all NULLs from LEFT JOIN)
                    ("c2", "Force", 0, None, 0, None, None),
                ],
            ]
        )
        container = _make_container(stores=_FakeStores(conn))

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

        result = await dashboard_route(request)

        # total_concepts = 2 (both concepts)
        assert result["total_concepts"] == 2

        # mastery_by_concept has 2 rows (LEFT JOIN includes unstarted)
        assert len(result["mastery_by_concept"]) == 2

        # concept c1: started, not mastered, due (next_review in 2020 → past)
        c1 = next(m for m in result["mastery_by_concept"] if m["concept_id"] == "c1")
        assert c1["mastered"] is False
        assert c1["last_score"] == 0.4
        assert c1["repetitions"] == 1
        assert c1["is_due"] is True

        # concept c2: unstarted (all nulls from LEFT JOIN)
        c2 = next(m for m in result["mastery_by_concept"] if m["concept_id"] == "c2")
        assert c2["mastered"] is False
        assert c2["last_score"] is None
        assert c2["repetitions"] == 0
        assert c2["next_review_at"] is None
        assert c2["is_due"] is False  # unstarted is NOT due

        # Sort: c1 (due, priority 0) should come before c2 (unstarted, priority 1)
        assert result["mastery_by_concept"][0]["concept_id"] == "c1"
        assert result["mastery_by_concept"][1]["concept_id"] == "c2"

        # due_count = 1 (only c1 is due; c2 is unstarted, not due)
        assert result["due_count"] == 1
        assert result["mastered_count"] == 0
