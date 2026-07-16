"""Tests for ARISTOTLE API routes: misconceptions, settings, upload.

Run: pytest tests/test_aristotle_routes.py -v
"""

from __future__ import annotations

import io
import warnings
from unittest.mock import MagicMock

import pytest

warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Fakes (same pattern as test_aristotle_intake.py)
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, rows=None):
        self._rows = rows or []
        self._executed = []

    async def execute(self, sql, params=()):
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

    async def get_stores(self, corpus_id, **kwargs):
        return self._stores


def _make_request(container, body=None, headers=None):
    """Build a fake FastAPI Request for route testing."""
    request = MagicMock()
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.container = container
    if body is not None:
        if isinstance(body, dict):

            async def _json():
                return body

            request.json = _json
        elif isinstance(body, bytes):

            async def _body():
                return body

            request.body = _body
    else:

        async def _json():
            return {}

        request.json = _json

        async def _body():
            return b""

        request.body = _body
    request.headers = headers or {}
    return request


def _make_container(conn):
    """Build a container with a fake corpus_registry backed by conn."""
    container = MagicMock()
    container.corpus_registry = _FakeRegistry(_FakeStores(conn))
    return container


# ---------------------------------------------------------------------------
# Test 1: misconceptions route returns list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Phase B/D — route not yet implemented (ARISTOTLE-DEBT-009)", strict=True)
async def test_misconceptions_route_returns_list():
    """Mock DB to return 2 rows. GET /aristotle/misconceptions."""
    from aristotle.api import misconceptions_route

    conn = _FakeConn(
        rows=[
            (
                1,
                "c1",
                "thinks force sustains motion",
                "force changes motion",
                "2026-01-01",
            ),
            (
                2,
                "c2",
                "confuses inertia with friction",
                "inertia is mass property",
                "2026-01-02",
            ),
        ]
    )
    container = _make_container(conn)
    request = _make_request(container)

    result = await misconceptions_route(request)
    assert "misconceptions" in result
    assert isinstance(result["misconceptions"], list)
    assert len(result["misconceptions"]) == 2
    assert result["misconceptions"][0]["concept_id"] == "c1"
    assert (
        result["misconceptions"][0]["misconception_text"]
        == "thinks force sustains motion"
    )


# ---------------------------------------------------------------------------
# Test 2: get settings returns defaults when no row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Phase B/D — route not yet implemented (ARISTOTLE-DEBT-009)", strict=True)
async def test_get_settings_returns_defaults_when_no_row():
    """Mock DB to return no row. GET /aristotle/settings."""
    from aristotle.api import get_settings_route

    conn = _FakeConn(rows=None)  # fetchone returns None
    container = _make_container(conn)
    request = _make_request(container)

    result = await get_settings_route(request)
    assert result["primary_language"] == "English"
    assert result["session_length"] == 5
    assert result["mastery_threshold"] == 0.85
    assert result["hint_aggressiveness"] == "balanced"


# ---------------------------------------------------------------------------
# Test 3: post settings upserts and returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Phase B/D — route not yet implemented (ARISTOTLE-DEBT-009)", strict=True)
async def test_post_settings_upserts_and_returns():
    """POST /aristotle/settings with Urdu settings."""
    from aristotle.api import update_settings_route

    conn = _FakeConn(rows=None)
    container = _make_container(conn)
    request = _make_request(
        container,
        body={
            "primary_language": "Urdu",
            "session_length": 7,
            "mastery_threshold": 0.9,
            "hint_aggressiveness": "generous",
        },
    )

    result = await update_settings_route(request)
    assert result["primary_language"] == "Urdu"
    assert result["session_length"] == 7
    assert result["mastery_threshold"] == 0.9
    assert result["hint_aggressiveness"] == "generous"
    # Verify the INSERT OR REPLACE was issued.
    insert_calls = [
        sql
        for sql, _ in conn._executed
        if "INSERT OR REPLACE INTO aristotle_settings" in sql
    ]
    assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# Test 4: upload PDF returns extracted text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_pdf_returns_extracted_text():
    """POST /aristotle/upload with a minimal PDF."""
    from aristotle.api import upload_route
    from pypdf import PdfWriter

    # Create a minimal 1-page PDF in memory.
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_buffer = io.BytesIO()
    writer.write(pdf_buffer)
    pdf_bytes = pdf_buffer.getvalue()

    container = _make_container(_FakeConn())
    request = _make_request(
        container, body=pdf_bytes, headers={"content-type": "application/pdf"}
    )

    result = await upload_route(request)
    assert "extracted_text" in result
    assert result["source_type"] == "pdf"
    assert result["page_count"] == 1
    assert isinstance(result["char_count"], int)


