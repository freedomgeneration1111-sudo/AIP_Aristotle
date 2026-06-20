"""Tests for ARISTOTLE API routes: misconceptions, settings, upload.

Run: pytest tests/test_aristotle_routes.py -v
"""
from __future__ import annotations

import io
import json
import warnings
from unittest.mock import AsyncMock, MagicMock

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
async def test_misconceptions_route_returns_list():
    """Mock DB to return 2 rows. GET /aristotle/misconceptions."""
    from aristotle.api import misconceptions_route

    conn = _FakeConn(rows=[
        (1, "c1", "thinks force sustains motion", "force changes motion", "2026-01-01"),
        (2, "c2", "confuses inertia with friction", "inertia is mass property", "2026-01-02"),
    ])
    container = _make_container(conn)
    request = _make_request(container)

    result = await misconceptions_route(request)
    assert "misconceptions" in result
    assert isinstance(result["misconceptions"], list)
    assert len(result["misconceptions"]) == 2
    assert result["misconceptions"][0]["concept_id"] == "c1"
    assert result["misconceptions"][0]["misconception_text"] == "thinks force sustains motion"


# ---------------------------------------------------------------------------
# Test 2: get settings returns defaults when no row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
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
async def test_post_settings_upserts_and_returns():
    """POST /aristotle/settings with Urdu settings."""
    from aristotle.api import update_settings_route

    conn = _FakeConn(rows=None)
    container = _make_container(conn)
    request = _make_request(container, body={
        "primary_language": "Urdu",
        "session_length": 7,
        "mastery_threshold": 0.9,
        "hint_aggressiveness": "generous",
    })

    result = await update_settings_route(request)
    assert result["primary_language"] == "Urdu"
    assert result["session_length"] == 7
    assert result["mastery_threshold"] == 0.9
    assert result["hint_aggressiveness"] == "generous"
    # Verify the INSERT OR REPLACE was issued.
    insert_calls = [
        sql for sql, _ in conn._executed
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
    request = _make_request(container, body=pdf_bytes, headers={"content-type": "application/pdf"})

    result = await upload_route(request)
    assert "extracted_text" in result
    assert result["source_type"] == "pdf"
    assert result["page_count"] == 1
    assert isinstance(result["char_count"], int)


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
    request = _make_request(container, body=img_bytes, headers={"content-type": "image/png"})

    result = await upload_route(request)
    assert "extracted_text" in result
    assert result["source_type"] == "image"
    # tesseract may return empty string on a blank image — that's fine.
    assert isinstance(result["extracted_text"], str)
