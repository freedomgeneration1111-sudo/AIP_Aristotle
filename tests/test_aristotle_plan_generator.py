"""Tests for the multi-step plan generation pipeline (Task 14 + Task 16 fix).

Task 16: generate_plan_pipeline's Step 1 used to hard-fail with "No
structural map found — paper may not be ingested yet" whenever
get_structural_map() returned no TOC. But an empty structural map is the
ACCEPTED degraded mode when structural_analysis.py's single-call-for-the-
whole-paper design (documented as reliable only up to ~30 chunks) times out
on a larger textbook and gets skipped — see paper_ingestor.py's
"ingest_analysis_timeout ... skipping (chunks are indexed, RAG will work
without structure)". Since nothing ever retries the skipped analysis, the
old check meant any real textbook over ~30 chunks could NEVER get a plan:
"please try confirming your plan again" just re-ran the same doomed check
forever. Reproduced against a real 99-chunk pharmacognosy textbook.

Run: pytest tests/test_aristotle_plan_generator.py -v
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aip.foundation.protocols.actors import ActorResult
from aristotle.actors.intake import IntakeSession
from aristotle.actors.plan_generator import generate_plan_pipeline


# ---------------------------------------------------------------------------
# Fakes (same pattern as test_aristotle_intake.py / test_aristotle_tutoring.py)
# ---------------------------------------------------------------------------


class _FakeModelProvider:
    """Fake ModelProvider that returns one canned response for every call."""

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


class _FakeConn:
    """Fake aiosqlite.Connection — records writes, never validates schema."""

    async def execute(self, sql: str, params: tuple = ()):
        return self

    async def commit(self):
        pass

    async def fetchall(self):
        return []

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


def _make_container(model_provider: Any) -> Any:
    return type(
        "C",
        (),
        {
            "model_provider": model_provider,
            "corpus_registry": _FakeRegistry(_FakeStores(_FakeConn())),
        },
    )()


def _make_session() -> IntakeSession:
    return IntakeSession(
        current_focus="COMPLETE",
        material_ids=["material-1"],
        extracted={
            "subject": "pharmacognosy",
            "prior_knowledge": "high school biology and chemistry",
            "goals": "career as a pharmacist",
            "schedule_minutes": 30,
        },
    )


_LLM_RESPONSE = json.dumps({"gaps": [], "phases": [], "concepts": []})


# ---------------------------------------------------------------------------
# Task 16 tests
# ---------------------------------------------------------------------------


class TestPlanGeneratorStructuralMapDegradation:
    @pytest.mark.asyncio
    @patch("aristotle.actors.intake.IntakeActor.generate_plan", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.retrieve_relevant_chunks", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.get_structural_map", new_callable=AsyncMock)
    async def test_proceeds_without_structural_map_when_chunks_exist(
        self, mock_smap, mock_retrieve, mock_generate_plan,
    ):
        """Moses's exact real-world case: structural analysis timed out and
        was skipped (empty TOC), but embedding succeeded (chunks ARE
        retrievable). The pipeline must NOT hard-fail — it should proceed
        through to plan storage using retrieved chunks alone.
        """
        mock_smap.return_value = {"toc": [], "concepts": [], "citations": []}
        mock_retrieve.return_value = [
            {"chunk_id": "c1", "content": "Pharmacognosy is the study of crude drugs."},
        ]
        mock_generate_plan.return_value = ActorResult(ok=True, data={"plan_id": "plan-123"})

        model_provider = _FakeModelProvider(responses={"beast": _LLM_RESPONSE})
        container = _make_container(model_provider)
        session = _make_session()

        await generate_plan_pipeline(session, container, "job-1")

        # The old bug hard-failed at Step 1 before ever reaching storage.
        # Reaching generate_plan() proves the pipeline ran all 6 steps.
        mock_generate_plan.assert_awaited()
        assert session.plan_id == "plan-123"

    @pytest.mark.asyncio
    @patch("aristotle.actors.plan_generator._fail_plan_job", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.retrieve_relevant_chunks", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.get_structural_map", new_callable=AsyncMock)
    async def test_still_fails_when_genuinely_not_ingested(
        self, mock_smap, mock_retrieve, mock_fail,
    ):
        """The safety net is preserved, just moved to the right signal: if
        there's truly nothing retrievable — no structural map AND no
        chunks — the job still fails. The message now describes the real
        problem (not-yet-ingested) rather than a symptom (missing
        structural map specifically, which is normal for large textbooks).
        """
        mock_smap.return_value = {"toc": [], "concepts": [], "citations": []}
        mock_retrieve.return_value = []

        model_provider = _FakeModelProvider(responses={"beast": _LLM_RESPONSE})
        container = _make_container(model_provider)
        session = _make_session()

        await generate_plan_pipeline(session, container, "job-2")

        mock_fail.assert_awaited_once()
        _, _, message = mock_fail.call_args.args
        assert "not be ingested yet" in message
        assert "structural map" not in message.lower()

    @pytest.mark.asyncio
    @patch("aristotle.actors.intake.IntakeActor.generate_plan", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.retrieve_relevant_chunks", new_callable=AsyncMock)
    @patch("aristotle.ingestion.paper_ingestor.get_structural_map", new_callable=AsyncMock)
    async def test_proceeds_normally_when_structural_map_present(
        self, mock_smap, mock_retrieve, mock_generate_plan,
    ):
        """Sanity check: the common/ideal case (structural analysis
        succeeded, e.g. a small paper under ~30 chunks) is unaffected by
        the Task 16 change.
        """
        mock_smap.return_value = {
            "toc": [{"heading": "Introduction", "chunk_index": 0}],
            "concepts": ["pharmacognosy"],
            "citations": [],
        }
        mock_retrieve.return_value = [
            {"chunk_id": "c1", "content": "Pharmacognosy is the study of crude drugs."},
        ]
        mock_generate_plan.return_value = ActorResult(ok=True, data={"plan_id": "plan-456"})

        model_provider = _FakeModelProvider(responses={"beast": _LLM_RESPONSE})
        container = _make_container(model_provider)
        session = _make_session()

        await generate_plan_pipeline(session, container, "job-3")

        mock_generate_plan.assert_awaited()
        assert session.plan_id == "plan-456"
