"""Tests for Task 18 (ADR-004): student identity + plan/concept ownership scoping.

Covers:
  - Migration M009: aristotle_student table + new columns on
    aristotle_learning_plan (student_id, material_id) and aristotle_concept
    (plan_id, material_id) + backfill behavior.
  - New API endpoints: POST /students, GET /students, GET /plans?student_id=X.
  - Updated API endpoints: GET /concepts (optional plan_id/material_id filters
    + unscoped-call warning), GET /dashboard (optional student_id/plan_id
    filters + unscoped-call warning).
  - IntakeActor.generate_plan populates the new columns on
    aristotle_learning_plan + aristotle_concept.
  - IntakeSession.student_id round-trips through intake_session_to_dict /
    intake_session_from_dict.

Run: pytest tests/test_aristotle_student_scoping.py -v
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from aip.foundation.protocols.actors import ActorContext
from aristotle.actors.intake import (
    IntakeActor,
    IntakeSession,
    intake_session_from_dict,
    intake_session_to_dict,
)


# ---------------------------------------------------------------------------
# Fakes — same pattern as test_aristotle_intake.py / test_plan_generator.py
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def close(self):
        pass


class _FakeConn:
    """Fake aiosqlite.Connection — records writes, returns canned rows.

    The caller can register SQL-substring matchers that determine what
    rows a given execute() call should return. Unmatched queries return
    []. This lets us simulate "the plan_job table has these rows" /
    "the concept table has these rows" without standing up a real DB.
    """

    def __init__(self, routes: list[tuple[str, list[tuple]]] | None = None):
        # routes: list of (sql_substring, rows_to_return)
        self._routes = routes or []
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple = ()):
        self.executed.append((sql, params))
        for substring, rows in self._routes:
            if substring.lower() in sql.lower():
                return _FakeCursor(rows)
        return _FakeCursor([])

    async def commit(self):
        pass


class _FakeStores:
    def __init__(self, write_conn):
        self.connection_manager = type("CM", (), {"write_conn": write_conn})()


class _FakeRegistry:
    def __init__(self, stores):
        self._stores = stores

    async def get_stores(self, corpus_id: str, **kwargs):
        return self._stores


def _make_ctx(stores: Any | None = None, model_provider: Any | None = None) -> ActorContext:
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
        logger=logging.getLogger("test"),
        cancel_event=asyncio.Event(),
    )


def _make_request(body: dict | None = None, query_params: dict | None = None, container: Any = None):
    """Build a minimal fake FastAPI Request for route tests."""
    return type(
        "R",
        (),
        {
            "app": type(
                "A",
                (),
                {"state": type("S", (), {"container": container})()},
            )(),
            "query_params": type(
                "Q",
                (),
                {
                    "get": lambda self, key, default=None: (query_params or {}).get(key, default),
                },
            )(),
            "json": (lambda: asyncio.sleep(0, body or {})) if False else (asyncio.coroutine(lambda: body or {}) if hasattr(asyncio, "coroutine") else None),
        },
    )()


async def _request_json_factory(body: dict):
    """Helper: returns an async function returning the given body."""
    async def _json():
        return body
    return _json


def _build_request(body: dict | None = None, query_params: dict | None = None, container: Any = None):
    """Build a minimal fake FastAPI Request that works with the route code."""
    # request.json() is awaited with no args — bind body via closure.
    async def _json():
        return body or {}
    return type(
        "R",
        (),
        {
            "app": type(
                "A",
                (),
                {"state": type("S", (), {"container": container})()},
            )(),
            "query_params": type(
                "Q",
                (),
                {
                    "get": lambda self, key, default=None: (query_params or {}).get(key, default),
                },
            )(),
            "json": staticmethod(_json),
        },
    )()


# ---------------------------------------------------------------------------
# Migration M009 tests
# ---------------------------------------------------------------------------


class TestM009Migration:
    """Tests that M009 creates the new table, columns, and backfill row.

    These run against the real (in-memory) SQLite DB the extension host
    spins up — same pattern as test_aristotle_extension.py's
    test_aristotle_m003_creates_phase_b5_schema.
    """

    @pytest.mark.skipif(
        True,  # placeholder; real test is in test_aristotle_extension.py-style
        reason="M009 schema assertions live in test_aristotle_extension.py — see test_aristotle_m009_creates_student_scoping_schema",
    )
    @pytest.mark.asyncio
    async def test_placeholder(self):
        pass


# ---------------------------------------------------------------------------
# IntakeSession.student_id serialization round-trip
# ---------------------------------------------------------------------------


class TestIntakeSessionStudentId:
    def test_student_id_defaults_to_definer(self):
        """A fresh IntakeSession has student_id='definer' — preserves the
        pre-Task-18 single-tenant behavior when the API doesn't send one."""
        session = IntakeSession()
        assert session.student_id == "definer"

    def test_student_id_round_trips_through_serialization(self):
        """student_id set at /intake/start reaches generate_plan() at
        /intake/step's COMPLETE transition via the session dict."""
        session = IntakeSession(student_id="student-abc")
        d = intake_session_to_dict(session)
        assert d["student_id"] == "student-abc"

        restored = intake_session_from_dict(d)
        assert restored.student_id == "student-abc"

    def test_student_id_defaults_to_definer_for_legacy_sessions(self):
        """A session dict serialized before Task 18 (no student_id key)
        deserializes to student_id='definer' — backward compat."""
        legacy_dict = {
            "state": "GREETING",
            "subject": "pharmacognosy",
            # no student_id key
        }
        session = intake_session_from_dict(legacy_dict)
        assert session.student_id == "definer"


