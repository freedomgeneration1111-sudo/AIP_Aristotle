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

**Status:** Partially resolved — 4 of 9 routes wired (commit baf6ef2); 5 still deferred
**Phase:** Phase B / Phase D
**Filed:** 2026-06-18
**Last update:** 2026-06-26 (4 routes wired)

**Resolved (4 routes, xfail markers removed):**
1. `intake_start_route` — Phase D onboarding intake (commit baf6ef2)
2. `intake_step_route` — Phase D onboarding intake step (commit baf6ef2)
3. `placer_start_route` — Phase D placement assessment (commit baf6ef2)
4. `placer_step_route` — Phase D placement step (commit baf6ef2)

These 4 routes were wired as thin FastAPI wrappers around the existing
actor logic (IntakeActor, run_intake_step, run_placer_step,
check_intake_triggers, _detect_intake_intent,
_sample_concepts_for_placement) + serialization helpers
(intake_session_to_dict/from_dict, placer_session_to_dict/from_dict).
Tests in `tests/test_aristotle_intake.py` now pass without xfail.

**Still deferred (5 routes, tests remain xfail):**
5. `session_history_route` — teacher dashboard session history (test_teacher_dashboard.py)
6. `misconceptions_route` — misconceptions list for dashboard (test_aristotle_routes.py)
7. `get_settings_route` — extension settings read (test_aristotle_routes.py)
8. `update_settings_route` — extension settings write (test_aristotle_routes.py)
9. `upload_route` — file upload (PDF, image, txt, html, json) (test_aristotle_routes.py, 6 tests)

**Why the remaining 5 are deferred:**
These routes need NEW behavior — not just wrapping existing actors.
`upload_route` needs OCR pipeline integration (pytesseract + pypdf);
`session_history_route` + `misconceptions_route` need new read queries;
`get/update_settings_route` need a settings store. Each is a separate
piece of work; they will be wired as their consuming GUI surface ships.

**Resolution policy:**
When each remaining route is implemented, remove the xfail marker and
verify the test passes. If the test fails, fix the implementation, do
not silently re-mark as xfail.

