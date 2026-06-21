"""Contract + behavior tests for EXAMINER + MENTOR actors — ADR-ARISTOTLE §2.

These tests verify:
  1. Both actors conform to the foundation Actor Protocol (isinstance check).
  2. Both actors have the correct name + cadence (manual-only).
  3. EXAMINER degrades gracefully when no model is configured.
  4. MENTOR reads/writes the aristotle_struggle_pattern table.

The integration test (test_aristotle_extension.py) already covers the
"all three actors register via hooks.py" path. These tests focus on the
actors themselves in isolation.

Run:  CI=true uv run pytest tests/test_aristotle_actors.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aip.foundation.protocols.actors import Actor, ActorContext


# --------------------------------------------------------------------------
# Protocol conformance tests (no aiosqlite needed — pure isinstance checks)
# --------------------------------------------------------------------------


def test_examiner_conforms_to_actor_protocol():
    """EXAMINER conforms to the foundation Actor Protocol (ADR-014 §5.2)."""
    from aristotle.actors import ExaminerActor

    actor = ExaminerActor()
    assert isinstance(actor, Actor), (
        "ExaminerActor must conform to foundation.protocols.actors.Actor"
    )
    assert actor.name == "examiner"
    assert actor.cadence == 0.0  # manual-only


def test_mentor_conforms_to_actor_protocol():
    """MENTOR conforms to the foundation Actor Protocol (ADR-014 §5.2)."""
    from aristotle.actors import MentorActor

    actor = MentorActor()
    assert isinstance(actor, Actor), (
        "MentorActor must conform to foundation.protocols.actors.Actor"
    )
    assert actor.name == "mentor"
    assert actor.cadence == 0.0  # manual-only


def test_socrates_still_conforms():
    """SOCRATES still conforms after the multi-actor refactor."""
    from aristotle.actors import SocratesActor

    actor = SocratesActor()
    assert isinstance(actor, Actor)
    assert actor.name == "socrates"
    assert actor.cadence == 0.0


def test_all_three_actors_have_distinct_names():
    """The three actors have distinct names (required by host.register_actor)."""
    from aristotle.actors import ExaminerActor, MentorActor, SocratesActor

    names = {
        SocratesActor().name,
        ExaminerActor().name,
        MentorActor().name,
    }
    assert names == {"socrates", "examiner", "mentor"}, (
        f"expected 3 distinct names, got {names}"
    )


def test_all_three_actors_have_health():
    """Each actor exposes a health() dict (ADR-014 §5.2)."""
    from aristotle.actors import ExaminerActor, MentorActor, SocratesActor

    for actor_cls in [SocratesActor, ExaminerActor, MentorActor]:
        health = actor_cls().health()
        assert isinstance(health, dict), (
            f"{actor_cls.__name__}.health() must return dict"
        )
        assert "state" in health, f"{actor_cls.__name__}.health() must include 'state'"
        assert "name" in health, f"{actor_cls.__name__}.health() must include 'name'"


# --------------------------------------------------------------------------
# Behavior tests (need aiosqlite for CorpusRegistry — these run in CI)
# --------------------------------------------------------------------------


def _make_ctx(container: Any, config: Any = None) -> ActorContext:
    """Build a minimal ActorContext for testing."""
    return ActorContext(
        container=container,
        config=config,
        logger=__import__("logging").getLogger("test"),
        cancel_event=asyncio.Event(),
    )


class _FakeRegistry:
    """Fake CorpusRegistry that returns a fake stores object."""

    def __init__(self, stores: Any = None):
        self._stores = stores

    async def get_stores(self, corpus_id: str, **kwargs):
        return self._stores


class _FakeStores:
    """Fake CorpusStores with a mock connection_manager."""

    def __init__(self, write_conn):
        class _CM:
            pass

        self.connection_manager = _CM()
        self.connection_manager.write_conn = write_conn


class _FakeConn:
    """Fake aiosqlite.Connection for testing MENTOR's SQL."""

    def __init__(self, rows: list[tuple] | None = None):
        self._rows = rows  # pre-seeded rows for SELECT
        self._executed = []  # log of executed SQL

    async def execute(self, sql: str, params: tuple = ()):
        self._executed.append((sql, params))
        return _FakeCursor(self._rows)

    async def commit(self):
        pass


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_examiner_degrades_gracefully_without_model():
    """EXAMINER returns ok=True even when no model is configured (governance: no silent model calls)."""
    from aristotle.actors import ExaminerActor

    # Container with corpus_registry but NO model_provider
    container = type(
        "C",
        (),
        {
            "corpus_registry": _FakeRegistry(
                stores=_FakeStores(write_conn=_FakeConn())
            ),
            "model_provider": None,
        },
    )()

    actor = ExaminerActor()
    ctx = _make_ctx(container=container)
    result = await actor.run_cycle(ctx)

    assert result.ok is True, (
        "EXAMINER should be ok=True even without a model — it's ready, "
        "just can't generate questions yet. The tutoring loop checks this."
    )


@pytest.mark.asyncio
async def test_examiner_fails_without_corpus_registry():
    """EXAMINER returns ok=False when corpus_registry is missing."""
    from aristotle.actors import ExaminerActor

    container = type("C", (), {"corpus_registry": None, "model_provider": None})()
    actor = ExaminerActor()
    ctx = _make_ctx(container=container)
    result = await actor.run_cycle(ctx)

    assert result.ok is False
    assert "corpus_registry" in result.error


