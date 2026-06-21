"""Tests for ARISTOTLE GUI pages + API client + entry point registration.

Run: pytest tests/test_aristotle_gui_pages.py -v
"""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

warnings.filterwarnings("ignore", message="coroutine.*was never awaited")


# ---------------------------------------------------------------------------
# Test 1: pages module importable
# ---------------------------------------------------------------------------


def test_pages_module_importable():
    """import aristotle.gui.pages succeeds (with Brain GUI mocked)."""
    # The module uses try/except for Brain GUI imports — it should
    # import cleanly even without Brain's gui package present.
    import aristotle.gui.pages

    assert hasattr(aristotle.gui.pages, "aristotle_stats_page")
    assert hasattr(aristotle.gui.pages, "aristotle_map_page")
    assert hasattr(aristotle.gui.pages, "aristotle_settings_page")


# ---------------------------------------------------------------------------
# Test 2: get_mastery returns dict on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_client_get_mastery_returns_dict_on_success():
    """Mock httpx to return {"mastery_by_concept": []}.
    Call get_mastery() — assert returns dict.
    """
    from aristotle.gui.api_client import get_mastery

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"mastery_by_concept": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("aristotle.gui.api_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_mastery()

    assert isinstance(result, dict)
    assert "mastery_by_concept" in result


# ---------------------------------------------------------------------------
# Test 3: get_mastery returns {} on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_client_get_mastery_returns_empty_on_error():
    """Mock httpx to raise ConnectError.
    Call get_mastery() — assert returns {}.
    """
    from aristotle.gui.api_client import get_mastery

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("aristotle.gui.api_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_mastery()

    assert result == {}


# ---------------------------------------------------------------------------
# Test 4: get_concepts returns list on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_client_get_concepts_returns_list_on_success():
    """Mock httpx to return [{"id": "c1"}].
    Assert returns list.
    """
    from aristotle.gui.api_client import get_concepts

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": "c1", "topic": "Inertia"}]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("aristotle.gui.api_client.httpx.AsyncClient", return_value=mock_client):
        result = await get_concepts()

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == "c1"


# ---------------------------------------------------------------------------
# Test 5: entry point registered
# ---------------------------------------------------------------------------


def test_entry_point_registered():
    """importlib.metadata.entry_points(group="aip.extension_gui")
    assert any ep.name == "aristotle"
    """
    from importlib.metadata import entry_points

    eps = entry_points(group="aip.extension_gui")
    names = [ep.name for ep in eps]
    assert "aristotle" in names, f"expected 'aristotle' in {names}"