**Related work:**
- `tests/test_aristotle_intake.py` (4 tests — wired, passing)
- `tests/test_aristotle_routes.py` (9 tests — still xfail)
- `tests/test_teacher_dashboard.py` (2 tests — still xfail)
- `aristotle/api.py` (where the remaining routes need to be added)
- Brain GUI: `gui/pages/ask.py` `_ask_page_aristotle()` consumes
  /intake/* and /placer/* — wired in AIP_Brain commit a6f59bc on
  feat/multi-corpus.

---

## ARISTOTLE-DEBT-012 — COMPLETE-trigger in-flight race (Task 21 Fix 5 secondary gap)

**Status:** Active — flagged back from Task 21, not closed
**Phase:** Phase D (intake → plan generation)
**Filed:** 2026-07-16 (Task 21)

**What was deferred:**

Task 21 Fix 5 implemented the must-have idempotency guard for the
COMPLETE-trigger block in `aristotle/actors/intake.py::run_intake_step`:
if `session.state == IntakeState.COMPLETE and session.plan_id`, return
the existing plan_id without re-launching `create_plan_job` /
`generate_plan_pipeline`. This blocks the post-completion duplicate-plan
bug observed in the dogfood session.

The secondary gap (also called out in the Task 21 prompt) is NOT closed:
if the first COMPLETE trigger takes the pipeline-start path (not the
legacy fallback), the background pipeline is running but
`session.plan_id` is NOT set (the pipeline hasn't finished yet). A
second COMPLETE trigger would slip past guard #1 (no plan_id) and
launch a DUPLICATE background pipeline.

**Why deferred:**

Closing the gap requires disambiguating two cases that are
indistinguishable from `session.state` alone:
  - "PLAN_DRAFT focus, GENERATING_PLAN state" — the legitimate
    first-time COMPLETE (the model is in the plan-draft phase, about
    to produce its first COMPLETE). The pipeline SHOULD launch.
  - "Pipeline in-flight, GENERATING_PLAN state" — the first pipeline
    started but hasn't finished yet. A re-trigger should NOT launch a
    duplicate.

`IntakeState.GENERATING_PLAN` is the state mapped from BOTH
`current_focus="PLAN_DRAFT"` (see the `focus_to_state` dict in
`run_intake_step`) AND a would-be "pipeline in-flight" marker. The
existing tests (`test_llm_driven_complete_with_draft_plan_triggers_pipeline`
and `test_full_intake_loop_with_upload_and_draft_plan`) pre-seed
`state=GENERATING_PLAN` to simulate the first case — so a naive guard
on `session.state == GENERATING_PLAN` would break them.

The clean fixes (any one of):
  1. A new `IntakeState` value (e.g. `PLAN_PIPELINE_RUNNING`) — schema
     change, requires a migration + serializer update
     (`intake_session_to_dict` / `intake_session_from_dict`).
  2. A new boolean field on `IntakeSession` (e.g.
     `plan_pipeline_started: bool`) — schema change, requires the same
     serializer updates. The field is set to True right before
     `supervised_task(...)` in the pipeline-start path, and the guard
     checks it instead of `session.state`.
  3. Track in-flight tasks by session_id (the container's
     `_aristotle_plan_tasks` dict is keyed by `plan_job_id`, not
     `session_id`) — structural change to the container.

All three exceed the "one-line guard" scope the Task 21 prompt allows
for this secondary gap.

**Remediation trigger:**

When the next dogfood session reports a duplicate plan from a
re-confirmation message while the first pipeline is still running
(< 2 minute window between first COMPLETE and pipeline completion),
pick one of the three fixes above. Option 2 (new boolean field on
IntakeSession) is the smallest-blast-radius choice — one new field,
two serializer updates, one guard. The Task 21 code comment at the
COMPLETE-trigger block in `aristotle/actors/intake.py` documents all
three options in detail.

**Related work:**
- `aristotle/actors/intake.py::run_intake_step` (the COMPLETE-trigger block, ~line 1390)
- `tests/test_aristotle_intake.py::test_llm_driven_complete_with_draft_plan_triggers_pipeline` (existing test that pre-seeds state=GENERATING_PLAN — would break a naive guard)
- `tests/test_aristotle_intake.py::test_complete_trigger_idempotent_after_legacy_complete` (Task 21 new test — verifies the must-have guard)
- `tests/test_aristotle_intake_e2e.py::test_full_intake_loop_with_upload_and_draft_plan` (existing E2E test that pre-seeds state=GENERATING_PLAN)

---

## ARISTOTLE-DEBT-013 — `/aristotle/session/step` output drops TEACH/PROBE/QUIZ (Task 21 investigation)

**Status:** Active — confirmed bug, NOT yet fixed (Task 21 was investigation-only)
**Phase:** Tutoring loop / API
**Filed:** 2026-07-16 (Task 21)

**What was deferred:**

The `/aristotle/session/step` route in `aristotle/api.py:237-262`
computes its `output` field as:

```python
"output": (
    result.error
    or (result.data.get("prompt", "") if isinstance(result.data, dict) else "")
    or ""
) if result.ok else "",
```

The `result.data.get("prompt", "")` only reads the `prompt` key. But:
  - `_step_teach()` returns `data={"explanation": ..., "fading_mode": ...}`
  - `_step_probe()` returns `data={"question": ..., "question_type": "probe"}`
  - `_step_quiz()` returns `data={"question": ..., "question_type": "..."}`
  - Only `_step_predict()` returns `data={"prompt": ...}`

So TEACH/PROBE/QUIZ outputs are silently dropped — `output` is `""` for
those steps. `ask.py`'s `_step_tutoring` reads `data.get("output", "")`
and renders it; if it's empty, nothing is rendered. A learner using the
chat UI today sees the PREDICT prompt, types a guess, and then nothing
— the TEACH explanation is generated by the model (visible in backend
logs), stored on `session.last_explanation`, but never reaches the
chat. PROBE and QUIZ questions are similarly invisible.

**Why deferred:**

The Task 21 prompt explicitly scoped this as investigation-only ("do
not change code for this one"). The full investigation report is at
`docs/investigations/task-21-ask-py-teach-rendering.md`.

**Remediation trigger:**

Next tutoring-loop touch. The proposed minimal fix is one expression
in `session_step_route`:

```python
"output": (
    result.error
    or (
        result.data.get("prompt")
        or result.data.get("explanation")
        or result.data.get("question")
        or result.data.get("feedback")
        or ""
    ) if isinstance(result.data, dict) else ""
) if result.ok else "",
```

This is the boundary-layer translation: actors use semantically-named
keys (`explanation` / `question` / `feedback`), the GUI reads a single
`output` string. The translation belongs at the API boundary, not in
the actors or `ask.py`. No actor changes, no session-coordinator
changes, no `ask.py` changes — one expression in `aristotle/api.py`
plus one regression test asserting that `POST /session/step` for a
TEACH step returns `output` containing the explanation text.

The fix does NOT address EVALUATE's `diagnosis` block (misconception /
why_wrong / corrective) — those are separate fields on `data.diagnosis`,
not on `data.feedback`. Surfacing the diagnosis in the chat is a
separate design decision (do we want to show the learner the
misconception directly, or only use it internally to drive REMEDIATE?).

**Related work:**
- `aristotle/api.py:237-262` (the buggy `session_step_route`)
- `aristotle/session.py:949-980` (`_step_teach` — returns `data.explanation`)
- `aristotle/session.py:983-1015` (`_step_probe` — returns `data.question`)
- `aristotle/session.py:1018+` (`_step_quiz` — returns `data.question`)
- `aristotle/session.py:496+` (`_step_predict` — the only one that uses `data.prompt`)
- `AIP_Brain/gui/pages/ask.py:2079-2108` (`_step_tutoring` — reads `data.output`)
- `docs/investigations/task-21-ask-py-teach-rendering.md` (full investigation report)

---
