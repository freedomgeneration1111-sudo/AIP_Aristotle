"""Tests for teacher dashboard — session-history route + API client.

Run: pytest tests/test_teacher_dashboard.py -v
"""
from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Fakes (same pattern as test_aristotle_routes.py)
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


def _make_request(container):
    request = MagicMock()
    request.headers = {}
    request.app.state.container = container
    return request


def _make_container(conn):
    container = MagicMock()
    container.corpus_registry = _FakeRegistry(_FakeStores(conn))
    return container


# ---------------------------------------------------------------------------
# Test 1: session-history route returns sessions list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_history_route_returns_sessions_list():
    """Mock DB to return 2 grouped rows. GET /aristotle/session-history."""
    from aristotle.api import session_history_route

    conn = _FakeConn(rows=[
        ("sess-1", "c1", 5, "2026-01-01T10:00:00", "2026-01-01T10:30:00", 3, 1, 1),
        ("sess-2", "c2", 3, "2026-01-02T14:00:00", "2026-01-02T14:15:00", 2, 1, 0),
    ])
    container = _make_container(conn)
    request = _make_request(container)

    result = await session_history_route(request)
    assert "sessions" in result
    assert isinstance(result["sessions"], list)
    assert len(result["sessions"]) == 2
    assert result["sessions"][0]["session_id"] == "sess-1"
    assert result["sessions"][0]["event_count"] == 5
    assert result["sessions"][0]["answer_count"] == 3
    assert result["sessions"][0]["curiosity_count"] == 1
    assert result["sessions"][0]["chat_count"] == 1


# ---------------------------------------------------------------------------
# Test 2: session-history empty when no log entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_history_empty_when_no_log_entries():
    """Mock DB to return no rows. GET /aristotle/session-history."""
    from aristotle.api import session_history_route

    conn = _FakeConn(rows=[])
    container = _make_container(conn)
    request = _make_request(container)

    result = await session_history_route(request)
    assert result == {"sessions": []}


# ---------------------------------------------------------------------------
# Test 3: get_session_history API client returns list on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_history_api_client_returns_list():
    """Mock httpx to return {"sessions": [{"session_id": "s1"}]}."""
    from aristotle.gui.api_client import get_session_history

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"sessions": [{"session_id": "s1"}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("aristotle.gui.api_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_session_history()

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["session_id"] == "s1"


# ---------------------------------------------------------------------------
# Test 4: get_session_history returns empty on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_history_returns_empty_on_error():
    """Mock httpx to raise ConnectError."""
    from aristotle.gui.api_client import get_session_history

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("aristotle.gui.api_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_session_history()

    assert result == []