@pytest.mark.asyncio
async def test_upload_sql_insert_column_value_order_is_correct():
    """Regression: the INSERT into aristotle_uploaded_material must use
    (id=material_id, student_id='definer', ...) — NOT swapped.

    A previous version had the values as ("definer", material_id, ...)
    which silently swapped id and student_id. Because id is the PRIMARY KEY,
    the second upload would fail with UNIQUE constraint violation.
    The _FakeConn doesn't enforce constraints, so this test inspects the
    actual SQL params to catch the regression.
    """
    from aristotle.api import upload_route

    conn = _FakeConn()
    container = _make_container(conn)
    request = _make_request(
        container,
        body=b"some text content",
        headers={"content-type": "text/plain"},
    )

    result = await upload_route(request)
    assert result["material_id"], "material_id should be non-empty"

    # Find the INSERT statement.
    insert_calls = [
        (sql, params)
        for sql, params in conn._executed
        if "INSERT INTO aristotle_uploaded_material" in sql
    ]
    assert len(insert_calls) == 1, "expected exactly one INSERT"
    sql, params = insert_calls[0]

    # Column order in the SQL is (id, student_id, filename, source_type,
    # extracted_text, char_count, page_count). Params must match.
    assert params[0] == result["material_id"], (
        f"params[0] (id) should be the material_id UUID, got {params[0]!r}"
    )
    assert params[1] == "definer", (
        f"params[1] (student_id) should be 'definer', got {params[1]!r}"
    )
    assert params[2] == "upload", (
        f"params[2] (filename) should be 'upload', got {params[2]!r}"
    )
    assert params[3] == "text", (
        f"params[3] (source_type) should be 'text', got {params[3]!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: upload image returns extracted text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_image_returns_extracted_text():
    """POST /aristotle/upload with a minimal image."""
    from aristotle.api import upload_route
    from PIL import Image

    # Create a small white image.
    img = Image.new("RGB", (100, 50), color="white")
    img_buffer = io.BytesIO()
    img.save(img_buffer, format="PNG")
    img_bytes = img_buffer.getvalue()

    container = _make_container(_FakeConn())
    request = _make_request(
        container, body=img_bytes, headers={"content-type": "image/png"}
    )

    result = await upload_route(request)
    assert "extracted_text" in result
    assert result["source_type"] == "image"
    # tesseract may return empty string on a blank image — that's fine.
    assert isinstance(result["extracted_text"], str)


# ---------------------------------------------------------------------------
# Test 6: upload txt returns text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_txt_returns_text():
    """POST /upload with body b'Hello world' + Content-Type text/plain."""
    from aristotle.api import upload_route

    container = _make_container(_FakeConn())
    request = _make_request(
        container,
        body=b"Hello world",
        headers={"content-type": "text/plain"},
    )

    result = await upload_route(request)
    assert result["source_type"] == "text"
    assert "Hello" in result["extracted_text"]


# ---------------------------------------------------------------------------
# Test 7: upload html strips tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_html_strips_tags():
    """POST /upload with b'<h1>Title</h1><p>Body text</p>' Content-Type text/html."""
    from aristotle.api import upload_route

    container = _make_container(_FakeConn())
    request = _make_request(
        container,
        body=b"<h1>Title</h1><p>Body text</p>",
        headers={"content-type": "text/html"},
    )

    result = await upload_route(request)
    assert result["source_type"] == "html"
    assert "Title" in result["extracted_text"]
    assert "<h1>" not in result["extracted_text"]


# ---------------------------------------------------------------------------
# Test 8: upload json returns text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_json_returns_text():
    """POST /upload with b'{"key": "value"}' Content-Type application/json."""
    from aristotle.api import upload_route

    container = _make_container(_FakeConn())
    request = _make_request(
        container,
        body=b'{"key": "value"}',
        headers={"content-type": "application/json"},
    )

    result = await upload_route(request)
    assert result["source_type"] == "text"
    assert "key" in result["extracted_text"]


# ---------------------------------------------------------------------------
# Test 9: upload unsupported returns 415
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_unsupported_returns_415():
    """POST /upload with Content-Type application/octet-stream + no magic bytes."""
    from aristotle.api import upload_route
    from fastapi import HTTPException

    container = _make_container(_FakeConn())
    request = _make_request(
        container,
        body=b"\x00\x01\x02\x03binary garbage",
        headers={"content-type": "application/octet-stream"},
    )

    try:
        await upload_route(request)
        assert False, "should have raised HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 415


# ---------------------------------------------------------------------------
# Test 9: health route returns 200 + minimal JSON (GUI sidebar liveness probe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_route_returns_ok():
    """GET /aristotle/health — must return 200-shaped payload without
    touching the container or DB. Used by Brain's GUI sidebar health
    poller (_poll_extension_health in gui/components/layout.py) to
    decide whether to render this extension's nav items.
    """
    from aristotle.api import health_route

    # health_route takes no request arg — it's a pure liveness probe.
    result = await health_route()
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert result.get("extension") == "aristotle"


# ---------------------------------------------------------------------------
# Task 22 — /session/step output field (TEACH/PROBE/QUIZ outputs were dropped)
# ---------------------------------------------------------------------------
#
# Task 21's investigation item confirmed that session_step_route's `output`
# field only read `result.data["prompt"]`, which is the key _step_predict()
# uses. _step_teach() returns data.explanation, _step_probe()/_step_quiz()
# return data.question, _step_evaluate() returns data.feedback — so every
# step except PREDICT was silently dropped from the chat UI. These are the
# integration-level regression tests that should have existed already: they
# hit /session/step directly via the route function and assert response
# shape. The whole reason the bug shipped unnoticed is that no test
# exercised the actual API response shape for anything but PREDICT.
#
# Pattern follows the existing _make_request + _make_container helpers
# above (direct route call, no FastAPI TestClient lifespan needed).


class _FakeModelProvider:
    """Fake ModelProvider that returns canned responses by slot.

    Same shape as the one in test_aristotle_tutoring.py — returns a
    successful content dict for the slot, or an error dict if the slot
    is configured to fail (for the ok=False test case).
    """

    def __init__(self, responses: dict[str, str] | None = None,
                 error_slots: set[str] | None = None):
        self._responses = responses or {}
        self._error_slots = error_slots or set()
        self.calls: list[tuple[str, list[dict]]] = []

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        self.calls.append((slot_name, messages))
        if slot_name in self._error_slots:
            return {
                "error": True,
                "content": "",
                "error_message": "429 rate limit (fake)",
            }
        content = self._responses.get(slot_name, f"[fake {slot_name} response]")
        return {
            "content": content,
            "model": "fake-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "latency_ms": 5,
        }


def _make_session_container(model_provider, conn):
    """Build a container with model_provider + corpus_registry + extensions.registry.

    run_session_step() checks container.extensions.registry (non-None) and
    then imports the actors directly. The actors reach the corpus via
    container.corpus_registry.get_stores("aristotle:textbook").
    """
    return type(
        "C",
        (),
        {
            "model_provider": model_provider,
            "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            "extensions": type("H", (), {"registry": "fake-registry-non-none"})(),
        },
    )()


def _concept_row():
    """A single aristotle_concept row for _FakeConn to return.

    Columns match the SELECT in SocratesActor._fetch_concept /
    ExaminerActor._fetch_concept: id, topic, subtopic, content_primary,
    content_alt, content_alt_lang, prerequisite_concept_id, bloom_target.
    """
    return ("c1", "Inertia", "Newton's First Law",
            "Objects resist changes in motion.", None, None, None, 3)


@pytest.mark.asyncio
async def test_session_step_teach_output_contains_explanation(monkeypatch):
    """Task 22 Fix 1 regression test: POST /session/step for a TEACH step
    returns response["output"] containing the explanation text.

    Before the fix, `output` was computed as
    `result.data.get("prompt", "")` — but _step_teach() returns
    `data={"explanation": ...}` (no prompt key), so output was "" and the
    explanation was silently dropped from the chat UI. This test would
    have caught the bug at Task 21 ship time.
    """
    # Neutralize asyncio.sleep so the examiner retry helper (if invoked)
    # doesn't slow the test. TEACH uses the `beast` slot which won't
    # retry here, but patch defensively.
    async def _no_sleep(_seconds):
        pass
    monkeypatch.setattr("aristotle.actors.examiner.asyncio.sleep", _no_sleep)

    from aristotle.api import session_step_route
    from aristotle.session import SessionState

    fake = _FakeModelProvider(
        responses={"beast": "Inertia is the resistance of an object to changes in its motion."}
    )
    conn = _FakeConn(rows=[_concept_row()])
    container = _make_session_container(fake, conn)
    # Session in TEACH state — the next run_session_step dispatches to
    # _step_teach → socrates.teach() → model_provider.call(slot="beast").
    session_body = {
        "student_id": "definer",
        "concept_id": "c1",
        "state": SessionState.TEACH.value,
    }
    request = _make_request(container, body={"session": session_body, "student_input": ""})

    result = await session_step_route(request)

    assert result["ok"] is True, f"TEACH step should succeed; error={result.get('error')}"
    # The fix: output is non-empty and contains a recognizable substring
    # of the mocked explanation. Before the fix, this was "".
    assert result["output"], (
        "TEACH step output must be non-empty — before Task 22 Fix 1, the "
        "explanation was silently dropped because the API only read "
        "result.data['prompt'] (which _step_teach doesn't populate)"
    )
    assert "inertia" in result["output"].lower(), (
        f"output should contain the explanation text; got: {result['output']!r}"
    )
    # The session advanced to PROBE (TEACH → PROBE per the state machine).
    assert result["session"]["state"] == "PROBE"
    # The fake model was called with the beast slot (SOCRATES.teach uses beast).
    assert any(call[0] == "beast" for call in fake.calls), (
        f"TEACH step should call the beast slot; calls={fake.calls}"
    )


@pytest.mark.asyncio
async def test_session_step_probe_output_contains_question(monkeypatch):
    """Task 22 Fix 1 regression test: POST /session/step for a PROBE step
    returns response["output"] containing the probe question text.

    Before the fix, output was "" because _step_probe() returns
    `data={"question": ...}` (no prompt key).
    """
    async def _no_sleep(_seconds):
        pass
    monkeypatch.setattr("aristotle.actors.examiner.asyncio.sleep", _no_sleep)

    from aristotle.api import session_step_route
    from aristotle.session import SessionState

    fake = _FakeModelProvider(
        responses={"evaluation": "In your own words, what does inertia mean?"}
    )
    conn = _FakeConn(rows=[_concept_row()])
    container = _make_session_container(fake, conn)
    session_body = {
        "student_id": "definer",
        "concept_id": "c1",
        "state": SessionState.PROBE.value,
    }
    request = _make_request(container, body={"session": session_body, "student_input": ""})

    result = await session_step_route(request)

    assert result["ok"] is True, f"PROBE step should succeed; error={result.get('error')}"
    assert result["output"], (
        "PROBE step output must be non-empty — before Task 22 Fix 1, the "
        "probe question was silently dropped because the API only read "
        "result.data['prompt'] (which _step_probe doesn't populate)"
    )
    assert "inertia" in result["output"].lower(), (
        f"output should contain the probe question text; got: {result['output']!r}"
    )
    assert result["session"]["state"] == "QUIZ"
    assert any(call[0] == "evaluation" for call in fake.calls), (
        f"PROBE step should call the evaluation slot; calls={fake.calls}"
    )


@pytest.mark.asyncio
async def test_session_step_evaluate_output_contains_feedback(monkeypatch):
    """Task 22 Fix 1 regression test: POST /session/step for an EVALUATE
    step returns response["output"] containing the learner-facing feedback.

    Before the fix, output was "" because _step_evaluate() returns
    `data={"feedback": ..., "score": ..., ...}` (no prompt key).
    """
    import json as _json
    async def _no_sleep(_seconds):
        pass
    monkeypatch.setattr("aristotle.actors.examiner.asyncio.sleep", _no_sleep)

    from aristotle.api import session_step_route
    from aristotle.session import SessionState

    eval_payload = _json.dumps({
        "score": 0.9,
        "mastery_achieved": True,
        "feedback": "Exactly — you identified that inertia resists changes in motion.",
        "diagnosis": None,
    })
    fake = _FakeModelProvider(responses={"evaluation": eval_payload})
    conn = _FakeConn(rows=[_concept_row()])
    container = _make_session_container(fake, conn)
    session_body = {
        "student_id": "definer",
        "concept_id": "c1",
        "state": SessionState.EVALUATE.value,
        "last_quiz_question": "What is inertia?",
        "quiz_generated": True,
    }
    request = _make_request(
        container,
        body={"session": session_body, "student_input": "objects resist changes in motion"},
    )

    result = await session_step_route(request)

    assert result["ok"] is True, f"EVALUATE step should succeed; error={result.get('error')}"
    assert result["output"], (
        "EVALUATE step output must be non-empty — before Task 22 Fix 1, the "
        "feedback was silently dropped because the API only read "
        "result.data['prompt'] (which _step_evaluate doesn't populate)"
    )
    assert "inertia" in result["output"].lower(), (
        f"output should contain the feedback text; got: {result['output']!r}"
    )


@pytest.mark.asyncio
async def test_session_step_predict_output_still_works(monkeypatch):
    """Task 22 Fix 1 regression test: the existing PREDICT path (which
    DOES use data.prompt) still works after the fix. The fallback chain
    is `prompt or explanation or question or feedback or ""` — prompt is
    first, so PREDICT's output is unchanged.
    """
    async def _no_sleep(_seconds):
        pass
    monkeypatch.setattr("aristotle.actors.examiner.asyncio.sleep", _no_sleep)

    from aristotle.api import session_step_route
    from aristotle.session import SessionState

    # PREDICT doesn't call a model — it's a fixed template. So we don't
    # need a fake model provider that returns anything specific; the
    # _FakeModelProvider default returns "[fake {slot} response]" which
    # is fine because predict() never calls it.
    fake = _FakeModelProvider()
    conn = _FakeConn(rows=[_concept_row()])
    container = _make_session_container(fake, conn)
    session_body = {
        "student_id": "definer",
        "concept_id": "c1",
        "state": SessionState.PREDICT.value,
    }
    request = _make_request(container, body={"session": session_body, "student_input": ""})

    result = await session_step_route(request)

    assert result["ok"] is True, f"PREDICT step should succeed; error={result.get('error')}"
    assert result["output"], (
        "PREDICT step output must be non-empty (this path already worked "
        "before Task 22 — regression check that the fix didn't break it)"
    )
    # The predict prompt mentions the concept topic ("Inertia").
    assert "inertia" in result["output"].lower(), (
        f"PREDICT output should mention the concept topic; got: {result['output']!r}"
    )


@pytest.mark.asyncio
async def test_session_step_infra_failure_returns_student_message(monkeypatch):
    """Task 22 Fix 2 regression test: when run_session_step returns
    ok=False (e.g. EVALUATE's model call exhausted retries on a 429), the
    API returns a non-empty student-facing message in `output` — NOT a
    blank string, and NOT the raw error text.

    Before the fix, `output` was "" on ok=False, so the student's screen
    went blank. After Task 21 Fix 3, evaluate() legitimately returns
    ok=False on infra failure (instead of silently scoring the student
    0.0) — so this case now happens in production and must be handled.
    """
    async def _no_sleep(_seconds):
        pass
    monkeypatch.setattr("aristotle.actors.examiner.asyncio.sleep", _no_sleep)

    from aristotle.api import session_step_route
    from aristotle.session import SessionState

    # Configure the evaluation slot to always return error=True (429).
    # _call_with_retry will retry 3 times (max_retries=2), then return
    # ok=False with "model call failed or returned empty content: ...".
    fake = _FakeModelProvider(error_slots={"evaluation"})
    conn = _FakeConn(rows=[_concept_row()])
    container = _make_session_container(fake, conn)
    session_body = {
        "student_id": "definer",
        "concept_id": "c1",
        "state": SessionState.EVALUATE.value,
        "last_quiz_question": "What is inertia?",
        "quiz_generated": True,
    }
    request = _make_request(
        container,
        body={"session": session_body, "student_input": "a guess"},
    )

    result = await session_step_route(request)

    # The EVALUATE step failed (infra failure after retries).
    assert result["ok"] is False, (
        "EVALUATE step should fail when the evaluation slot returns "
        "error=True after retries (Task 21 Fix 3 behavior)"
    )
    # Task 22 Fix 2: output is a non-empty student-facing message — NOT blank.
    assert result["output"], (
        "ok=False must still return a non-empty student-facing message in "
        "`output` — before Task 22 Fix 2, the screen went blank on infra failure"
    )
    # And NOT the raw error text (which may contain provider/429/stack details).
    assert result["output"] != result.get("error"), (
        "output must NOT be the raw error text — that's for logs (the "
        "`error` key), not the chat UI"
    )
    assert "429" not in result["output"], (
        "output must NOT expose the raw error code to the student; "
        f"got: {result['output']!r}"
    )
    # The raw error IS carried in the separate "error" key for debugging.
    assert result.get("error"), (
        "the raw error must still be in the `error` key for logs/debugging"
    )
    assert "model call failed" in result["error"], (
        f"error key should carry the infra-failure message; got: {result['error']!r}"
    )
    # Verify retries actually happened (max_retries=2 → 3 total calls).
    eval_calls = [c for c in fake.calls if c[0] == "evaluation"]
    assert len(eval_calls) == 3, (
        f"evaluate() should retry 3 times (max_retries=2); got {len(eval_calls)} calls"
    )
