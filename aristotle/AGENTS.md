# ============================================================

# Aristotle Extension — Agent Navigation
> ADR-ARISTOTLE Phase A dogfood. The first extension built on the ADR-014 platform.
> Imports from aip.foundation only (via the Actor Protocol). Self-contained otherwise.

## Purpose
ARISTOTLE is the adaptive tutor — the first real consumer of the Phase 0
extension platform (ADR-014). This is the **Phase A dogfood drop**: a minimal
extension that proves the platform contract end-to-end. Each gap ARISTOTLE
surfaces is a Phase 0 protocol gap to log (ADR-ARISTOTLE §9).

Phase A scope (ADR-ARISTOTLE §11):
- Ingestor + curriculum map + prerequisite graph (placeholder — content
  ingestion comes when the textbook corpus has material)
- student_profile + struggle_pattern (schema in M001_aristotle.sql)
- TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (placeholder — SOCRATES
  is the entry point; full state machine comes with workflow integration)
- SM-2 via core VIGIL (reused, not re-implemented)
- Bilingual (content_primary + content_alt + content_alt_lang schema)

## Architecture Constraints
- **Self-contained**: imports from `aip.foundation.protocols.actors` only
  (ActorResult, ActorContext). No adapter or orchestration imports. The
  container is accessed via `ctx.container` (duck-typed as Any in the
  foundation Protocol).
- **Discovered by ExtensionHost**: lives under `extensions/aristotle/`.
  The host adds `extensions/` to sys.path at stage 1 validate (ADR-014 §6.4),
  making `aristotle.config`, `aristotle.actors`, `aristotle.hooks` importable.
- **Actor Protocol conformance**: SOCRATES conforms to
  `aip.foundation.protocols.actors.Actor` (name/cadence/run_cycle/health).
  The host validates this via `isinstance(actor, Actor)` at scheduler start.
