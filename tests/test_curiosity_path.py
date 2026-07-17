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
# Task 24 Fix 2 — sentence-level matching + new patterns
# ---------------------------------------------------------------------------


def test_classify_real_screenshot_input_give_me_rundown():
    """Task 24 Fix 2: the EXACT input from the live placement screenshot
    must classify as QUESTION (not ANSWER).

    'give me a rundown on our learning plan' — a direct-request question.
    Before Task 24, 'give me' was not in question_starters, so this fell
    through to ANSWER and was sent to examiner.evaluate() to be graded
    as a content answer — the live bug.
    """
    assert _classify_student_input("give me a rundown on our learning plan") == "QUESTION"


def test_classify_real_screenshot_input_second_session_oriented():
    """Task 24 Fix 2: the EXACT input from the live placement screenshot
    must classify as QUESTION or TANGENT (not ANSWER).

    'this is my second session. i want to be oriented in what we are
    learning next' — the question-like part ('i want to be oriented...')
    is in the SECOND clause, not the first. Before Task 24's sentence-level
    matching, the whole-string startswith check missed it and this fell
    through to ANSWER.

    Either QUESTION or TANGENT is acceptable — both route to curiosity
    handling. What matters is it's NOT ANSWER.
    """
    result = _classify_student_input(
        "this is my second session. i want to be oriented in what we are learning next"
    )
    assert result in ("QUESTION", "TANGENT"), (
        f"real screenshot input must classify as QUESTION or TANGENT (not ANSWER); "
        f"got {result!r}"
    )


def test_classify_regression_give_up_is_answer():
    """Task 24 Fix 2 REGRESSION TEST: a genuine content answer that
    happens to contain a similar word ('give up? no, resins are...') must
    still classify as ANSWER.

    The clause split should help here: 'give up' is its own clause (after
    the '?' split), but 'give up' does NOT start with 'give me' (the only
    'give' question_starter). The 'no, resins are...' clause doesn't
    match any trigger either. So the whole input classifies as ANSWER
    and reaches examiner.evaluate() normally.

    If this test fails, the new patterns are too loose and are catching
    real content answers.
    """
    result = _classify_student_input(
        "give up? no, resins are sticky plant secretions used for protection"
    )
    assert result == "ANSWER", (
        f"genuine content answer containing 'give up?' must classify as ANSWER; "
        f"got {result!r}"
    )


def test_classify_new_question_starters():
    """Task 24 Fix 2: each new question_starter pattern classifies as QUESTION."""
    new_starters = [
        "show me the concept map",
        "walk me through how SM-2 works",
        "orient me on the plan structure",
        "remind me what we covered last session",
        "help me understand the difference between IgE and IgG",
    ]
    for text in new_starters:
        result = _classify_student_input(text)
        assert result == "QUESTION", (
            f"{text!r} should classify as QUESTION (new question_starter); got {result!r}"
        )


def test_classify_new_tangent_markers():
    """Task 24 Fix 2: each new tangent_marker pattern classifies as TANGENT."""
    new_markers = [
        "i want to switch topics",
        "i'd like to review something else",
        "i wanted to ask about the exam",
    ]
    for text in new_markers:
        result = _classify_student_input(text)
        assert result == "TANGENT", (
            f"{text!r} should classify as TANGENT (new tangent_marker); got {result!r}"
        )


def test_classify_clause_level_matching_catches_mid_sentence_triggers():
    """Task 24 Fix 2: a trigger phrase in the second clause (after a period)
    is caught — not just triggers at the very start of the whole input."""
    # 'i want to' in the second clause → TANGENT
    assert _classify_student_input(
        "ok great. i want to review the plan again"
    ) == "TANGENT"
    # 'what' in the second clause → QUESTION
    assert _classify_student_input(
        "sounds good. what comes next?"
    ) == "QUESTION"


def test_classify_existing_behavior_preserved():
    """Task 24 Fix 2 regression: the existing whole-string checks still work.
    The clause-level matching is ADDITIVE — it doesn't replace the existing
    behavior, just extends it."""
    # These all worked before Task 24 and must still work after.
    assert _classify_student_input("The acceleration is 9.8 m/s") == "ANSWER"
    assert _classify_student_input("What is Newton's second law?") == "QUESTION"
    assert _classify_student_input("How does gravity work") == "QUESTION"
    assert _classify_student_input("But what about relativity") == "TANGENT"
    assert _classify_student_input("Got it") == "CHAT"


# ---------------------------------------------------------------------------
# Task 26 Fix 3 — tighten CHAT classifier's "no"/"yes" matching
# ---------------------------------------------------------------------------


def test_classify_no_idea_is_not_chat():
    """Task 26 Fix 3: 'no idea' must NOT classify as CHAT.

    Before the fix, 'no' was in social_words with prefix matching, so
    'no idea' matched lower.startswith('no ') → CHAT. But 'no idea' is
    a realistic decline phrasing, not a bare acknowledgment — it should
    reach the decline gate (Task 23) via the ANSWER path, or be classified
    as substantive content.
    """
    assert _classify_student_input("no idea") != "CHAT", (
        "'no idea' must NOT classify as CHAT — it's a decline phrasing, "
        "not a bare acknowledgment"
    )


def test_classify_no_clue_is_not_chat():
    """Task 26 Fix 3: 'no clue' must NOT classify as CHAT (same as 'no idea')."""
    assert _classify_student_input("no clue") != "CHAT", (
        "'no clue' must NOT classify as CHAT — it's a decline phrasing"
    )


def test_classify_bare_no_still_chat():
    """Task 26 Fix 3 regression: bare 'no' must STILL classify as CHAT.

    The fix carves 'no' out of the prefix-matching loop and requires an
    EXACT match. Bare 'no' (the legitimate social-acknowledgment case)
    must still be CHAT — don't break it.
    """
    assert _classify_student_input("no") == "CHAT"
    assert _classify_student_input("no.") == "CHAT"
    assert _classify_student_input("no,") == "CHAT"


def test_classify_bare_yes_still_chat():
    """Task 26 Fix 3 regression: bare 'yes' must STILL classify as CHAT.
    Same carve-out as 'no' — exact match only, but bare 'yes' still works."""
    assert _classify_student_input("yes") == "CHAT"
    assert _classify_student_input("yes.") == "CHAT"


def test_classify_yes_but_im_not_sure_is_not_chat():
    """Task 26 Fix 3: 'yes but I'm not sure' must NOT classify as CHAT.

    This is a qualified answer (substantive content), not a bare
    acknowledgment. Before the fix, it matched lower.startswith('yes ') → CHAT.
    """
    assert _classify_student_input("yes but I'm not sure") != "CHAT", (
        "'yes but I'm not sure' must NOT classify as CHAT — it's a "
        "qualified answer, not a bare acknowledgment"
    )


def test_classify_other_social_words_still_prefix_match():
    """Task 26 Fix 3 regression: the rest of social_words (ok, thanks, sure,
    etc.) keep the existing prefix-match behavior. Only 'no' and 'yes' are
    carved out for exact-match-only."""
    # These should all still be CHAT (prefix match).
    assert _classify_student_input("ok") == "CHAT"
    assert _classify_student_input("ok, got it") == "CHAT"
    assert _classify_student_input("thanks") == "CHAT"
    assert _classify_student_input("sure") == "CHAT"
    assert _classify_student_input("got it") == "CHAT"
    assert _classify_student_input("understood") == "CHAT"


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