# ---------------------------------------------------------------------------
# IntakeActor.generate_plan populates new columns
# ---------------------------------------------------------------------------


class TestGeneratePlanPopulatesScopingColumns:
    @pytest.mark.asyncio
    async def test_concept_inserts_carry_plan_id_and_material_id(self):
        """Task 18: each aristotle_concept row created by generate_plan()
        must carry plan_id + material_id, so future callers can scope by
        plan without parsing concept_ids_json.

        Reproduces the contract ADR-004 requires: "every future write
        path that creates an aristotle_concept row must remember to
        populate the new ownership columns."
        """
        # Set up: a session with a draft_plan and a material_id, against
        # a fake conn that records every execute() call.
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="Pharmacognosy",
            goals="career as a pharmacist",
            schedule_minutes=30,
            material_ids=["material-xyz"],
            draft_plan=[
                {"topic": "Crude drugs", "subtopic": "definition", "bloom_target": 2, "content_primary": "..."},
                {"topic": "Extraction", "subtopic": "maceration", "bloom_target": 3, "content_primary": "..."},
            ],
            student_id="student-abc",
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)

        assert result.ok, f"generate_plan should succeed; error={result.error}"

        # Find every INSERT INTO aristotle_concept the actor issued.
        concept_inserts = [
            (sql, params) for sql, params in conn.executed
            if "INSERT OR REPLACE INTO aristotle_concept" in sql
        ]
        assert len(concept_inserts) == 2, (
            f"expected 2 concept inserts (one per draft_plan entry); "
            f"got {len(concept_inserts)}"
        )

        # Every concept INSERT must include plan_id + material_id in its
        # VALUES tuple. We don't hardcode the plan_id (it's a UUID
        # generated inside generate_plan), but it must be the SAME
        # plan_id across all concept inserts AND match the plan row's id.
        plan_ids_seen: set[str] = set()
        material_ids_seen: set[str] = set()
        for sql, params in concept_inserts:
            # The column list is in the SQL; the VALUES tuple is params.
            # The new INSERT puts plan_id + material_id at the end:
            #   (cid, subject_slug, topic, subtopic, bloom,
            #    content_primary, prereq_id, plan_id, material_id)
            assert "plan_id" in sql, (
                f"concept INSERT must include plan_id column; sql={sql!r}"
            )
            assert "material_id" in sql, (
                f"concept INSERT must include material_id column; sql={sql!r}"
            )
            # params is a tuple of 9 values per the new INSERT.
            assert len(params) == 9, (
                f"concept INSERT should have 9 bound params "
                f"(cid, slug, topic, subtopic, bloom, content, prereq, "
                f"plan_id, material_id); got {len(params)}: {params}"
            )
            plan_ids_seen.add(params[-2])
            material_ids_seen.add(params[-1])

        # All concepts share the same plan_id + material_id.
        assert len(plan_ids_seen) == 1, (
            f"all concept inserts should share one plan_id; saw {plan_ids_seen}"
        )
        assert material_ids_seen == {"material-xyz"}, (
            f"all concept inserts should carry the session's material_id; "
            f"saw {material_ids_seen}"
        )

    @pytest.mark.asyncio
    async def test_plan_insert_carries_student_id_and_material_id(self):
        """Task 18: the aristotle_learning_plan row created by
        generate_plan() must carry student_id + material_id, so
        GET /aristotle/plans?student_id=X can find it."""
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="Pharmacognosy",
            goals="career as a pharmacist",
            schedule_minutes=30,
            material_ids=["material-xyz"],
            draft_plan=[
                {"topic": "Crude drugs", "subtopic": "definition", "bloom_target": 2, "content_primary": "..."},
            ],
            student_id="student-abc",
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)
        assert result.ok

        plan_inserts = [
            (sql, params) for sql, params in conn.executed
            if "INSERT INTO aristotle_learning_plan" in sql
        ]
        assert len(plan_inserts) == 1, (
            f"expected 1 plan insert; got {len(plan_inserts)}"
        )
        sql, params = plan_inserts[0]
        assert "student_id" in sql, (
            f"plan INSERT must include student_id column; sql={sql!r}"
        )
        assert "material_id" in sql, (
            f"plan INSERT must include material_id column; sql={sql!r}"
        )
        # New shape: (plan_id, subject, goals, schedule, concept_ids_json,
        #   current_idx, status, created_at, student_id, material_id)
        assert len(params) == 10, (
            f"plan INSERT should have 10 bound params; got {len(params)}"
        )
        assert params[-2] == "student-abc", (
            f"plan INSERT student_id param should be 'student-abc'; got {params[-2]!r}"
        )
        assert params[-1] == "material-xyz", (
            f"plan INSERT material_id param should be 'material-xyz'; got {params[-1]!r}"
        )

    @pytest.mark.asyncio
    async def test_plan_insert_defaults_student_id_to_definer_when_absent(self):
        """Sessions that never set student_id (legacy / no API field)
        default to 'definer' — preserves pre-Task-18 behavior."""
        conn = _FakeConn()
        ctx = _make_ctx(stores=_FakeStores(conn))
        session = IntakeSession(
            subject="Pharmacognosy",
            goals="...",
            schedule_minutes=30,
            draft_plan=[
                {"topic": "X", "subtopic": "y", "bloom_target": 2, "content_primary": "..."},
            ],
            # student_id not set — defaults to 'definer'
        )

        actor = IntakeActor()
        result = await actor.generate_plan(ctx, session)
        assert result.ok

        plan_inserts = [
            (sql, params) for sql, params in conn.executed
            if "INSERT INTO aristotle_learning_plan" in sql
        ]
        assert len(plan_inserts) == 1
        _, params = plan_inserts[0]
        assert params[-2] == "definer", (
            f"plan INSERT student_id should default to 'definer'; got {params[-2]!r}"
        )
        assert params[-1] is None, (
            f"plan INSERT material_id should be None when session has no "
            f"material_ids; got {params[-1]!r}"
        )