- **Manual-only actor**: `cadence=0.0` — the tutoring state machine is driven
  by user turns, not by a timer (ADR-ARISTOTLE §3: "the learner only feels
  rhythm"). The host runs one cycle on start, then waits for cancellation.

## Contracts (What This Module Promises to Consumers)

### Manifest (extension.yaml)
- `id: aristotle` (immutable post-registration; must not collide)
- `manifest_version: 1`
- `contributes.corpora`: one `textbook` corpus (type=document, sensitive=false)
  → registered as `aristotle:textbook` (ADR-014 §6.2 namespacing)
- `contributes.actors: [socrates]` (advisory; actual registration in hooks.py)
- `contributes.workflows_dir: workflows` (placeholder tutoring_session_v1.yaml)
- `contributes.migrations: migrations` (M001_aristotle.sql)
- `config.schema: aristotle.config:AristotleSettings`

### AristotleSettings (config.py)
Plain dataclass (not pydantic_settings.BaseSettings) so it instantiates
without env-var dependencies. Defaults:
- `primary_language: str = "en"`
- `alt_language: str = "ur"` (ADR-ARISTOTLE §7 bilingual)
- `bloom_default: int = 3` (1-6 scale, ADR-ARISTOTLE §4)
- `review_interval_seconds: int = 86400` (24h SM-2 default)

### SOCRATES actor (actors/socrates.py)
- `name = "socrates"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: verifies `aristotle:textbook` corpus is registered via
  `ctx.container.corpus_registry.get_stores()`, logs its presence, returns
  `ActorResult(ok=True)`. A full SOCRATES would query the concept graph +
  call a model + persist the result — that's Phase A follow-up.
- `health()`: returns `{"state": "active", "name": "socrates", ...}`

### EXAMINER actor (actors/examiner.py)
- `name = "examiner"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: verifies the corpus is reachable + checks whether
  `container.model_provider` is configured. Returns `ok=True` in both cases
  (the actor is healthy; it just can't generate questions without a model).
  The tutoring loop checks model availability before attempting a quiz —
  governance invariant: "No silent model calls" (AGENTS.md §1.7).
- `health()`: returns `{"state": "active", "name": "examiner", ...}`
- Role in state machine: PROBE → QUIZ → EVALUATE (ADR-ARISTOTLE §3)

### MENTOR actor (actors/mentor.py)
- `name = "mentor"`, `cadence = 0.0` (manual-only)
- `run_cycle(ctx)`: reads `aristotle_struggle_pattern` for the default
  student (`'definer'` — pre-alpha single-tenant). If absent, initializes
  with a placeholder. If present, logs it. This proves the actor can
  execute SQL against the extension's own corpus via
  `stores.connection_manager.write_conn`.
- `health()`: returns `{"state": "active", "name": "mentor", ...}`
- Role in state machine: EVALUATE (updates struggle_pattern after scoring) +
  feeds REMEDIATE (the struggle_pattern sentence is injected into the
  re-teaching prompt, ADR-ARISTOTLE §2).

### Migration (M001_aristotle.sql)
Creates two tables in the `aristotle:textbook` corpus:
- `aristotle_concept`: concept-aware chunks (ADR-ARISTOTLE §4) with bilingual
  columns `content_primary` + `content_alt` + `content_alt_lang` (ADR-014 §1).
  Includes `prerequisite_concept_id` for the DAG.
- `aristotle_struggle_pattern`: one persistent AI-written diagnostic sentence
  per student (ADR-ARISTOTLE §2 MENTOR role). Pre-alpha single-tenant:
  `student_id` defaults to `'definer'`.

**Note on progress store location**: ADR-014 §1 says progress tables go in
the `definer` corpus, but the migration_loader (step 1) applies to the
extension's own corpus (`aristotle:textbook`). For pre-alpha dogfood,
per-corpus is simpler and matches the loader's behavior. Revisit at Phase B
(teacher dashboard) when cross-corpus aggregation matters.

### Hooks (hooks.py)
- `on_load(host)`: calls `host.register_actor("socrates", SocratesActor, cadence=0.0)`.
  The host sets `_current_ext_id` before calling, so `host.config` /
  `host.manifest` resolve to ARISTOTLE's validated config + manifest.
- `on_unload(host)`: no-op (no background resources to release in Phase A).

## Data Flows (In / Out)

### In
- `extension.yaml` manifest (discovered by host at stage 0)
- `M001_aristotle.sql` migration (applied to `aristotle:textbook` corpus at stage 2)
- `AristotleSettings` config (loaded + instantiated at stage 1)
- `hooks.py::on_load` (called at stage 5 to register SOCRATES)

### Out
- `aristotle:textbook` corpus registered with CorpusRegistry
- `aristotle_concept` + `aristotle_struggle_pattern` tables in that corpus
- `socrates` actor registered + scheduler task started (runs one cycle on start)
- `tutoring_session_v1` workflow template discovered via WorkflowRegistry.add_path

### Cross-folder flows
- `extensions/aristotle/hooks.py` → `aip.adapter.extensions.host.ExtensionHost`:
  calls `host.register_actor(...)` at stage 5.
- `extensions/aristotle/actors/socrates.py` → `aip.foundation.protocols.actors`:
  imports `ActorContext` + `ActorResult`.
- `extensions/aristotle/actors/socrates.py` → `ctx.container.corpus_registry`:
  calls `get_stores("aristotle:textbook")` at runtime.
- `extensions/aristotle/config.py` → host's `_import_class`:
  loaded via `importlib.import_module("aristotle.config")` at stage 1
  (requires `extensions/` on sys.path — added by host).

## Known Gotchas
- **Progress tables are in `aristotle:textbook`, not `definer`.** ADR-014 §1
  says progress tables go in the definer corpus, but the migration_loader
  applies to the extension's own corpus. Pre-alpha pragmatism; revisit at
  Phase B. The `aristotle_*` naming convention is preserved either way.
- **Map click must pass concept_id in URL.** The learning map navigates to
  `/ask?extension=aristotle&concept={cid}`. If the query params are dropped,
  the ask page cannot determine which concept to tutor — the student gets
  a generic concept selector instead of the pre-selected flow.
- **`_session_to_dict` / `_session_from_dict` must stay in sync with
  `SessionContext` fields.** When a new boolean flag is added to the
  session dataclass (e.g. `predict_generated`), BOTH serializers must be
  updated. Missing a field silently defaults to False on round-trip,
  breaking state transitions that depend on the flag.
- **Output extraction must check `result.data`, not just `result.error`.**
  Post-DEBT-011 migration, actors return content via `result.data` (e.g.
  `data={"prompt": "..."}` for PREDICT). The session/step route must
  fall back through `result.error` → `result.data.get("prompt")` → empty
  string. Reading only `result.error` returns empty output for ok results.
- **All three actors are placeholders.** The dogfood SOCRATES/EXAMINER/MENTOR
  verify platform reachability (corpus, model provider, struggle_pattern
  table) but don't do real teaching/probing/mentoring. The full tutoring
  loop (concept graph query, model call, persistence, state machine
  execution) is Phase A follow-up work.
- **`cadence=0.0` means manual-only.** All three actors run one cycle on
  start, then wait forever for cancellation. The tutoring state machine is
  driven by user turns, not by a timer (ADR-ARISTOTLE §3).
- **The `tutoring_session_v1.yaml` workflow is engine-compatible and executable.**
  Rewritten to use the L5 Workflow Engine's node types (`agent`, `script`,
  `condition`) instead of the placeholder types (`synthesize`, `decision`,
  `commit`) that the loader rejects. The engine is wired into the container
  (`container.workflow_engine`, ADR-014 §8 step 2). Extensions access it via
  `ctx.container.workflow_engine.run_workflow(yaml_path, variables)`. The
  workflow's 7 nodes (teach → probe → quiz → evaluate → check_mastery →
  remediate → next_concept) match the ADR-ARISTOTLE §3 state machine. The
  `script` nodes (`evaluate`, `next_concept`) reference `run: aristotle_evaluate`
  and `run: aristotle_next_concept` — these are script handlers that need
  to be registered with the engine (future work; the engine currently runs
  them in fixture/no-op mode).
