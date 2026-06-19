# AIP_Aristotle Technical Debt Register

**Owner:** B. Moses Jorgensen
**Last Updated:** 2026-06-18 (initial creation — seeded with Phase A debt)

Each entry records a deliberate deferral — what was skipped, why, and what triggers remediation.

---

## ARISTOTLE-DEBT-001 — Progress Tables in aristotle:textbook, Not Definer

**Status:** Active — revisit at Phase B (teacher dashboard)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
ADR-014 §1 (the platform's settled decisions) says progress tables go in
the `definer` corpus with `aristotle_*` naming. But the migration_loader
(step 1 of the platform build) applies extension migrations to the
extension's OWN contributed corpus (`aristotle:textbook`), not to the
definer corpus. For pre-alpha dogfood, per-corpus is simpler and matches
the loader's behavior.

The `aristotle_concept` and `aristotle_struggle_pattern` tables are in
`aristotle:textbook`, not `definer`.

**Why deferred:**
The migration_loader's behavior (apply to the extension's first declared
corpus) is simpler than targeting the definer corpus, which would require
cross-corpus migration targeting. Pre-alpha single-tenant doesn't need
cross-corpus aggregation. The `aristotle_*` naming convention is preserved
either way, so the tables are identifiable.

**Remediation trigger:**
Phase B (teacher dashboard) — when Komal needs cross-student aggregation
(mastery heatmaps, what's due across the class), the progress tables need
to be in a shared corpus (definer) or a dedicated progress corpus. At that
point, either:
1. Move the tables to `definer` (requires a migration + the platform
   gaining cross-corpus migration targeting), OR
2. Add a dedicated `aristotle:progress` corpus (cleaner separation; the
   manifest declares a second corpus).

**Related work:**
- ADR-014 §1 (platform — progress store location decision)
- `aristotle/migrations/M001_aristotle.sql` (the tables themselves)
- `AIP_Brain/src/aip/adapter/extensions/loaders/migration_loader.py` (the loader)

---

## ARISTOTLE-DEBT-002 — Actors Are Placeholders (No Real Model Calls)

**Status:** Active — Near-Term (Phase A completion)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
SOCRATES, EXAMINER, and MENTOR conform to the Actor Protocol and verify
platform reachability (corpus, model provider, struggle_pattern table),
but they don't do real teaching/probing/mentoring. SOCRATES doesn't
generate explanations. EXAMINER doesn't generate/score questions. MENTOR
doesn't write AI-diagnostic struggle_pattern sentences.

**Why deferred:**
The dogfood goal was to prove the platform contract end-to-end (manifest
validates, migration applies, actor registers, scheduler runs, health
surfaces). Real model calls require a configured model provider, which
is an operational concern (API keys) not a contract concern. The actors
are structured to accept a model provider via `ctx.container.model_provider`
— the tutoring loop just needs to call it.

**Remediation trigger:**
Near-Term (Phase A completion) — once a model provider is configured on
the container, the actors gain real model calls. This is tracked in
`PLANNED_FEATURES.md` Near-Term section.

**Related work:**
- `aristotle/actors/socrates.py` (verifies corpus, doesn't generate)
- `aristotle/actors/examiner.py` (verifies model availability, doesn't generate/score)
- `aristotle/actors/mentor.py` (reads/initializes struggle_pattern, doesn't write AI diagnostics)
- ADR-001 §2 (the five modes + their roles)

---

## ARISTOTLE-DEBT-003 — Workflow Script Handlers Not Registered

**Status:** Active — Near-Term (Phase A completion)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
The `tutoring_session_v1.yaml` workflow declares two `script` nodes:
`evaluate` (run: `aristotle_evaluate`) and `next_concept` (run:
`aristotle_next_concept`). These script handlers are NOT registered with
the workflow engine. The engine runs them in fixture/no-op mode.

**Why deferred:**
The workflow YAML is engine-compatible (the L5 loader parses it without
errors), which was the dogfood goal. Registering real script handlers
requires understanding the engine's script-handler registration mechanism
(`ScriptNode` + `script_fixture_mode`), which is a platform-side concern.
The handlers themselves (update mastery, consult prerequisite DAG, update
struggle_pattern) depend on real model calls (ARISTOTLE-DEBT-002).

**Remediation trigger:**
Near-Term (Phase A completion) — after real model calls land, the script
handlers can be registered. Tracked in `PLANNED_FEATURES.md` Near-Term.

**Related work:**
- `aristotle/workflows/tutoring_session_v1.yaml` (the workflow declaring the script nodes)
- `AIP_Brain/src/aip/orchestration/workflow/loader.py` (the loader — script_fixture_mode default)
- `AIP_Brain/src/aip/orchestration/workflow/node.py` (ScriptNode)

---

## ARISTOTLE-DEBT-004 — Single-Tenant student_id (Pre-Alpha)

**Status:** Active — by design, pre-alpha
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
The `aristotle_struggle_pattern` table has a `student_id` column (PK), but
MENTOR hardcodes `student_id = "definer"` (pre-alpha single-tenant). There's
no multi-student session routing.

**Why deferred:**
ADR-014 §1 (platform settled decisions): "One install per learner (pre-
alpha). Multi-tenant is the deferred enterprise version." The student_id
column is in place so the tenant dimension can be added later without a
rewrite. The stable PK is the forward-compatible hedge.

**Remediation trigger:**
When ARISTOTLE moves from one-install-per-learner to multi-student (post-
alpha). At that point, MENTOR reads `student_id` from the session context
(`ctx.container` session metadata) instead of hardcoding `"definer"`.

**Related work:**
- ADR-014 §1 (platform — tenancy decision)
- `aristotle/actors/mentor.py` (hardcoded student_id)
- `aristotle/migrations/M001_aristotle.sql` (the student_id column)

---

---

## ARISTOTLE-DEBT-005 — Platform Gap: ScriptNode Disabled in Production

**Status:** Active — blocked by platform
**Phase:** Phase A
**Filed:** 2026-06-18

**What was deferred:**
The tutoring state machine workflow (`tutoring_session_v1.yaml`) declares two
`script` nodes: `evaluate` (run: `aristotle_evaluate`) and `next_concept`
(run: `aristotle_next_concept`). The platform's `ScriptNode` (in
`AIP_Brain/src/aip/orchestration/workflow/node.py:80`) is **hard-disabled in
production** — it returns `success=False` with a DISABLED error. Only fixture
mode works (no-op). The engine doesn't support registering real script handlers.

This means the workflow can't drive the tutoring state machine. The `evaluate`
and `next_concept` nodes would fail in production.

**Why deferred:**
This is a platform gap, not an ARISTOTLE gap. The platform's ScriptNode
intentionally disables arbitrary code execution for safety (no sandbox). The
fix would be either:
1. The platform adds a registered-script-handler mechanism (safe named handlers,
   not arbitrary code), OR
2. ARISTOTLE converts the script nodes to agent nodes (model calls), which
   works but means the deterministic logic (scoring, DAG traversal) goes
   through a model — wrong for deterministic operations.

For Phase A, ARISTOTLE uses an **actor-driven** approach instead: the actors
(SOCRATES, EXAMINER, MENTOR) expose public tutoring methods (`teach()`,
`probe()`, `quiz()`, `evaluate()`, `update_struggle_pattern()`) that a session
coordinator calls directly. The workflow YAML stays as documentation of the
state machine; execution is actor-driven.

**Remediation trigger:**
When the platform adds a registered-script-handler mechanism to ScriptNode
(safe named handlers), ARISTOTLE can register `aristotle_evaluate` and
`aristotle_next_concept` and the workflow becomes executable. Until then,
the actor-driven approach works and is arguably cleaner (the state machine
lives in code, not YAML).

**Related work:**
- `AIP_Brain/src/aip/orchestration/workflow/node.py:80` (ScriptNode — disabled)
- `aristotle/workflows/tutoring_session_v1.yaml` (the workflow declaring script nodes)
- `aristotle/actors/{socrates,examiner,mentor}.py` (the actor-driven approach)
- ADR-001 §9 ("If anything here forces a reach into core internals, that is a
  Phase 0 gap to log")


---

## ARISTOTLE-DEBT-006 — Platform Gap: Vigil Has No SM-2 Spaced Repetition

**Status:** Active — blocked by platform
**Phase:** Phase A
**Filed:** 2026-06-18

**What was deferred:**
ADR-001 §2 says "VIGIL is reused from core. SM-2; decides what comes due
and when." But the platform's Vigil actor
(`AIP_Brain/src/aip/orchestration/actors/vigil.py`) is a **quality evaluation
actor** (faithfulness, consistency, source grounding, canonical drift
detection), NOT a spaced repetition scheduler. It has no SM-2 methods.

This means ARISTOTLE can't reuse VIGIL for spaced repetition. The
`review_interval_seconds` config field (originally intended to be passed
to VIGIL) is unused.

**Why deferred:**
This is a platform gap, not an ARISTOTLE gap. The fix would be either:
1. The platform adds SM-2 to Vigil (or a new dedicated spaced-repetition
   actor), OR
2. ARISTOTLE implements SM-2 directly.

For Phase A, option 2 is the pragmatic fix: SM-2 is ~20 lines of Python
(`aristotle/sm2.py`) and doesn't belong in the platform anyway (it's
pedagogy-specific, not platform infrastructure). The `aristotle_mastery`
table (M002 migration) stores the SM-2 state per (student, concept) pair.

**Remediation trigger:**
If the platform later adds a spaced-repetition actor (SM-2 or otherwise),
ARISTOTLE can migrate to reuse it. Until then, the local SM-2
implementation works and is cleaner — ARISTOTLE owns its pedagogy.

**Related work:**
- `AIP_Brain/src/aip/orchestration/actors/vigil.py` (the actual Vigil — quality eval, not SM-2)
- `aristotle/sm2.py` (the local SM-2 implementation)
- `aristotle/migrations/M002_aristotle_mastery.sql` (the mastery table)
- ADR-001 §2 (the original assumption that VIGIL had SM-2)
- ADR-001 §9 ("If anything here forces a reach into core internals, that is
  a Phase 0 gap to log")


---

## ARISTOTLE-DEBT-007 — SOCRATES Uses Raw SQL, Not Brain's Retrieval Pipeline

**Status:** Active — Phase A pragmatism, revisit at Phase B/C
**Phase:** Phase A
**Filed:** 2026-06-19

**What was deferred:**
SOCRATES.teach() fetches concept content from `aristotle_concept` via raw SQL
on the corpus write connection (`stores.connection_manager.write_conn.execute()`).
It does NOT use the Brain's `assemble_augmented_context` or the hybrid FTS5+vector
retrieval pipeline. The same is true for EXAMINER's `_fetch_concept()` helper.

**Why deferred:**
Phase A dogfood goal was to prove the tutoring loop end-to-end. Raw SQL is simpler
and works — the concept table is small and the query is a primary-key lookup. The
Brain's retrieval pipeline (FTS5 + vector + graph + RRF fusion) is designed for
fuzzy retrieval across a large corpus, not for exact concept lookups.

**Remediation trigger:**
Phase B/C — when SOCRATES needs to:
1. Pull alternate framings from the textbook corpus (requires fuzzy retrieval, not PK lookup)
2. Blend textbook content with HERALD's live news (requires cross-corpus retrieval)
3. Use the graph store for prerequisite DAG traversal (requires graph retrieval)

At that point, SOCRATES should call `ctx.container._search_sources_fn` or equivalent
to use the Brain's retrieval pipeline, not raw SQL.

**Related work:**
- `aristotle/actors/socrates.py::_fetch_concept()` (raw SQL)
- `aristotle/actors/examiner.py::_fetch_concept()` (same pattern)
- `AIP_Brain/src/aip/adapter/api/routes/_augmented_context.py` (the Brain's retrieval pipeline)
- ADR-001 §4 (concept-aware retrieval via the graph store)


---

## ARISTOTLE-DEBT-008 — GUI Coupling to Brain's gui/ Package

**Status:** Active — revisit when third-party extensions need the same components
**Phase:** Phase B
**Filed:** 2026-06-18

**What was deferred:**
`aristotle/gui.py` imports from the Brain's `gui/` package:
- `gui.components.layout` (`build_top_bar`, `build_left_nav`)
- `gui.state` (`GuiState`)
- `gui.theme` (color + font constants: `C_AMBER`, `C_CREAM`, `C_SURFACE`, etc.)

This is tight GUI coupling — the extension depends on the platform's GUI
package, not just its API. It works because ARISTOTLE is installed in the
same venv as the Brain and both run as the same NiceGUI app. But it means:
1. ARISTOTLE can't render its GUI without the Brain's gui/ package present.
2. A third-party extension that doesn't know about the Brain's theme
   constants can't reuse the layout components.
3. The boundary test (which scans for `aip.*` imports) doesn't catch this
   because `gui.*` is not an `aip.*` import — it's a separate top-level
   package.

**Why deferred:**
For pre-alpha single-tenant (one install, one learner), the coupling is
acceptable — ARISTOTLE is always co-installed with the Brain. Fixing it
requires either:
1. The Brain exports its theme/layout as a pip-installable package
   (e.g. `aip-gui-kit`), OR
2. Extensions ship their own theme/layout, OR
3. The Brain's gui/ package becomes an `aip.adapter.extensions.gui_kit`
   module that extensions import through the allowlist.

Option 1 is the cleanest for a multi-extension future. Options 2 and 3
are simpler but either duplicate theme constants or expand the boundary.

**Remediation trigger:**
When a second extension (LOOM, CodeForge, or a third-party extension)
needs to reuse the Brain's layout components, extract the theme +
layout into a shared package. Until then, the coupling is a known
pre-alpha trade-off.

**Related work:**
- `aristotle/gui.py` (imports `gui.components.layout`, `gui.state`, `gui.theme`)
- `AIP_Brain/gui/components/layout.py` (the layout module)
- `AIP_Brain/gui/theme.py` (the theme constants)
- `tests/test_import_boundary.py` (doesn't catch this — `gui.*` isn't `aip.*`)

---

## ARISTOTLE-DEBT-009 — CLI `health` hit wrong URL (404 against real server)

**Status:** Resolved — fixed 2026-06-19 during dogfood
**Phase:** CLI
**Filed:** 2026-06-19

**What was broken:**
`aristotle/cli.py:78` called `client.get("/health/extensions")`, but the
Brain platform mounts the health router with `prefix="/api/v1"` — so the
real URL is `/api/v1/health/extensions`. Running `aristotle health`
against a live Brain server returned `404 Not Found`, and the CLI
printed `ERROR: Client error '404 Not Found' for url
'http://127.0.0.1:8000/health/extensions'` and exited 1.

The other CLI commands (`ingest`, `list-concepts`, `session`) worked
because they hit `/aristotle/*` (no `/api/v1` prefix — Aristotle's
router is mounted without a prefix).

The 8 CLI tests in `tests/test_aristotle_cli_api.py` didn't catch this
because they `patch("aristotle.cli._client")` with a MagicMock — the
mock returns a 200-style response regardless of which URL the CLI
actually requests. There is no integration test that exercises the real
URL path against a real (or test-client) server.

**Resolution:**
One-line fix: `client.get("/health/extensions")` → `client.get("/api/v1/health/extensions")`.

**Recommended follow-up:**
Add an integration test that uses FastAPI's `TestClient` against the
real `create_app()` to verify each CLI URL resolves (no mock). This
would also have caught the platform-side bug
(AIP_BRAIN DEBT-013) where Aristotle's routers weren't mounted at all.

**Related work:**
- `aristotle/cli.py:78` (the URL)
- `tests/test_aristotle_cli_api.py:312` (mocks `_client`, hides URL bugs)

---

## ARISTOTLE-DEBT-010 — EXAMINER CI-fixture breaks EVALUATE JSON parse (student always fails in CI mode)

**Status:** Open
**Phase:** Tutoring loop / CI mode
**Filed:** 2026-06-19

**What's broken:**
The session coordinator (`aristotle/session.py::_step_evaluate`) expects
EXAMINER's `evaluate()` to return JSON `{score, mastery_achieved, feedback}`,
which it parses with `json.loads(session.last_evaluation)`. In CI mode,
however, the platform's `ModelSlotResolver` returns a plain-text fixture
like `[CI-FIXTURE for evaluation] Concept: Newton's First Law...` for
the `evaluation` slot. `json.loads` raises `JSONDecodeError`, the
coordinator catches it and defaults to `score=0.0, mastered=False`, and
the learner can never reach the mastery threshold — every session
exhausts `max_retries=2` and exits with `mastered=False`.

Observed during dogfood (2026-06-19): `aristotle session newton_first_law
--answer "objects resist changes in motion"` produced a 14-step session
with three EVALUATE attempts, all parsing as score=0.0, ending
`Mastered: False, Final score: 0.0`. The struggle-pattern table also
fills with concatenated `[CI-FIXTURE for sexton]` prefixes because
MENTOR keeps re-reading the prior pattern and prepending the fixture.

**Why deferred:**
In production (real LLM), EXAMINER is prompted to return strict JSON and
this works. The bug only manifests in CI mode, where it's "expected"
that fixtures don't drive real learning — but it makes the dogfood
smoke test misleading (the loop runs, but the learner can never succeed).

**Recommended fix (one of):**
1. Have `ModelSlotResolver` return valid JSON for the `evaluation` slot
   when `ci_mode=True` (e.g. `{"score": 0.8, "mastery_achieved": true,
   "feedback": "[CI-FIXTURE]"}`). This is the cleanest fix because it
   makes CI-mode sessions actually exercise the mastered=True branch.
2. Have `ExaminerActor.evaluate()` detect `ci_mode` and synthesize a
   valid JSON response itself, bypassing the model call.
3. Have `_step_evaluate` treat a non-JSON response as a soft pass
   (score=0.5, mastered=False) rather than a hard zero — at least the
   learner wouldn't always fail.

Option 1 is preferred because it fixes the root cause and lets CI mode
verify the mastered branch end-to-end.

**Related work:**
- `aristotle/session.py:_step_evaluate` (the `json.loads` + fallback)
- `aristotle/actors/examiner.py` (evaluate prompt — should enforce JSON)
- `AIP_Brain/src/aip/adapter/model_slot_resolver.py` (CI fixture format)
- `tests/test_aristotle_tutoring.py` — likely uses a stub that returns
  valid JSON directly, masking this in unit tests.
