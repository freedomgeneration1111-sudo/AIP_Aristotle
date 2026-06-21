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

**Status:** Resolved — fixed 2026-06-19 (Option 1, platform-side)
**Phase:** Tutoring loop / CI mode
**Filed:** 2026-06-19

**What was broken:**
The session coordinator (`aristotle/session.py::_step_evaluate`) expects
EXAMINER's `evaluate()` to return JSON `{score, mastery_achieved, feedback}`,
which it parses with `json.loads(session.last_evaluation)`. In CI mode,
however, the platform's `ModelSlotResolver` returned a plain-text fixture
like `[CI-FIXTURE for evaluation] Concept: Newton's First Law...` for
the `evaluation` slot. `json.loads` raised `JSONDecodeError`, the
coordinator caught it and defaulted to `score=0.0, mastered=False`, and
the learner could never reach the mastery threshold — every session
exhausted `max_retries=2` and exited with `mastered=False`.

Observed during dogfood (2026-06-19): `aristotle session newton_first_law
--answer "objects resist changes in motion"` produced a 14-step session
with three EVALUATE attempts, all parsing as score=0.0, ending
`Mastered: False, Final score: 0.0`. The struggle-pattern table also
filled with concatenated `[CI-FIXTURE for sexton]` prefixes because
MENTOR kept re-reading the prior pattern and prepending the fixture.

**Resolution (Option 1 — platform-side, chosen as cleanest):**
Fixed in `AIP_Brain/src/aip/adapter/model_slot_resolver.py` (commit on
`feat/multi-corpus`). When `ci_mode=True` and `slot_name == "evaluation"`,
the resolver now returns a JSON string:
`{"score": 0.9, "mastery_achieved": true, "feedback": "[CI-FIXTURE...]"}`.
The high score + `mastery_achieved=True` lets CI-mode dogfood runs
exercise the `mastered=True` branch (TEACH → PROBE → QUIZ → EVALUATE →
NEXT_CONCEPT → SESSION_COMPLETE in 6 steps, not 14). Other slots
(`synthesis`, `beast`, `sexton`, `embedding`) keep the plain-text
fixture — their callers don't have a JSON contract (or, like Sexton and
Beast, they have robust find-first-bracket JSON extraction that handles
plain text gracefully).

**Why Option 1 over Options 2/3:**
- Option 2 (ExaminerActor detects ci_mode and synthesizes JSON) would
  couple the extension to a platform-specific runtime flag — exactly
  the kind of import-boundary violation ADR-014 §5.3 forbids.
- Option 3 (treat non-JSON as soft pass, score=0.5) papers over the
  root cause and still wouldn't let CI mode exercise the mastered=True
  branch (0.5 < 0.7 mastery_threshold).
- Option 1 fixes the root cause: the resolver was producing
  wrong-shaped output for a slot with a documented JSON contract. The
  fix is one branch in `ModelSlotResolver.call()`, fully testable from
  the platform side without coupling.

**Why this slot specifically:**
The `evaluation` slot is the only slot with a strict `json.loads()`
caller that has no fallback extraction logic. Sexton and Beast parse
JSON too, but they use a robust extractor (`_extract_json_array` in
`sexton.py` lines 77-118) that finds the first `[` and last `]` —
plain-text fixtures don't break them. If a future actor adds another
strict-JSON slot, the same pattern (slot-specific fixture in
`ModelSlotResolver.call()`) should be extended.

