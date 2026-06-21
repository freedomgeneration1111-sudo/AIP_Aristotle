"""Tests for ADR-002 Amendment A1 — curiosity path + intent classification.

Run: pytest tests/test_curiosity_path.py -v
"""

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import MagicMock

import pytest

from aip.foundation.protocols.actors import ActorContext
from aristotle.session import (
    SessionContext,
    SessionState,
    _classify_student_input,
    _step_curiosity,
    _step_chat,
)

warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Tests 1-5: _classify_student_input (pure unit, no async)
# ---------------------------------------------------------------------------


def test_classify_answer_default():
    """_classify_student_input('The acceleration is 9.8 m/s') → 'ANSWER'."""
    assert _classify_student_input("The acceleration is 9.8 m/s") == "ANSWER"


def test_classify_question_mark():
    """_classify_student_input('What is Newton's second law?') → 'QUESTION'."""
    assert _classify_student_input("What is Newton's second law?") == "QUESTION"


def test_classify_question_starter():
    """_classify_student_input('How does gravity work') → 'QUESTION'."""
    assert _classify_student_input("How does gravity work") == "QUESTION"


def test_classify_tangent():
    """_classify_student_input('But what about relativity') → 'TANGENT'."""
    assert _classify_student_input("But what about relativity") == "TANGENT"


def test_classify_chat():
    """_classify_student_input('Got it') → 'CHAT'."""
    assert _classify_student_input("Got it") == "CHAT"


# ---------------------------------------------------------------------------
# Test 6: _step_curiosity returns intent_class + weave-back
# ---------------------------------------------------------------------------


class _FakeModelProvider:
    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls = []

    async def call(self, slot_name, messages, **kwargs):
        self.calls.append((slot_name, messages))
        return {"content": self._responses.get(slot_name, "Here's the answer.")}


class _FakeConn:
    async def execute(self, sql, params=()):
        return MagicMock()

    async def commit(self):
        pass


class _FakeStores:
    def __init__(self, write_conn):
        self.connection_manager = type("CM", (), {"write_conn": write_conn})()


class _FakeRegistry:
    def __init__(self, stores):
        self._stores = stores

    async def get_stores(self, corpus_id, **kwargs):
        return self._stores


def _make_ctx(model_provider=None, stores=None):
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
        config=None,
        logger=__import__("logging").getLogger("test"),
        cancel_event=asyncio.Event(),
    )


@pytest.mark.asyncio
async def test_session_step_curiosity_response_includes_intent_class():
    """Call _step_curiosity with QUESTION intent.
    Assert result has intent_class == 'QUESTION' and weave-back offer.
    """
    fake = _FakeModelProvider(
        responses={
            "beast": "Gravity is the force that attracts objects toward each other."
        }
    )
    conn = _FakeConn()
    ctx = _make_ctx(model_provider=fake, stores=_FakeStores(conn))
    session = SessionContext(concept_id="newton_first_law", state=SessionState.TEACH)

    result = await _step_curiosity(ctx, session, "What is gravity?", "QUESTION")

    assert result.ok
    assert result.data["intent_class"] == "QUESTION"
    assert "Want to keep exploring this" in result.data["response"]
    # Session state should NOT have changed — curiosity doesn't advance phase.
    assert session.state == SessionState.TEACH


# ---------------------------------------------------------------------------
# Test 7: _step_chat returns CHAT intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_step_chat_returns_chat_intent():
    """Call _step_chat. Assert intent_class == 'CHAT', session_complete == False."""
    fake = _FakeModelProvider(responses={"beast": "Great! Let's continue."})
    ctx = _make_ctx(model_provider=fake)
    session = SessionContext(concept_id="c1", state=SessionState.TEACH)

    result = await _step_chat(ctx, session, "Got it")

    assert result.ok
    assert result.data["intent_class"] == "CHAT"
    # Session state should NOT have changed.
    assert session.state == SessionState.TEACH