- **No HERALD actor yet.** HERALD (field awareness) is Phase C — depends on
  the Phase 0 web/feed layer (ADR-014 §3.4), which is not yet built.
- **EXAMINER returns `ok=True` even without a model.** This is intentional:
  the actor is healthy, it just can't generate questions. The tutoring loop
  checks model availability before attempting a quiz (governance: "No silent
  model calls"). A future EXAMINER will return NEEDS_CONFIGURATION when
  asked to generate a question without a model.
- **`model_provider.call()` does NOT raise on API failures (429, 5xx).**
  It returns `{"error": True, "content": "", "error_message": "..."}`.
  Callers MUST inspect `result.get("error")` and content emptiness —
  catching only raised exceptions will treat rate-limited calls as
  success-with-empty-content. Task 21 added `_model_call_failed()` +
  `_call_with_retry()` helpers in `aristotle/actors/examiner.py` that
  encode the canonical check (matches `ModelSlotResolver`'s
  `primary_failed` pattern). Any new model-call site should use these
  helpers, not hand-roll its own try/except.
- **Evaluation model sometimes wraps its JSON in ```json fences.**
  `json.loads()` fails on the raw fenced text. Task 21 added
  `_strip_json_fences()` in `aristotle/actors/examiner.py`, applied
  before `json.loads()` in `evaluate()`. Any new strict-JSON model-call
  site should apply the same stripping.
- **`/aristotle/session/step`'s `output` field only reads `data.prompt`.**
  This is a confirmed bug (Task 21 investigation, NOT yet fixed): the API
  computes `output = result.error or result.data.get("prompt", "") or ""`,
  but `_step_teach()` returns `data.explanation`, `_step_probe()` and
  `_step_quiz()` return `data.question`. TEACH/PROBE/QUIZ outputs are
  silently dropped — only PREDICT (which uses `data.prompt`) reaches the
  chat UI. See `docs/investigations/task-21-ask-py-teach-rendering.md`
  for the full analysis and proposed minimal fix.
- **COMPLETE-trigger idempotency guard only catches the post-completion
  case, not the in-flight race.** Task 21 Fix 5 added a guard in
  `aristotle/actors/intake.py::run_intake_step` that blocks re-trigger
  after `session.state == COMPLETE and session.plan_id` (the legacy-
  fallback path sets both). The in-flight race (second COMPLETE while
  the first background pipeline is still running, before plan_id is set)
  is NOT closed — closing it requires disambiguating
  "PLAN_DRAFT focus, GENERATING_PLAN state" (legitimate first-time
  COMPLETE) from "pipeline in-flight, GENERATING_PLAN state" (re-trigger),
  which exceeds the one-line-guard scope. See the long comment at the
  COMPLETE-trigger block in `aristotle/actors/intake.py` for the three
  structural fixes that would close the gap.

## Last Cycle
- **Task 21 — Tutoring session-quality fixes (examiner resilience + teach prompt length + intake idempotency)** (this cycle):
  - **Fix 1 — Strip markdown JSON fences before parsing** (`aristotle/actors/examiner.py::evaluate()`):
    the evaluation model sometimes wraps its JSON response in a ```json
    fence. `json.loads()` failed on the raw fenced text, scoring the
    student 0.0 for what was actually a valid response. Added
    `_strip_json_fences()` helper (regex strip of leading/trailing
    ```json or ``` fence, language tag optional) applied before
    `json.loads()`. The existing `(JSONDecodeError, ValueError, TypeError)`
    fallback path is unchanged — it now only triggers for genuinely
    malformed responses. 3 new tests in `TestExaminerResilience` (fenced
    ```json, bare ```, and genuinely-bad-JSON still falls back).
  - **Fix 2 — `_generate_question()` must not log a failed call as success**
    (`aristotle/actors/examiner.py::_generate_question()`, shared by
    `probe()` and `quiz()`): `model_provider.call()` does NOT raise on
    API failures like 429 — it returns `{"error": True, "content": ""}`.
    The previous code only caught raised exceptions, so a rate-limited
    call fell through to `question = result.get("content", "")` → empty
    string → logged as `examiner_probe_ok ... question_len=0` (success!).
    Added the canonical `_model_call_failed()` check (matches
    `ModelSlotResolver`'s `primary_failed` pattern) + a retry-with-backoff
    helper `_call_with_retry()` (max_retries=2, 1s then 2s sleep, same
    shape as `aristotle/actors/intake.py`'s beast-slot retry loop). 3
    new tests: error=True returns ok=False (not ok=True with empty
    question), empty-content returns ok=False, retry-then-succeeds
    verifies call count = 2.
  - **Fix 3 — `evaluate()` retries on failed/empty call, doesn't score it as 0**
    (`aristotle/actors/examiner.py::evaluate()`): same root cause as Fix 2,
    different symptom. A rate-limited evaluation-slot call returned empty
    content, indistinguishable from genuinely-malformed JSON — both fell
    into the same score=0.0 fallback. Infrastructure flakiness was scoring
    the student's answer as WRONG. Now: `_call_with_retry()` runs first;
    only after retries are exhausted does the call fall through to the
    score=0.0 path. 2 new tests: error=True retries (call count = 3) then
    returns ok=False (NOT ok=True with score=0.0), and retry-then-succeeds
    returns the actual model score (not 0.0).
  - **Fix 4 — Teaching prompt: length ceiling + plain-language register**
    (`aristotle/actors/socrates.py::_build_system_prompt()`): confirmed
    via full-file grep — no length, brevity, or register constraint
    anywhere in the file. The `full_worked_example` fading-mode
    instruction said "Show every step — do not skip anything" with no
    counterweight, producing ~5000-character single explanations in
    production. Added a `LENGTH AND REGISTER` block to the base prompt
    (applies across ALL fading modes): 2-4 short paragraphs maximum,
    plain everyday English, short sentences. Softened the
    `full_worked_example` branch to "Show every step, but keep each
    step to 1-2 sentences — completeness of steps, not verbosity per
    step." 3 new tests in `TestSocratesPromptLength` pin the string
    presence so a future refactor can't silently drop the constraint.
  - **Fix 5 — Guard the COMPLETE trigger against duplicate plan generation**
    (`aristotle/actors/intake.py::run_intake_step`, ~line 1390):
    implemented the must-have post-COMPLETE guard: if
    `session.state == IntakeState.COMPLETE and session.plan_id`, return
    the existing plan_id without re-launching `create_plan_job` /
    `generate_plan_pipeline`. The legacy-fallback path (which sets
    `session.state=COMPLETE` + `session.plan_id`) is preserved — a
    genuine first-time successful completion is NOT blocked. 1 new test
    `test_complete_trigger_idempotent_after_legacy_complete` verifies
    the guard fires and the pipeline-launch functions are NOT called.
    **Secondary gap (in-flight race) flagged back per Task 21 prompt**:
    I attempted to also block re-trigger WHILE the first pipeline is
    still running in the background (by persisting
    `session.state = IntakeState.GENERATING_PLAN` and adding a guard
    for that case). That approach was ambiguous and broke two existing
    tests — `IntakeState.GENERATING_PLAN` is ALSO the state mapped from
    `current_focus="PLAN_DRAFT"`, so the guard couldn't distinguish
    "PLAN_DRAFT phase, first-time COMPLETE" (legitimate, should launch
    pipeline) from "pipeline in-flight, re-trigger" (should NOT relaunch).
    The clean fixes (new IntakeState value, new session boolean field,
    or session-keyed task tracking) all exceed the "one-line guard" scope
    the Task 21 prompt allows for this secondary gap. See the long
    comment in `aristotle/actors/intake.py` at the COMPLETE-trigger
    block for the full analysis. The must-have behavior (blocking
    re-trigger AFTER completion) is implemented and tested.
  - **Investigation item — TEACH-step rendering in `gui/pages/ask.py`**
    (investigation-only, NO code changes per the Task 21 prompt):
    **confirmed bug**. The `/aristotle/session/step` route in
    `aristotle/api.py:237-262` computes its `output` field as
    `result.error or result.data.get("prompt", "") or ""`. The
    `data.get("prompt", "")` only reads the `prompt` key — but
    `_step_teach()` returns `data={"explanation": ...}`,
    `_step_probe()` returns `data={"question": ...}`, and
    `_step_quiz()` returns `data={"question": ...}`. Only
    `_step_predict()` uses `data.prompt`. So TEACH/PROBE/QUIZ outputs
    are silently dropped by the API — `ask.py`'s `_step_tutoring`
    faithfully renders the empty `output` string. Proposed minimal fix
    (one expression in `session_step_route`, NOT implemented): fall
    through `data.prompt or data.explanation or data.question or
    data.feedback or ""`. Full report at
    `docs/investigations/task-21-ask-py-teach-rendering.md`.
  - **Test impact**: 225 passed / 1 skipped / 5 xfailed / 0 failures
    (was 213/1/5/0 at Task 20 baseline; +12 new tests: 8 in
    `TestExaminerResilience`, 3 in `TestSocratesPromptLength`, 1 in
    `TestIntakeLLMDriven`). No existing tests modified or removed.
- **Task 20 — DELETE plan route + plan-scoped GUI pages + delete affordance** (prior cycle):
  - **Backend**: new `DELETE /aristotle/plans/{plan_id}` route in
    `aristotle/api.py`. Explicit cascade (SQLite foreign keys are not
    enforced — no PRAGMA foreign_keys anywhere): placement_event →
    intake_session → (per concept: mastery, predict_event,
    misconception_log) → concept → plan_job → learning_plan. Single
    transaction, rollback on any failure. Does NOT touch
    aristotle_uploaded_material or vector store chunks — material may
    be shared/re-used. Returns {deleted, plan_id, subject,
    concepts_deleted, cascade_rows_deleted}. 404 on unknown plan_id.
  - **Backend**: `GET /dashboard` response's `mastery_by_concept[]`
    rows now include `plan_id` (8th SELECT column, was 7) — so the
    Teacher Dashboard can label each row with its subject without a
    second lookup. Backward-compatible additive field.
  - **GUI helpers**: `aristotle/gui/api_client.py` — added `plan_id`
    param to `get_mastery()`, `get_concepts()`, `get_misconceptions()`,
    `get_struggle_patterns()`, `get_session_history()`. Added
    `delete_plan(plan_id)` calling the new DELETE route. (Settings
    helper deliberately NOT given plan_id — confirmed
    `aristotle_settings` is keyed by `student_id` only, no `plan_id`
    column; per-plan scoping there would be fake.)
  - **GUI pages**: `aristotle/gui/pages.py` — `/aristotle/stats` and
    `/aristotle/map` now have a plan selector at the top (populated
    from `get_plans()`, defaults to most-recently-active plan).
    `/aristotle/teacher` (dashboard) gets an optional "All subjects"
    filter (default — preserves cross-subject aggregation behavior)
    plus subject labels on every Needs-Attention, Recent-Sessions, and
    ALL-CONCEPTS row (label is "Unlabeled" for pre-Task-18 legacy rows
    with no plan_id, not hidden or crashing). `/aristotle/settings`
    gets NO selector — settings are student-global per M005 schema.
  - **GUI picker** (Brain-side, separate commit on feat/multi-corpus):
    `_show_plan_picker()` in `gui/pages/ask.py` now renders a trash
    icon next to each "Resume: X" button. Click opens a modal dialog
    with "Delete permanently" + "Cancel" — two-step confirm, no
    one-click delete on live student data. On confirm, calls DELETE
    route, shows a "Deleted X — N concepts and M related records
    removed" message, then re-renders the picker.
  - **Tests**: 5 new tests in `test_aristotle_student_scoping.py`
    (TestDeletePlanRoute): 404 on unknown plan_id, full cascade in
    dependency order, does-not-touch-uploaded-material, rollback on
    mid-cascade failure, no-concepts edge case. 213 passed / 5 xfailed
    / 0 failures (was 208, +5 new).
- **Task 19 — GUI port fix + plan picker (ADR-004 GUI half)** (prior cycle):
  - **Bug fix**: `aristotle/gui/api_client.py` was hardcoded to
    `ARISTOTLE_BACKEND_URL=http://localhost:8001` — a port nothing was
    listening on. Aristotle's API is mounted on Brain's backend at :8000
    (extension_api_router_mounted ext='aristotle'), NOT a separate port.
    Every dashboard / stats / map / settings / session-history call
    silently failed into {} / [] and the GUI rendered empty. Fixed:
    `_BASE` now reads `ARISTOTLE_BACKEND_URL` (still respected if set)
    falling back to `AIP_BACKEND_URL` (same env var as Brain's
    `gui/pages/ask.py`) falling back to `http://127.0.0.1:8000`. One-line
    behavior change, big visible effect: dashboard, stats, map, settings,
    session-history now actually fetch data.
  - **Feature**: added `get_students()`, `create_student(name)`,
    `get_plans(student_id)` helpers to `aristotle/gui/api_client.py`.
    These back the plan picker that `AIP_Brain/gui/pages/ask.py` now
    renders on page load (see Task 19 commit on AIP_Brain
    `feat/multi-corpus`). The picker lists existing learning plans and
    lets the learner resume one or start a new subject — without it,
    every page load kicked off a fresh intake and existing plans were
    undiscoverable from the UI. Closes the "no information in dashboard
    / can't resume my lessons" bug report.
  - **Test impact**: 208 passed / 5 xfailed / 0 failures (unchanged
    from Task 18 baseline — the api_client.py changes are additive
    helpers + a default URL change; no test logic touched).
  - **Import boundary**: 2/2 pass — no new aip.* imports added.
- **Task 18 — ADR-004 backend (student identity + scoping)** (prior cycle):
  - M009 migration: aristotle_student table, definer backfill, four new
    columns (aristotle_learning_plan.student_id + material_id,
    aristotle_concept.plan_id + material_id), two indexes, best-effort
    backfill. See worklog.md Task 18 for full context.
  - API: POST/GET /students, GET /plans?student_id=X (new); /concepts
    and /dashboard now accept optional plan_id/material_id/student_id
    filters and log unscoped-call warnings.
  - IntakeSession.student_id field flows from /intake/start through
    generate_plan into aristotle_learning_plan.student_id.
  - 208 passed / 5 xfailed / 0 failures (was 187, +21 new tests).
- **Onboarding gateway + API bug fixes** (prior cycle):
  - Fixed map click handler in `gui/pages.py`: concept cards now navigate to
    `/ask?extension=aristotle&concept={cid}` instead of bare `/ask`, passing
    the concept_id through to the ask page.
  - Added `_ask_page_aristotle()` to Brain's `gui/pages/ask.py`: reads
    `extension` and `concept` query params, branches into Aristotle tutoring
    UI with concept name display + START button (pre-selected) or concept
    selector list (direct URL).
  - Fixed `_session_to_dict` missing `predict_generated` field — round-trip
    serialization now preserves the PREDICT generation flag.
  - Fixed `_session_from_dict` missing `predict_generated` field.
  - Fixed output extraction in session/step route: now checks
    `result.data.get("prompt")` (for PREDICT) after `result.error`, instead
    of only reading `result.error` which is empty post-DEBT-011 migration.
- **ARISTOTLE Phase A — multi-actor + state machine** (prior cycle):
  - Built EXAMINER actor (`actors/examiner.py`): probe/quiz/evaluate mode.
    Conforms to Actor Protocol. Verifies corpus reachability + checks model
    availability. Returns `ok=True` in both cases (healthy actor; the
    tutoring loop checks model before quiz). Governance: no silent model calls.
  - Built MENTOR actor (`actors/mentor.py`): long-arc tracking. Conforms to
    Actor Protocol. Reads `aristotle_struggle_pattern` table via
    `stores.connection_manager.write_conn`; initializes with a placeholder
    if absent. Proves per-student state read/write against the extension's
    own corpus.
  - Updated `actors/__init__.py` to re-export all three actors.
  - Updated `hooks.py` to register SOCRATES + EXAMINER + MENTOR (all
    cadence=0.0, manual-only).
  - Updated `extension.yaml` advisory actors list: `[socrates, examiner, mentor]`.
  - Replaced the placeholder `tutoring_session_v1.yaml` with a real state
    machine workflow: 7 nodes (teach → probe → quiz → evaluate →
    remediate_on_struggle [decision] → remediate → next_concept). Declared
    but not executable — the workflow engine isn't wired into the container
    yet (ADR-014 §8 step 2 deferred).
  - Added `tests/test_aristotle_actors.py` (10 tests): 5 conformance
    (isinstance + distinct names + health for all three) + 5 behavior
    (EXAMINER degrades gracefully without model; EXAMINER fails without
    corpus_registry; MENTOR initializes struggle_pattern when absent;
    MENTOR reads existing without INSERTing; MENTOR fails without
    corpus_registry). All 10 pass locally (fakes, no aiosqlite needed).
  - Verified: manifest validates with 3 actors; all three conform to Actor
    Protocol; workflow YAML parses with 7 nodes; all 14 existing Actor
    Protocol + WorkflowRegistry tests still pass (no regression).
- **Phase A dogfood drop** (prior cycle):
  - Built `extensions/aristotle/` (7 files): `extension.yaml` manifest,
    `config.py` (AristotleSettings dataclass), `migrations/M001_aristotle.sql`
    (aristotle_concept + aristotle_struggle_pattern tables with bilingual
    schema), `actors/socrates.py` (minimal SOCRATES conforming to Actor
    Protocol), `actors/__init__.py`, `hooks.py` (on_load registers SOCRATES),
    `workflows/tutoring_session_v1.yaml` (placeholder), `__init__.py`.
  - **Surfaced + fixed a platform gap**: the host's `_import_class` did
    `importlib.import_module("aristotle.config")` but `aristotle` wasn't
    importable because `extensions/` wasn't on sys.path. Fixed by adding
    `extensions/` to sys.path at stage 1 validate (host.py). This is
    exactly the kind of gap ARISTOTLE was supposed to surface
    (ADR-ARISTOTLE §9).
  - Added `tests/test_aristotle_extension.py` (7 integration tests):
    manifest validates; migrations create tables; SOCRATES registers;
    SOCRATES conforms to Actor Protocol; config.schema loads; health
    surfaces; stop cancels.
  - Verified locally: manifest validates (8 fields); AristotleSettings
    instantiates with bilingual defaults (en/ur); SocratesActor conforms
    to Actor Protocol; all 14 existing Actor Protocol + WorkflowRegistry
    tests still pass (no regression from the sys.path fix).
  - Full ARISTOTLE integration tests deferred to CI (need aiosqlite for
    CorpusRegistry).

## Key Files
| File | Role |
|------|------|
| `extension.yaml` | Manifest v1 — declares textbook corpus, 3 actors (socrates/examiner/mentor), migrations, config.schema |
| `config.py` | AristotleSettings dataclass (bilingual defaults: en primary, ur alt) |
| `migrations/M001_aristotle.sql` | Creates aristotle_concept (bilingual schema) + aristotle_struggle_pattern |
| `actors/__init__.py` | Re-exports SocratesActor, ExaminerActor, MentorActor |
| `actors/socrates.py` | SOCRATES — teach mode. Verifies corpus reachability. Conforms to Actor Protocol. |
| `actors/examiner.py` | EXAMINER — probe/quiz/evaluate. Verifies corpus + checks model availability. Conforms to Actor Protocol. |
| `actors/mentor.py` | MENTOR — long-arc tracking. Reads/writes aristotle_struggle_pattern. Conforms to Actor Protocol. |
| `hooks.py` | on_load registers all 3 actors; on_unload is a no-op |
| `workflows/tutoring_session_v1.yaml` | TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine (7 nodes; declared, not yet executable) |
| `__init__.py` | Package marker + docstring |

## Work Guidance
- Adding a new actor (EXAMINER, MENTOR): create `actors/<name>.py` with a
  class conforming to the foundation Actor Protocol (name/cadence/run_cycle/
  health). Add to `actors/__init__.py`. Register in `hooks.py::on_load` via
  `host.register_actor(...)`. Update the manifest's advisory `actors:` list.
- Adding a new table: add a new `M00X_<name>.sql` migration (M<3-digit>_
  naming convention). The migration_loader applies it to the
  `aristotle:textbook` corpus. Use `CREATE TABLE IF NOT EXISTS` for
  idempotency.
- Adding a config field: add to `AristotleSettings` in `config.py` with a
  default. The host instantiates via `cls()` (zero-arg), so all fields
  must have defaults.
- Testing: every new behavior gets a test in `tests/test_aristotle_extension.py`.
  The integration tests point the host at the real `extensions/` dir.

## How to Test
```bash
# Run the ARISTOTLE integration tests (needs aiosqlite + structlog):
CI=true uv run pytest tests/test_aristotle_extension.py -v

# Verify the manifest validates in isolation:
PYTHONPATH=src python -c "
import yaml
from aip.adapter.extensions.manifest import Manifest
m = Manifest.model_validate(yaml.safe_load(open('extensions/aristotle/extension.yaml')))
print(m.id, m.version, m.contributes.corpora)
"

# Verify SocratesActor conforms to the Actor Protocol:
PYTHONPATH=src:extensions python -c "
from aip.foundation.protocols.actors import Actor
from aristotle.actors import SocratesActor
print('conforms:', isinstance(SocratesActor(), Actor))
"
```

# ============================================================