@pytest.mark.asyncio
async def test_mentor_initializes_struggle_pattern_when_absent():
    """MENTOR initializes the struggle_pattern table with a placeholder when no row exists."""
    from aristotle.actors import MentorActor

    # Empty table — fetchone returns None
    conn = _FakeConn(rows=None)
    container = type(
        "C",
        (),
        {
            "corpus_registry": _FakeRegistry(stores=_FakeStores(write_conn=conn)),
        },
    )()

    actor = MentorActor()
    ctx = _make_ctx(container=container)
    result = await actor.run_cycle(ctx)

    assert result.ok is True, f"MENTOR should succeed; error={result.error}"

    # Verify the INSERT was executed
    insert_executed = any(
        "INSERT" in sql.upper() and "aristotle_struggle_pattern" in sql.lower()
        for sql, _ in conn._executed
    )
    assert insert_executed, (
        f"MENTOR should INSERT a placeholder row; executed={conn._executed}"
    )


@pytest.mark.asyncio
async def test_mentor_reads_existing_struggle_pattern():
    """MENTOR reads the existing struggle_pattern without INSERTing."""
    from aristotle.actors import MentorActor

    # Pre-seeded row — fetchone returns it
    existing_pattern = (
        "Learner struggles with abstraction — needs concrete examples first."
    )
    conn = _FakeConn(rows=[(existing_pattern,)])
    container = type(
        "C",
        (),
        {
            "corpus_registry": _FakeRegistry(stores=_FakeStores(write_conn=conn)),
        },
    )()

    actor = MentorActor()
    ctx = _make_ctx(container=container)
    result = await actor.run_cycle(ctx)

    assert result.ok is True, f"MENTOR should succeed; error={result.error}"

    # Verify NO INSERT was executed (the row already exists)
    insert_executed = any("INSERT" in sql.upper() for sql, _ in conn._executed)
    assert not insert_executed, (
        f"MENTOR should NOT INSERT when a row exists; executed={conn._executed}"
    )


@pytest.mark.asyncio
async def test_mentor_fails_without_corpus_registry():
    """MENTOR returns ok=False when corpus_registry is missing."""
    from aristotle.actors import MentorActor

    container = type("C", (), {"corpus_registry": None})()
    actor = MentorActor()
    ctx = _make_ctx(container=container)
    result = await actor.run_cycle(ctx)

    assert result.ok is False
    assert "corpus_registry" in result.error


# --------------------------------------------------------------------------
# Workflow engine-compatibility tests (moved from AIP_Brain — these test
# ARISTOTLE's workflow, so they belong with ARISTOTLE, not the platform)
# --------------------------------------------------------------------------


def test_aristotle_workflow_yaml_parses():
    """The tutoring_session_v1.yaml parses as valid YAML with the right structure."""
    import yaml
    from pathlib import Path

    workflow_path = (
        Path(__file__).parent.parent
        / "aristotle"
        / "workflows"
        / "tutoring_session_v1.yaml"
    )
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)
    assert wf["template_id"] == "tutoring_session_v1"
    assert wf["name"] == "Tutoring Session v1"
    assert "nodes" in wf
    assert len(wf["nodes"]) == 8, f"expected 8 nodes, got {len(wf['nodes'])}"
    node_ids = [n["id"] for n in wf["nodes"]]
    assert node_ids == [
        "predict",
        "teach",
        "probe",
        "quiz",
        "evaluate",
        "check_mastery",
        "remediate",
        "next_concept",
    ]


def test_aristotle_workflow_uses_engine_compatible_node_types():
    """Every node type is one the L5 engine's loader accepts."""
    import yaml
    from pathlib import Path

    workflow_path = (
        Path(__file__).parent.parent
        / "aristotle"
        / "workflows"
        / "tutoring_session_v1.yaml"
    )
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)
    allowed_types = {
        "script",
        "agent",
        "condition",
        "dialog",
        "parallel",
        "review",
        "re_synthesize",
    }
    for node in wf["nodes"]:
        assert node["type"] in allowed_types, (
            f"Node {node['id']!r} has type {node['type']!r} not in {sorted(allowed_types)}"
        )


def test_aristotle_workflow_agent_nodes_have_model_slot():
    """Every agent node has a model_slot (required by the loader's AgentNode)."""
    import yaml
    from pathlib import Path

    workflow_path = (
        Path(__file__).parent.parent
        / "aristotle"
        / "workflows"
        / "tutoring_session_v1.yaml"
    )
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)
    agent_nodes = [n for n in wf["nodes"] if n["type"] == "agent"]
    assert len(agent_nodes) >= 3
    for node in agent_nodes:
        assert "model_slot" in node, f"Agent node {node['id']!r} must have model_slot"


def test_aristotle_workflow_condition_node_has_branches():
    """The check_mastery condition node has next_on_true + next_on_false."""
    import yaml
    from pathlib import Path

    workflow_path = (
        Path(__file__).parent.parent
        / "aristotle"
        / "workflows"
        / "tutoring_session_v1.yaml"
    )
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)
    condition_nodes = [n for n in wf["nodes"] if n["type"] == "condition"]
    assert len(condition_nodes) == 1
    cond = condition_nodes[0]
    assert cond["id"] == "check_mastery"
    assert cond["next_on_true"] == "next_concept"
    assert cond["next_on_false"] == "remediate"