**Verification:**
- Aristotle full test suite: 54 passed, 3 warnings (no regressions —
  the unit tests use `_FakeModelProvider` with explicit JSON responses,
  so they don't exercise the real resolver path).
- Brain `tests/test_model_slot_resolver.py`: 25 passed (the existing CI
  fixture tests use the `synthesis` slot, which still returns plain
  text — backward compat preserved).
- End-to-end: wrote `/home/z/my-project/scripts/verify_debt010_fix.py`
  which calls the real resolver in CI mode for the `evaluation` slot
  and confirms the output parses as JSON with `score=0.9,
  mastery_achieved=True`. Other slots confirmed to still return plain
  text (backward compat).

**Related work:**
- `AIP_Brain/src/aip/adapter/model_slot_resolver.py` (the fix — slot-specific CI fixture, ~line 328)
- `aristotle/session.py:_step_evaluate` (the `json.loads` + fallback — unchanged)
- `aristotle/actors/examiner.py` (evaluate prompt — unchanged, still enforces JSON contract for real LLM)
- `tests/test_aristotle_tutoring.py` (uses `_FakeModelProvider` stub — masks this in unit tests; recommend adding an integration test that exercises the real resolver in CI mode)
- `/home/z/my-project/scripts/verify_debt010_fix.py` (end-to-end verification script)

---

## ARISTOTLE-DEBT-011 — probe() error-as-payload migration (side-effect resolved)

**Status:** Resolved — migrated as a side effect of TASK 2 (commit 0352708)
**Scope:** `aristotle/actors/examiner.py` probe() + `aristotle/session.py` _step_probe
**Filed:** 2026-06-19

**Original issue (as filed):**
`probe()` used the error-as-payload pattern
(`ActorResult(ok=True, error=question_text)`). The session coordinator
read `result.error` in `_step_probe`. This was inconsistent with the
migrated actors (evaluate(), teach(), quiz() all use `ActorResult.data`).

**Resolution (side effect):**
TASK 2 (B.5 item 6 — transfer questions) migrated `_generate_question()`
— the shared helper used by BOTH `probe()` and `quiz()` — from
`error=question` to `data={"question": ..., "question_type": ...}`. This
migrated `probe()` automatically. `_step_probe` in session.py was updated
to read `result.data` (with a backward-compat fallback to `result.error`).

**Why this was filed as Open but resolved as a side effect:**
The original TASK 3 spec assumed `probe()` would NOT be migrated in
TASK 2. In practice, `probe()` and `quiz()` share `_generate_question()`,
so migrating `quiz()` necessarily migrated `probe()` too. The migration
is correct — there's no reason to keep `probe()` on error-as-payload when
its sibling `quiz()` uses `data=`. The debt entry is preserved for
traceability + to document the side-effect resolution.

**Current state:**
All four ARISTOTLE actors now use `ActorResult.data`:
  - `evaluate()` → `data={score, mastery_achieved, feedback, diagnosis}`
  - `teach()` → `data={explanation, fading_mode}`
  - `quiz()` → `data={question, question_type}`
  - `probe()` → `data={question, question_type}` (via shared _generate_question)
  - `predict()` → `data={prompt}` (was already on data= from the PREDICT commit)
  - `generate_hint()` → `data={hint}` (was already on data= from the HINT ladder commit)
  - `log_misconception()` → `data={logged, ...}` (was already on data= from the misconception-log commit)

The error-as-payload pattern is now fully eliminated from ARISTOTLE. The
platform-wide soft-deprecation (Brain DEBT-015) is on track.

**Related work:**
- `aristotle/actors/examiner.py:_generate_question()` (the shared helper — migrated in commit 0352708)
- `aristotle/session.py:_step_probe()` (reads result.data with backward-compat fallback)
- `tests/test_aristotle_tutoring.py::test_probe_calls_evaluation_slot` (updated to read result.data)
- Brain DEBT-015 (platform-wide ActorResult.data field — the root enabler)

## ARISTOTLE-DEBT-009 — Unbuilt API Routes (Tests Written Ahead of Implementation)

**Status:** Active — Phase B deferred
**Phase:** Phase B / Phase D
**Filed:** 2026-06-18

**What was deferred:**
9 API routes have tests written but no implementation in `aristotle/api.py`.
The function definitions do not exist anywhere in the codebase. Tests from
another session were written ahead of the implementation.

Missing routes (by function name):
1. `intake_start_route` — Phase D onboarding intake (test_aristotle_intake.py)
2. `intake_step_route` — Phase D onboarding intake step (test_aristotle_intake.py)
3. `placer_start_route` — Phase D placement assessment (test_aristotle_intake.py)
4. `placer_step_route` — Phase D placement step (test_aristotle_intake.py)
5. `session_history_route` — teacher dashboard session history (test_teacher_dashboard.py)
6. `misconceptions_route` — misconceptions list for dashboard (test_aristotle_routes.py)
7. `get_settings_route` — extension settings read (test_aristotle_routes.py)
8. `update_settings_route` — extension settings write (test_aristotle_routes.py)
9. `upload_route` — file upload (PDF, image, txt, html, json) (test_aristotle_routes.py, 6 tests)

**Why deferred:**
These routes were planned by another session (Phase B.5 / Phase D) but the
implementations were never committed. The tests are correct specifications
for the intended behavior — they just can't import the function.

**Resolution:**
Tests marked `@pytest.mark.xfail(reason="Phase B/D — route not yet implemented")`
so the suite reads clean (0 unexpected failures) without hiding the gap.
When each route is implemented, remove the xfail marker — the test will
either pass (implementation correct) or fail (implementation needs work).

**Remediation trigger:**
When implementing each Phase B/D route, remove the xfail marker and verify
the test passes.

**Related work:**
- `tests/test_aristotle_intake.py` (4 tests — intake + placer routes)
- `tests/test_aristotle_routes.py` (9 tests — misconceptions, settings, upload)
- `tests/test_teacher_dashboard.py` (2 tests — session history)
- `aristotle/api.py` (where the routes need to be added)

---
