"""Integration test: the real ARISTOTLE extension mounts via ExtensionHost.

This is the dogfood test — it verifies the installed ARISTOTLE package
(discovered via entry points, not filesystem path) loads, migrates, and
registers its actors + pages. Unlike test_extension_lifecycle.py (which
uses synthetic demo extensions), this test exercises the real ARISTOTLE
manifest, real migration SQL, real config schema, and real actors.

After the repo split, ARISTOTLE is a separate pip-installable package.
The tests use entry-point discovery (the same path the host uses in
production) + importlib.resources.files() for path-based assertions.

Run:  CI=true uv run pytest tests/test_aristotle_extension.py -v
"""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest

from aip.adapter.extensions.host import ExtensionHost
from aip.adapter.extensions.state import ExtensionState


# Resolve the installed ARISTOTLE package root via importlib.resources.
# This works for both pip install -e (editable) and pip install (wheel).
try:
    _ARISTOTLE_PKG_ROOT = Path(str(importlib.resources.files("aristotle")))
except Exception:
    _ARISTOTLE_PKG_ROOT = None  # aristotle not installed — tests will skip


@pytest.fixture
async def container(tmp_path: Path):
    """Minimal container with a real CorpusRegistry backed by tmp_path DBs."""
    from aip.adapter.corpus_registry import CorpusRegistry
    from aip.foundation.corpus_types import CorpusType

    registry = CorpusRegistry(max_corpora=4)
    await registry.startup(
        corpora_to_register=[
            ("definer", CorpusType.CONVERSATION, tmp_path / "definer.db"),
        ],
    )

    class _MinimalContainer:
        def __init__(self):
            self.corpus_registry = registry
            self.vigil = None
            self.beast = None
            self.sexton_actor = None
            self.model_provider = None

    return _MinimalContainer()


@pytest.fixture
def host(tmp_path: Path, container) -> ExtensionHost:
    """Host pointed at an empty extensions/ dir.

    ARISTOTLE is discovered via the aip.extensions entry point (the
    production path), NOT via filesystem glob. The empty extensions_dir
    means the filesystem discovery path finds nothing; the entry-point
    discovery path finds the installed ARISTOTLE package.
    """
    return ExtensionHost(
        extensions_dir=tmp_path / "extensions",  # empty — no filesystem extensions
        container=container,
        manifest_version_range=(1, 1),
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_package_has_expected_files():
    """Sanity: the installed ARISTOTLE package has the expected files."""
    assert (_ARISTOTLE_PKG_ROOT / "extension.yaml").exists()
    assert (_ARISTOTLE_PKG_ROOT / "migrations" / "M001_aristotle.sql").exists()
    assert (_ARISTOTLE_PKG_ROOT / "hooks.py").exists()
    assert (_ARISTOTLE_PKG_ROOT / "actors" / "socrates.py").exists()
    assert (_ARISTOTLE_PKG_ROOT / "config.py").exists()


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_manifest_validates(host: ExtensionHost):
    """ARISTOTLE's manifest parses + validates (manifest_version=1, id=aristotle)."""
    await host.discover()
    await host.validate()
    state = host.state("aristotle")
    assert state is ExtensionState.VALIDATED, (
        f"ARISTOTLE manifest should validate; state={state}, "
        f"failures={[f.to_dict() for f in host.failures('aristotle')]}"
    )


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_migrations_create_tables(host: ExtensionHost, container):
    """M001_aristotle.sql creates aristotle_concept + aristotle_struggle_pattern."""
    await host.start()
    assert host.state("aristotle") in (ExtensionState.REGISTERED, ExtensionState.MOUNTED), (
        f"ARISTOTLE should reach REGISTERED or MOUNTED; failures="
        f"{[f.to_dict() for f in host.failures('aristotle')]}"
    )

    # The migration applies to the aristotle:textbook corpus (ADR-014 §6.2).
    stores = await container.corpus_registry.get_stores("aristotle:textbook")
    assert await _table_exists(stores, "aristotle_concept"), \
        "M001_aristotle.sql should create aristotle_concept table"
    assert await _table_exists(stores, "aristotle_struggle_pattern"), \
        "M001_aristotle.sql should create aristotle_struggle_pattern table"


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_registers_actors(host: ExtensionHost):
    """All three actors are registered via hooks.py::on_load."""
    await host.start()
    actors = host.registered_actors()
    assert "socrates" in actors, f"SOCRATES should be registered; actors={actors}"
    assert "examiner" in actors, f"EXAMINER should be registered; actors={actors}"
    assert "mentor" in actors, f"MENTOR should be registered; actors={actors}"


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_socrates_conforms_to_actor_protocol():
    """SOCRATES conforms to the foundation Actor Protocol (ADR-014 §5.2)."""
    from aip.foundation.protocols.actors import Actor
    from aristotle.actors import SocratesActor

    actor = SocratesActor()
    assert isinstance(actor, Actor), (
        "SocratesActor must conform to foundation.protocols.actors.Actor — "
        "otherwise the host's isinstance check skips its scheduler."
    )
    assert actor.name == "socrates"
    assert actor.cadence == 0.0  # manual-only (ADR-001 §3)


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_config_schema_loads(host: ExtensionHost):
    """config.schema (aristotle.config:AristotleSettings) loads + instantiates."""
    await host.discover()
    await host.validate()
    rec = host.registry.get_record("aristotle")
    assert rec is not None
    assert rec.config is not None, (
        "AristotleSettings should be instantiated at validate; "
        f"failures={[f.to_dict() for f in host.failures('aristotle')]}"
    )
    # AristotleSettings defaults (ADR-001 §7 bilingual)
    assert rec.config.primary_language == "en"
    assert rec.config.alt_language == "ur"


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_health_surfaces_in_health(host: ExtensionHost):
    """ARISTOTLE appears in host.health() with state=REGISTERED or MOUNTED."""
    await host.start()
    health = host.health()
    by_id = {h["id"]: h for h in health}
    assert "aristotle" in by_id, f"ARISTOTLE should be in health: {health}"
    assert by_id["aristotle"]["state"] in ("REGISTERED", "MOUNTED")
    assert by_id["aristotle"]["version"] == "0.1.0"


@pytest.mark.skipif(_ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed")
@pytest.mark.asyncio
async def test_aristotle_stop_cancels_actors(host: ExtensionHost):
    """host.stop() cancels all actors + marks ARISTOTLE DISABLED."""
    await host.start()
    assert "socrates" in host.registered_actors()
    await host.stop()
    assert not host.is_running()
    assert host.state("aristotle") is ExtensionState.DISABLED
    assert host.registered_actors() == []


# --------------------------------------------------------------------------
async def _table_exists(stores, table: str) -> bool:
    """Check a SQLite table exists in the corpus's write connection."""
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None