# ---------------------------------------------------------------------------
# API route tests: /students, /plans, /concepts, /dashboard
# ---------------------------------------------------------------------------


class TestStudentsRoutes:
    @pytest.mark.asyncio
    async def test_post_students_creates_and_returns_student(self, caplog):
        """POST /students inserts a row + returns {id, name}."""
        from aristotle.api import create_student_route

        conn = _FakeConn()
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(body={"name": "Sameer"}, container=container)

        with caplog.at_level(logging.INFO):
            result = await create_student_route(request)

        assert "id" in result
        assert result["name"] == "Sameer"
        # The INSERT must have happened.
        inserts = [
            sql for sql, _ in conn.executed
            if "INSERT INTO aristotle_student" in sql
        ]
        assert len(inserts) == 1, f"expected 1 INSERT INTO aristotle_student; got {inserts}"

    @pytest.mark.asyncio
    async def test_post_students_rejects_empty_name(self):
        """POST /students with empty/missing name returns 400."""
        from aristotle.api import create_student_route
        from fastapi import HTTPException

        conn = _FakeConn()
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(body={"name": "   "}, container=container)

        with pytest.raises(HTTPException) as exc_info:
            await create_student_route(request)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_students_returns_list_ordered_by_created_at(self):
        """GET /students returns all rows as a list of {id, name, created_at}."""
        from aristotle.api import list_students_route

        rows = [
            ("id-1", "Definer", "2026-01-01T00:00:00Z"),
            ("id-2", "Sameer", "2026-07-12T00:00:00Z"),
        ]
        conn = _FakeConn(routes=[("FROM aristotle_student", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        result = await list_students_route(request)
        assert len(result) == 2
        assert result[0] == {"id": "id-1", "name": "Definer", "created_at": "2026-01-01T00:00:00Z"}
        assert result[1] == {"id": "id-2", "name": "Sameer", "created_at": "2026-07-12T00:00:00Z"}


class TestPlansRoute:
    @pytest.mark.asyncio
    async def test_get_plans_scopes_by_student_id(self):
        """GET /plans?student_id=X issues a WHERE student_id = ? query."""
        from aristotle.api import list_plans_route

        rows = [
            ("plan-1", "Pharmacognosy", "active", 0, '["c1","c2"]',
             "2026-07-12T00:00:00Z", None, "material-xyz"),
        ]
        conn = _FakeConn(routes=[("FROM aristotle_learning_plan", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(
            query_params={"student_id": "student-abc"},
            container=container,
        )

        result = await list_plans_route(request)
        assert len(result) == 1
        plan = result[0]
        assert plan["id"] == "plan-1"
        assert plan["subject"] == "Pharmacognosy"
        assert plan["status"] == "active"
        assert plan["current_concept_idx"] == 0
        assert plan["total_concepts"] == 2  # len(["c1","c2"])
        assert plan["material_id"] == "material-xyz"

        # Verify the SQL actually carried the WHERE clause.
        select_sqls = [
            sql for sql, _ in conn.executed
            if "FROM aristotle_learning_plan" in sql
        ]
        assert any("WHERE student_id = ?" in sql for sql in select_sqls), (
            f"expected WHERE student_id = ? in the plan query; saw {select_sqls}"
        )

    @pytest.mark.asyncio
    async def test_get_plans_defaults_student_id_to_definer(self):
        """GET /plans with no student_id defaults to 'definer' —
        preserves the pre-Task-18 single-tenant behavior."""
        from aristotle.api import list_plans_route

        conn = _FakeConn(routes=[("FROM aristotle_learning_plan", [])])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        await list_plans_route(request)

        # The query must have been issued with student_id='definer'.
        select_sqls_with_params = [
            (sql, params) for sql, params in conn.executed
            if "FROM aristotle_learning_plan" in sql
        ]
        assert select_sqls_with_params, "expected at least one plan SELECT"
        _, params = select_sqls_with_params[0]
        assert params == ("definer",), (
            f"plan query should have defaulted to 'definer'; got params={params}"
        )

    @pytest.mark.asyncio
    async def test_get_plans_handles_malformed_concept_ids_json(self):
        """GET /plans is robust to a corrupted concept_ids_json —
        total_concepts falls back to 0 rather than raising."""
        from aristotle.api import list_plans_route

        rows = [
            ("plan-1", "X", "active", 0, "not-valid-json",
             "2026-07-12T00:00:00Z", None, None),
        ]
        conn = _FakeConn(routes=[("FROM aristotle_learning_plan", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        result = await list_plans_route(request)
        assert len(result) == 1
        assert result[0]["total_concepts"] == 0


class TestConceptsRouteScoping:
    @pytest.mark.asyncio
    async def test_unscoped_call_returns_everything_and_logs_warning(self, caplog):
        """GET /concepts with no filters returns every concept (backwards
        compat) AND emits a `concepts_route_unscoped_call` warning so the
        unscoped usage is visible in logs."""
        from aristotle.api import list_concepts_route

        rows = [
            ("c1", "Inertia", None, 3, None, "plan-A", "mat-1"),
            ("c2", "Tangent spaces", None, 4, None, "plan-B", "mat-2"),
        ]
        conn = _FakeConn(routes=[("FROM aristotle_concept", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        # caplog captures via the root logger by default — make sure the
        # aristotle.api logger propagates so the warning reaches caplog.
        import aristotle.api as api_mod
        orig_propagate = api_mod.logger.propagate
        api_mod.logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="aristotle.api"):
                result = await list_concepts_route(request)
        finally:
            api_mod.logger.propagate = orig_propagate

        assert len(result) == 2
        # Both rows returned — unscoped.
        assert {r["id"] for r in result} == {"c1", "c2"}
        # New fields are present on each row.
        for r in result:
            assert "plan_id" in r
            assert "material_id" in r
        # Warning logged.
        assert any(
            "concepts_route_unscoped_call" in rec.message
            for rec in caplog.records
        ), "expected concepts_route_unscoped_call warning in logs"

    @pytest.mark.asyncio
    async def test_plan_id_filter_emits_no_warning(self, caplog):
        """GET /concepts?plan_id=X adds a WHERE clause and does NOT emit
        the unscoped warning."""
        from aristotle.api import list_concepts_route

        rows = [("c1", "Inertia", None, 3, None, "plan-A", "mat-1")]
        conn = _FakeConn(routes=[("FROM aristotle_concept", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(
            query_params={"plan_id": "plan-A"},
            container=container,
        )

        with caplog.at_level(logging.WARNING):
            result = await list_concepts_route(request)

        assert len(result) == 1
        assert result[0]["id"] == "c1"
        assert not any(
            "concepts_route_unscoped_call" in rec.message
            for rec in caplog.records
        ), "did not expect unscoped warning when plan_id filter is present"

        # Verify the SQL carried WHERE plan_id = ?
        select_sqls = [
            sql for sql, _ in conn.executed
            if "FROM aristotle_concept" in sql
        ]
        assert any("WHERE plan_id = ?" in sql for sql in select_sqls), (
            f"expected WHERE plan_id = ? in the concept query; saw {select_sqls}"
        )

    @pytest.mark.asyncio
    async def test_material_id_filter_emits_no_warning(self, caplog):
        """GET /concepts?material_id=X adds a WHERE clause and does NOT
        emit the unscoped warning."""
        from aristotle.api import list_concepts_route

        rows = [("c1", "Inertia", None, 3, None, "plan-A", "mat-1")]
        conn = _FakeConn(routes=[("FROM aristotle_concept", rows)])
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(
            query_params={"material_id": "mat-1"},
            container=container,
        )

        with caplog.at_level(logging.WARNING):
            result = await list_concepts_route(request)

        assert len(result) == 1
        assert not any(
            "concepts_route_unscoped_call" in rec.message
            for rec in caplog.records
        )

        select_sqls = [
            sql for sql, _ in conn.executed
            if "FROM aristotle_concept" in sql
        ]
        assert any("WHERE material_id = ?" in sql for sql in select_sqls)


class TestDashboardRouteScoping:
    @pytest.mark.asyncio
    async def test_unscoped_dashboard_logs_warning(self, caplog):
        """GET /dashboard with no plan_id logs an unscoped warning AND
        still returns results (backward compat)."""
        from aristotle.api import dashboard_route

        # Two concept rows (mastery lookup), one struggle pattern row.
        routes = [
            ("FROM aristotle_struggle_pattern", [("No struggles recorded yet.",)]),
            (
                "FROM aristotle_concept c",
                [
                    ("c1", "Inertia", 0, None, 0, None, None),
                    ("c2", "Tangent spaces", 0, None, 0, None, None),
                ],
            ),
        ]
        conn = _FakeConn(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        # Ensure aristotle.api logger propagates to caplog.
        import aristotle.api as api_mod
        orig_propagate = api_mod.logger.propagate
        api_mod.logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="aristotle.api"):
                result = await dashboard_route(request)
        finally:
            api_mod.logger.propagate = orig_propagate

        assert result["total_concepts"] == 2
        assert result["student_id"] == "definer"  # default
        assert result["plan_id"] is None  # unscoped
        assert any(
            "dashboard_route_unscoped_call" in rec.message
            for rec in caplog.records
        ), "expected dashboard_route_unscoped_call warning"

    @pytest.mark.asyncio
    async def test_scoped_dashboard_adds_where_plan_id(self, caplog):
        """GET /dashboard?plan_id=X scopes the concept join — no
        unscoped warning, and the SQL carries WHERE c.plan_id = ?."""
        from aristotle.api import dashboard_route

        routes = [
            ("FROM aristotle_struggle_pattern", [("No struggles recorded yet.",)]),
            (
                "FROM aristotle_concept c",
                [("c1", "Inertia", 0, None, 0, None, None)],
            ),
        ]
        conn = _FakeConn(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(
            query_params={"plan_id": "plan-A", "student_id": "student-abc"},
            container=container,
        )

        with caplog.at_level(logging.WARNING):
            result = await dashboard_route(request)

        assert result["plan_id"] == "plan-A"
        assert result["student_id"] == "student-abc"
        assert not any(
            "dashboard_route_unscoped_call" in rec.message
            for rec in caplog.records
        ), "did not expect unscoped warning when plan_id filter is present"

        # The concept query should have WHERE c.plan_id = ?
        concept_sqls = [
            sql for sql, _ in conn.executed
            if "FROM aristotle_concept c" in sql
        ]
        assert any("WHERE c.plan_id = ?" in sql for sql in concept_sqls), (
            f"expected WHERE c.plan_id = ? in the dashboard concept query; "
            f"saw {concept_sqls}"
        )

    @pytest.mark.asyncio
    async def test_dashboard_student_id_defaults_to_definer(self):
        """GET /dashboard with no student_id defaults to 'definer' —
        preserves pre-Task-18 behavior."""
        from aristotle.api import dashboard_route

        routes = [
            ("FROM aristotle_struggle_pattern", [("struggle text",)]),
            ("FROM aristotle_concept c", []),
        ]
        conn = _FakeConn(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        result = await dashboard_route(request)
        assert result["student_id"] == "definer"


class TestIntakeStartRouteStudentId:
    @pytest.mark.asyncio
    async def test_intake_start_passes_student_id_to_session(self):
        """POST /intake/start with student_id in the body sets it on the
        IntakeSession, so it flows through to generate_plan later."""
        from aristotle.api import intake_start_route
        from aristotle.actors.intake import IntakeTrigger, IntakeState

        # Mock check_intake_triggers to return a "full" trigger so the
        # route actually builds a session.
        async def _fake_check(ctx, plan_id):
            return IntakeTrigger(
                level="full",
                entry_state=IntakeState.GREETING,
                prompt=None,
            )

        # Mock run_intake_step so the route doesn't actually try to call
        # a model.
        async def _fake_run(session, student_input, ctx):
            return {"state": "GREETING", "prompt": "What subject?"}

        conn = _FakeConn()
        container = type(
            "C",
            (),
            {
                "model_provider": None,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            },
        )()
        request = _build_request(
            body={"student_id": "student-xyz"},
            container=container,
        )

        # Patch check_intake_triggers + run_intake_step where the route
        # imports them (aristotle.api).
        import aristotle.api as api_mod
        orig_check = api_mod.check_intake_triggers
        orig_run = api_mod.run_intake_step
        api_mod.check_intake_triggers = _fake_check
        api_mod.run_intake_step = _fake_run
        try:
            result = await intake_start_route(request)
        finally:
            api_mod.check_intake_triggers = orig_check
            api_mod.run_intake_step = orig_run

        assert result["session"]["student_id"] == "student-xyz", (
            f"expected student_id='student-xyz' on the session; "
            f"got {result['session'].get('student_id')}"
        )

    @pytest.mark.asyncio
    async def test_intake_start_defaults_student_id_to_definer(self):
        """POST /intake/start without student_id defaults to 'definer'."""
        from aristotle.api import intake_start_route
        from aristotle.actors.intake import IntakeTrigger, IntakeState

        async def _fake_check(ctx, plan_id):
            return IntakeTrigger(
                level="full",
                entry_state=IntakeState.GREETING,
                prompt="Welcome back",
            )

        conn = _FakeConn()
        container = type(
            "C",
            (),
            {
                "model_provider": None,
                "corpus_registry": _FakeRegistry(_FakeStores(conn)),
            },
        )()
        request = _build_request(body={}, container=container)

        import aristotle.api as api_mod
        orig_check = api_mod.check_intake_triggers
        api_mod.check_intake_triggers = _fake_check
        try:
            result = await intake_start_route(request)
        finally:
            api_mod.check_intake_triggers = orig_check

        # Trigger with a prompt returns early — session is in the response.
        assert result["session"]["student_id"] == "definer"


# ---------------------------------------------------------------------------
# Task 20: DELETE /aristotle/plans/{plan_id} — cascade + 404 + rollback
# ---------------------------------------------------------------------------


class _FakeCursorWithRowcount:
    """Cursor that supports rowcount for DELETE/UPDATE statements.

    The _FakeCursor above only supports fetchone/fetchall (SELECT). The
    DELETE route reads cur.rowcount after each DELETE to count cascade
    rows. This fake returns a configurable rowcount per execute() call.
    """

    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def close(self):
        pass


class _FakeConnWithRowcount:
    """Fake conn that supports DELETE rowcount + rollback tracking.

    Routes SQL by substring to (rows, rowcount) pairs. Tracks every
    execute() call + whether rollback() was called.
    """

    def __init__(
        self,
        routes: list[tuple[str, list[tuple], int]] | None = None,
    ):
        # routes: list of (sql_substring, rows_to_return, rowcount_for_delete)
        self._routes = routes or []
        self.executed: list[tuple[str, tuple]] = []
        self.rollback_called = False
        self.commit_called = False

    async def execute(self, sql: str, params: tuple = ()):
        self.executed.append((sql, params))
        for substring, rows, rowcount in self._routes:
            if substring.lower() in sql.lower():
                return _FakeCursorWithRowcount(rows=rows, rowcount=rowcount)
        return _FakeCursorWithRowcount(rows=[], rowcount=0)

    async def commit(self):
        self.commit_called = True

    async def rollback(self):
        self.rollback_called = True


class TestDeletePlanRoute:
    """Tests for DELETE /aristotle/plans/{plan_id} (Task 20)."""

    @pytest.mark.asyncio
    async def test_delete_returns_404_when_plan_not_found(self):
        """Unknown plan_id → 404, no DELETE statements issued."""
        from aristotle.api import delete_plan_route
        from fastapi import HTTPException

        # The existence-check SELECT returns no rows.
        conn = _FakeConnWithRowcount(
            routes=[("FROM aristotle_learning_plan WHERE id", [], 0)]
        )
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await delete_plan_route(request, "nonexistent-plan-id")
        assert exc_info.value.status_code == 404
        assert "plan not found" in exc_info.value.detail

        # Verify NO DELETE was issued (we should bail before cascade).
        delete_sqls = [sql for sql, _ in conn.executed if "DELETE FROM" in sql]
        assert delete_sqls == [], (
            f"no DELETE should run for unknown plan_id; saw {delete_sqls}"
        )

    @pytest.mark.asyncio
    async def test_delete_cascades_through_all_tables_in_order(self):
        """Known plan_id → DELETE in dependency order: placement →
        intake_session → mastery/predict/misconception (per concept) →
        concept → plan_job → learning_plan."""
        from aristotle.api import delete_plan_route

        # Plan exists (subject = 'Pharmacognosy'), has 2 concepts.
        # Each route is (sql_substring, rows, rowcount).
        routes = [
            # 0. existence check
            ("FROM aristotle_learning_plan WHERE id", [("Pharmacognosy",)], 1),
            # 1. placement_event delete (3 rows)
            ("DELETE FROM aristotle_placement_event", [], 3),
            # 2. intake_session delete (1 row)
            ("DELETE FROM aristotle_intake_session", [], 1),
            # 3. SELECT concept ids for this plan (2 concepts)
            ("SELECT id FROM aristotle_concept WHERE plan_id",
             [("c1",), ("c2",)], 0),
            # 4. mastery delete (2 rows)
            ("DELETE FROM aristotle_mastery", [], 2),
            # 5. predict_event delete (1 row)
            ("DELETE FROM aristotle_predict_event", [], 1),
            # 6. misconception_log delete (0 rows)
            ("DELETE FROM aristotle_misconception_log", [], 0),
            # 7. concept delete (2 rows)
            ("DELETE FROM aristotle_concept", [], 2),
            # 8. plan_job delete (1 row)
            ("DELETE FROM aristotle_plan_job", [], 1),
            # 9. learning_plan delete (1 row — the plan itself)
            ("DELETE FROM aristotle_learning_plan WHERE id", [], 1),
        ]
        conn = _FakeConnWithRowcount(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        result = await delete_plan_route(request, "plan-to-delete")

        assert result["deleted"] is True
        assert result["plan_id"] == "plan-to-delete"
        assert result["subject"] == "Pharmacognosy"
        assert result["concepts_deleted"] == 2
        # cascade = placement(3) + intake(1) + mastery(2) + predict(1) +
        # misconception(0) + plan_job(1) = 8 (concept rows counted
        # separately; plan row not counted)
        assert result["cascade_rows_deleted"] == 8

        # Verify the DELETE statements were issued in the right order.
        delete_sqls = [sql for sql, _ in conn.executed if "DELETE FROM" in sql]
        # 8 DELETE statements (placement, intake, mastery, predict,
        # misconception, concept, plan_job, learning_plan).
        assert len(delete_sqls) == 8, (
            f"expected 8 DELETE statements; saw {len(delete_sqls)}: {delete_sqls}"
        )
        # Verify ordering by table name.
        tables_deleted_in_order = []
        for sql in delete_sqls:
            # Extract table name from "DELETE FROM <table>"
            after = sql.split("DELETE FROM", 1)[1].strip()
            table = after.split()[0]
            tables_deleted_in_order.append(table)
        assert tables_deleted_in_order == [
            "aristotle_placement_event",
            "aristotle_intake_session",
            "aristotle_mastery",
            "aristotle_predict_event",
            "aristotle_misconception_log",
            "aristotle_concept",
            "aristotle_plan_job",
            "aristotle_learning_plan",
        ], f"DELETE order wrong: {tables_deleted_in_order}"

        # Commit should have been called (transaction committed).
        assert conn.commit_called is True
        # Rollback should NOT have been called (no failure).
        assert conn.rollback_called is False

    @pytest.mark.asyncio
    async def test_delete_does_not_touch_uploaded_material(self):
        """The cascade must NOT delete aristotle_uploaded_material —
        a material may be shared or re-used by another plan. Deleting
        a plan should not delete the source material it was built from."""
        from aristotle.api import delete_plan_route

        routes = [
            ("FROM aristotle_learning_plan WHERE id", [("Physics",)], 1),
            ("DELETE FROM aristotle_placement_event", [], 0),
            ("DELETE FROM aristotle_intake_session", [], 0),
            ("SELECT id FROM aristotle_concept WHERE plan_id", [], 0),
            ("DELETE FROM aristotle_plan_job", [], 0),
            ("DELETE FROM aristotle_learning_plan WHERE id", [], 1),
        ]
        conn = _FakeConnWithRowcount(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        await delete_plan_route(request, "plan-1")

        # Verify NO DELETE was issued against aristotle_uploaded_material.
        material_deletes = [
            sql for sql, _ in conn.executed
            if "DELETE FROM aristotle_uploaded_material" in sql
        ]
        assert material_deletes == [], (
            "DELETE must not touch aristotle_uploaded_material — "
            "material may be shared/re-used by another plan"
        )

    @pytest.mark.asyncio
    async def test_delete_rolls_back_on_mid_cascade_failure(self):
        """If any DELETE raises, the transaction rolls back and the
        route returns 500. No partial delete should be visible to the
        caller."""
        from aristotle.api import delete_plan_route
        from fastapi import HTTPException

        class _FailingConn(_FakeConnWithRowcount):
            async def execute(self, sql: str, params: tuple = ()):
                self.executed.append((sql, params))
                # Fail on the mastery DELETE (mid-cascade).
                if "DELETE FROM aristotle_mastery" in sql:
                    raise RuntimeError("simulated mid-cascade failure")
                for substring, rows, rowcount in self._routes:
                    if substring.lower() in sql.lower():
                        return _FakeCursorWithRowcount(rows=rows, rowcount=rowcount)
                return _FakeCursorWithRowcount(rows=[], rowcount=0)

        routes = [
            ("FROM aristotle_learning_plan WHERE id", [("Pharmacy",)], 1),
            ("DELETE FROM aristotle_placement_event", [], 1),
            ("DELETE FROM aristotle_intake_session", [], 1),
            ("SELECT id FROM aristotle_concept WHERE plan_id",
             [("c1",)], 0),
            ("DELETE FROM aristotle_mastery", [], 1),  # will raise
        ]
        conn = _FailingConn(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await delete_plan_route(request, "plan-1")
        assert exc_info.value.status_code == 500
        assert "rolled back" in exc_info.value.detail

        # Rollback MUST have been called.
        assert conn.rollback_called is True, (
            "rollback must be called on mid-cascade failure so partial "
            "deletes don't persist"
        )
        # Commit must NOT have been called.
        assert conn.commit_called is False

    @pytest.mark.asyncio
    async def test_delete_with_no_concepts_still_succeeds(self):
        """A plan with zero concepts (e.g. one where intake completed
        but plan_generator hasn't created concepts yet) still deletes
        cleanly — the concept-keyed child deletes are skipped, cascade
        count is 0 for those tables."""
        from aristotle.api import delete_plan_route

        routes = [
            ("FROM aristotle_learning_plan WHERE id", [("Empty",)], 1),
            ("DELETE FROM aristotle_placement_event", [], 0),
            ("DELETE FROM aristotle_intake_session", [], 0),
            # SELECT concept ids returns empty.
            ("SELECT id FROM aristotle_concept WHERE plan_id", [], 0),
            ("DELETE FROM aristotle_plan_job", [], 0),
            ("DELETE FROM aristotle_learning_plan WHERE id", [], 1),
        ]
        conn = _FakeConnWithRowcount(routes=routes)
        container = type(
            "C",
            (),
            {"corpus_registry": _FakeRegistry(_FakeStores(conn))},
        )()
        request = _build_request(container=container)

        result = await delete_plan_route(request, "empty-plan")

        assert result["deleted"] is True
        assert result["concepts_deleted"] == 0
        assert result["cascade_rows_deleted"] == 0

        # Verify the concept-keyed child DELETEs were SKIPPED (no
        # concepts → no IN clause → no mastery/predict/misconception
        # deletes).
        for table in ["aristotle_mastery", "aristotle_predict_event",
                      "aristotle_misconception_log", "aristotle_concept"]:
            table_deletes = [
                sql for sql, _ in conn.executed
                if f"DELETE FROM {table}" in sql
            ]
            assert table_deletes == [], (
                f"{table} DELETE should be skipped when plan has no "
                f"concepts; saw {table_deletes}"
            )
