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
    """Minimal container with a real CorpusRegistry backed by tmp_path DBs.

    Teardown (AIP_Brain DEBT-013 fix, ported): closes every corpus's
    stores via `stores.close_all()` so aiosqlite's background worker
    threads shut down cleanly before pytest closes the event loop.
    Without this, the worker thread tries `call_soon_threadsafe` on a
    closed loop and raises `RuntimeError: Event loop is closed`, surfaced
    as a `PytestUnhandledThreadExceptionWarning`.

    Note: the real ARISTOTLE extension registers `aristotle:textbook`
    during host.start(), so the teardown must close EVERY registered
    corpus — not just the `definer` corpus we explicitly registered here.
    """
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

    container = _MinimalContainer()
    try:
        yield container
    finally:
        # Close every registered corpus's stores (idempotent — safe even
        # if a test already closed them). This includes the
        # aristotle:textbook corpus that ARISTOTLE registers during
        # host.start(). Cancels the aiosqlite background worker threads
        # cleanly while the event loop is still open.
        for corpus_id in list(registry.corpora.keys()):
            try:
                stores = await registry.get_stores(corpus_id)
                if stores is not None and hasattr(stores, "close_all"):
                    await stores.close_all()
            except Exception:
                pass  # best-effort teardown — never mask a test failure


@pytest.fixture
async def host(tmp_path: Path, container):
    """Host pointed at an empty extensions/ dir.

    ARISTOTLE is discovered via the aip.extensions entry point (the
    production path), NOT via filesystem glob. The empty extensions_dir
    means the filesystem discovery path finds nothing; the entry-point
    discovery path finds the installed ARISTOTLE package. Note: unlike
    AIP_Brain's test_extension_lifecycle.py host fixture, we do NOT pass
    discover_installed_packages=False — ARISTOTLE's tests rely on
    entry-point discovery to find the real installed package.

    Teardown (AIP_Brain DEBT-013 fix, ported): `await host.stop()` on
    teardown cancels every actor scheduler task the test spawned via
    `host.start()`. stop() is idempotent — returns early if the host was
    never started or already stopped (test_aristotle_stop_cancels_actors
    calls stop() itself).

    Additionally, we patch `supervised_task` to track the inner coroutine
    (`_actor_scheduler_loop(...)`) so we can explicitly close it on
    teardown. Without this, if a task is cancelled while still PENDING
    (before `_supervised_inner` reaches `await coro`), the inner
    coroutine object is never touched — Python's GC flags it as
    "never awaited" and emits `RuntimeWarning: coroutine
    '_actor_scheduler_loop' was never awaited`. Closing the coroutine
    explicitly marks it as "handled" and suppresses the warning.
    """
    import aip.adapter.extensions.host as _host_mod

    _tracked_coros: list = []
    _orig_supervised = _host_mod.supervised_task

    def _tracking_supervised(name, coro):
        _tracked_coros.append(coro)
        return _orig_supervised(name, coro)

    _host_mod.supervised_task = _tracking_supervised

    h = ExtensionHost(
        extensions_dir=tmp_path / "extensions",  # empty — no filesystem extensions
        container=container,
        manifest_version_range=(1, 1),
    )
    try:
        yield h
    finally:
        _host_mod.supervised_task = _orig_supervised  # restore
        if h.is_running():
            await h.stop()
        # Explicitly close any inner coroutines that were never awaited
        # (task was cancelled while PENDING, so _supervised_inner never
        # reached `await coro`). coro.close() marks the coroutine as
        # "handled" and suppresses the RuntimeWarning at GC time.
        for coro in _tracked_coros:
            # coro is a coroutine object (not a Task). Check cr_frame:
            # if it's None, the coroutine already finished or was closed.
            # If it's not None, the coroutine is still pending — close it
            # to mark it as "handled" and suppress the RuntimeWarning.
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                try:
                    coro.close()
                except Exception:
                    pass


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_package_has_expected_files():
    """Sanity: the installed ARISTOTLE package has the expected files."""
    assert (_ARISTOTLE_PKG_ROOT / "extension.yaml").exists()
    assert (_ARISTOTLE_PKG_ROOT / "migrations" / "M001_aristotle.sql").exists()
    assert (_ARISTOTLE_PKG_ROOT / "hooks.py").exists()
    assert (_ARISTOTLE_PKG_ROOT / "actors" / "socrates.py").exists()
    assert (_ARISTOTLE_PKG_ROOT / "config.py").exists()


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
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


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_migrations_create_tables(host: ExtensionHost, container):
    """M001_aristotle.sql creates aristotle_concept + aristotle_struggle_pattern."""
    await host.start()
    assert host.state("aristotle") in (
        ExtensionState.REGISTERED,
        ExtensionState.MOUNTED,
    ), (
        f"ARISTOTLE should reach REGISTERED or MOUNTED; failures="
        f"{[f.to_dict() for f in host.failures('aristotle')]}"
    )

    # The migration applies to the aristotle:textbook corpus (ADR-014 §6.2).
    stores = await container.corpus_registry.get_stores("aristotle:textbook")
    assert await _table_exists(stores, "aristotle_concept"), (
        "M001_aristotle.sql should create aristotle_concept table"
    )
    assert await _table_exists(stores, "aristotle_struggle_pattern"), (
        "M001_aristotle.sql should create aristotle_struggle_pattern table"
    )


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_m003_creates_phase_b5_schema(host: ExtensionHost, container):
    """M003_aristotle_phase_b5.sql creates the Phase B.5 tables + extends mastery.

    Phase B.5 (ADR-002 Rev 2) adds:
      - aristotle_predict_event: logs the learner's pre-TEACH prediction.
      - aristotle_misconception_log: structured per-instance misconception history.
      - aristotle_mastery extended columns: hint_assisted_correct, slip_count,
        cold_start_passed, transfer_correct, transfer_attempted.

    This test runs the full host lifecycle (which applies M001 + M002 + M003
    in order) and verifies every new schema element exists. Follows the
    pattern of test_aristotle_migrations_create_tables above.
    """
    await host.start()
    assert host.state("aristotle") in (
        ExtensionState.REGISTERED,
        ExtensionState.MOUNTED,
    ), (
        f"ARISTOTLE should reach REGISTERED or MOUNTED; failures="
        f"{[f.to_dict() for f in host.failures('aristotle')]}"
    )

    stores = await container.corpus_registry.get_stores("aristotle:textbook")

    # New tables (ADR-002 §10.4, §10.5)
    assert await _table_exists(stores, "aristotle_predict_event"), (
        "M003 should create aristotle_predict_event table"
    )
    assert await _table_exists(stores, "aristotle_misconception_log"), (
        "M003 should create aristotle_misconception_log table"
    )

    # Extended aristotle_mastery columns (ADR-002 §10.6)
    expected_new_columns = [
        "hint_assisted_correct",
        "slip_count",
        "cold_start_passed",
        "transfer_correct",
        "transfer_attempted",
    ]
    for col in expected_new_columns:
        assert await _column_exists(stores, "aristotle_mastery", col), (
            f"M003 should add column {col!r} to aristotle_mastery"
        )

    # Sanity: the pre-existing M002 columns are still there (M003 is additive).
    assert await _column_exists(stores, "aristotle_mastery", "easiness_factor"), (
        "M002 easiness_factor column should still exist after M003"
    )
    assert await _column_exists(stores, "aristotle_mastery", "mastered"), (
        "M002 mastered column should still exist after M003"
    )


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_m004_creates_phase_d_schema(host: ExtensionHost, container):
    """M004_aristotle_phase_d.sql creates the Phase D onboarding tables.

    Phase D (ADR-002 Rev 2 §10) adds:
      - aristotle_intake_session: records the intake conversation state.
      - aristotle_learning_plan: versioned learning plan with concept sequence.
      - aristotle_placement_event: placement calibration results.

    This test runs the full host lifecycle (which applies M001 + M002 +
    M003 + M004 in order) and verifies every new table exists. Follows
    the pattern of test_aristotle_migrations_create_tables above.
    """
    await host.start()
    assert host.state("aristotle") in (
        ExtensionState.REGISTERED,
        ExtensionState.MOUNTED,
    ), (
        f"ARISTOTLE should reach REGISTERED or MOUNTED; failures="
        f"{[f.to_dict() for f in host.failures('aristotle')]}"
    )

    stores = await container.corpus_registry.get_stores("aristotle:textbook")

    # Phase D tables (ADR-002 §10.1, §10.2, §10.3)
    assert await _table_exists(stores, "aristotle_intake_session"), (
        "M004 should create aristotle_intake_session table"
    )
    assert await _table_exists(stores, "aristotle_learning_plan"), (
        "M004 should create aristotle_learning_plan table"
    )
    assert await _table_exists(stores, "aristotle_placement_event"), (
        "M004 should create aristotle_placement_event table"
    )

    # Sanity: the pre-existing M001 tables are still there (M004 is additive).
    assert await _table_exists(stores, "aristotle_concept"), (
        "M001 aristotle_concept table should still exist after M004"
    )
    assert await _table_exists(stores, "aristotle_mastery"), (
        "M002 aristotle_mastery table should still exist after M004"
    )


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_registers_actors(host: ExtensionHost):
    """All three actors are registered via hooks.py::on_load."""
    await host.start()
    actors = host.registered_actors()
    assert "socrates" in actors, f"SOCRATES should be registered; actors={actors}"
    assert "examiner" in actors, f"EXAMINER should be registered; actors={actors}"
    assert "mentor" in actors, f"MENTOR should be registered; actors={actors}"


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
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


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
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


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
@pytest.mark.asyncio
async def test_aristotle_health_surfaces_in_health(host: ExtensionHost):
    """ARISTOTLE appears in host.health() with state=REGISTERED or MOUNTED."""
    await host.start()
    health = host.health()
    by_id = {h["id"]: h for h in health}
    assert "aristotle" in by_id, f"ARISTOTLE should be in health: {health}"
    assert by_id["aristotle"]["state"] in ("REGISTERED", "MOUNTED")
    assert by_id["aristotle"]["version"] == "0.1.0"


@pytest.mark.skipif(
    _ARISTOTLE_PKG_ROOT is None, reason="aristotle package not installed"
)
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


async def _column_exists(stores, table: str, column: str) -> bool:
    """Check a column exists on a table via pragma_table_info."""
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        f"SELECT 1 FROM pragma_table_info('{table}') WHERE name = ?",
        (column,),
    )
    return await cur.fetchone() is not None
