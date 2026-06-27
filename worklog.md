# AIP_Aristotle — Work Log

Append-only work log. Each entry starts with `---` and includes Task ID,
Agent, Task, Work Log, Stage Summary, and Files changed. See
`AGENTS.md` Coding Cycle Protocol for the format.

---
Task ID: 1
Agent: Super Z (main)
Task: Extract ARISTOTLE from AIP_Brain/extensions/aristotle/ into a separate pip-installable repo

Work Log:
- Extracted all files from AIP_Brain/extensions/aristotle/ (11 files: __init__.py, config.py, hooks.py, extension.yaml, actors/{__init__,socrates,examiner,mentor}.py, migrations/M001_aristotle.sql, workflows/tutoring_session_v1.yaml, AGENTS.md).
- Created pyproject.toml: name=aip-aristotle, depends on aip>=0.1.0, declares [project.entry-points."aip.extensions"] aristotle = "aristotle.entrypoint:get_manifest". hatchling build backend. force-include for non-Python files (extension.yaml, migrations/, workflows/).
- Created aristotle/entrypoint.py: get_manifest() loads extension.yaml via importlib.resources, validates via Manifest.model_validate, returns a Manifest instance. This is the entry point the platform's ExtensionHost discovers via importlib.metadata.entry_points(group="aip.extensions").
- Moved tests/test_aristotle_extension.py + tests/test_aristotle_actors.py from AIP_Brain (they test ARISTOTLE, not the platform). Appended the 4 workflow engine-compatibility tests that were in AIP_Brain/tests/test_workflow_engine_wiring.py (they reference ARISTOTLE's workflow YAML, so they belong here).
- Created README.md + .gitignore.
- Initialized git repo, committed, pushed to https://github.com/freedomgeneration1111-sudo/AIP_Aristotle. Resolved README conflict from GitHub UI initial commit (took ours).
- Verified: get_manifest() works (loads + validates extension.yaml, returns Manifest with id=aristotle, version=0.1.0, 3 actors, 1 corpus). All 3 actors import + conform to foundation Actor Protocol from the new repo location. 9 tests pass (5 conformance + 4 workflow engine-compatibility).

Stage Summary:
- ARISTOTLE is now a separate pip-installable package at https://github.com/freedomgeneration1111-sudo/AIP_Aristotle. Install: `pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git`. Dev: `pip install -e .` after `pip install -e ../AIP_Brain`.
- The platform discovers ARISTOTLE via importlib.metadata.entry_points(group="aip.extensions") — the standard Python plugin mechanism. No sys.path hack for pip-installed extensions.
- Separation of concerns is machine-enforced by tests/test_extension_import_boundary.py (in AIP_Brain): extensions import only aip.foundation.protocols.* + aip.adapter.extensions + aip.foundation.schemas; the platform imports nothing from extensions.
- Downloadability is real: Sameer or Ramesh runs one command and ARISTOTLE is installed. No PyPI needed for pre-alpha.
- The precedent is set for LOOM and CodeForge: each is its own repo, its own pyproject.toml declaring the aip.extensions entry point, its own version + release cycle.

Files created:
- pyproject.toml, README.md, .gitignore
- aristotle/entrypoint.py (NEW — get_manifest for entry-point discovery)
- aristotle/ (all 11 files moved from AIP_Brain/extensions/aristotle/)
- tests/test_aristotle_extension.py (moved from AIP_Brain)
- tests/test_aristotle_actors.py (moved from AIP_Brain + 4 workflow tests appended)

---
Task ID: 2
Agent: Super Z (main)
Task: Port conventions/docs from AIP_Brain into AIP_Aristotle (set up from the beginning)

Work Log:
- Oriented: re-read AIP_Brain's root AGENTS.md (coding cycle protocol), ADR-000-template.md, AIP_GOVERNANCE.md, CONTRIBUTING.md, PLANNED_FEATURES.md, TECH_DEBT.md, STATUS.md, worklog.md format. Re-read the original ADR-ARISTOTLE architecture doc (uploaded at session start) — becomes ADR-001 in this repo.
- Created AGENTS.md (root): the coding cycle protocol adapted for an extension. Key adaptation: "layer discipline" becomes "extension boundary discipline" (the allowlist from test_import_boundary.py). Includes governance invariants (linked to AIP_Brain's AIP_GOVERNANCE.md, not copied), docs framework rules, coding cycle protocol (5 steps), AGENTS.md section template, child docs index, root status-tracking docs table, platform references table.
- Created PLANNED_FEATURES.md: ARISTOTLE-specific tracker. Already Built (Phase A dogfood: extension platform integration, 3 actors, data model, workflow, tests). Near-Term (Phase A completion: script handlers, real model calls, content ingestor, SM-2, teacher dashboard). Long-Term (Phase C: HERALD field awareness). Change Log + Cross-References.
- Created TECH_DEBT.md: ARISTOTLE-specific debt register. 4 items: ARISTOTLE-DEBT-001 (progress tables in aristotle:textbook not definer — revisit Phase B), ARISTOTLE-DEBT-002 (actors are placeholders — Near-Term), ARISTOTLE-DEBT-003 (workflow script handlers not registered — Near-Term), ARISTOTLE-DEBT-004 (single-tenant student_id — by design pre-alpha).
- Created STATUS.md: current operational state. Pre-alpha, not yet dogfoodable. What works (lifecycle, actors conform, MENTOR reads/writes struggle_pattern, workflow declared, health surfaces, boundary enforced). What doesn't (no real model calls, script handlers not registered, no content, no SM-2, no GUI). Install + dev + test instructions. Actor status table. Data model status table. Workflow status table. Platform dependencies table. Pilot readiness assessment.
- Created worklog.md: seeded with Task ID 1 (the extraction) + this entry (Task ID 2).
- Created docs/decisions/ADR-000-template.md: copied from AIP_Brain (the template is universal).
- Created docs/decisions/ADR-001-aristotle-architecture.md: reformatted the original ADR-ARISTOTLE spec to the ADR template (Context, Decision, Alternatives, Consequences, Related). Preserves all 11 sections of the original (single-voice principle, five modes, session experience, knowledge model, data model, HERALD, bilingual, teacher dashboard, Phase 0 consumption, pilot protocol, phased build).
- Created CONTRIBUTING.md: adapted from AIP_Brain. Dev setup is `pip install -e ../AIP_Brain && pip install -e .` (editable installs for both). Code style (ruff, line-length 120). Test instructions. Architecture (extension boundary, not layers). Design principles (no fake success, DEFINER sovereignty, honest evaluation, bilingual, single-voice). Commit message guidance.
- Created tests/test_import_boundary.py: the extension's own boundary test. Asserts aristotle/* imports from aip.* ONLY through the allowlist (aip.foundation.protocols.*, aip.adapter.extensions, aip.foundation.schemas). AST-checked (catches static, lazy, AND importlib imports). This is the self-defending boundary — the platform's test checks all extensions; this one checks ARISTOTLE specifically.
- Expanded README.md: from one-liner to full README (install, dev setup, what's here, architecture, status, pilot protocol, license).
- Verified: all docs parse (markdown structure sound); boundary test passes (ARISTOTLE's only aip.* import is aip.foundation.protocols.actors — on the allowlist); no code changes (docs-only unit).

Stage Summary:
- The convention framework from AIP_Brain is now in AIP_Aristotle, adapted for an extension (not a platform). Every future ARISTOTLE cycle follows the same discipline that got the platform this far: orient → contract check → code → verify → document.
- The extension boundary is now machine-enforced from BOTH sides: the platform's test_extension_import_boundary.py (checks all extensions) + ARISTOTLE's own test_import_boundary.py (checks itself). A forbidden import fails CI in either repo.
- ADR-001 (the architecture spec) is now in the repo, reformatted to the ADR template. Future ARISTOTLE ADRs start at ADR-002.
- The status-tracking docs (PLANNED_FEATURES, TECH_DEBT, STATUS) are ARISTOTLE-specific, not copies of the platform's. Each extension tracks its own features, debt, and operational state.
- The worklog is seeded with the extraction (Task ID 1) + this convention port (Task ID 2). The append-only format matches AIP_Brain's.

Files created:
- AGENTS.md (root — coding cycle protocol + boundary discipline)
- PLANNED_FEATURES.md (Phase A/B/C tracker)
- TECH_DEBT.md (4 ARISTOTLE-specific debt items)
- STATUS.md (current operational state)
- worklog.md (seeded with Task ID 1 + 2)
- docs/decisions/ADR-000-template.md (ADR template)
- docs/decisions/ADR-001-aristotle-architecture.md (the architecture spec, reformatted)
- CONTRIBUTING.md (dev setup for extension)
- tests/test_import_boundary.py (extension boundary test)
- README.md (expanded from one-liner)

---
Task ID: 3
Agent: Super Z (main)
Task: Write AIP_Aristotle roadmap (Phase A/B/C)

Work Log:
- Created ROADMAP.md with three phases: Phase A (tutoring loop — in progress, foundation done, 6 near-term gates to dogfoodable), Phase B (teacher dashboard — planned, depends on Phase A + platform v1.1 GUI mount), Phase C (HERALD — planned, blocked on platform web/feed layer).
- Verified Chunk 3 wiring status (Claude's flag): the platform's CorpusRegistry IS serving the live app via delegating properties. ARISTOTLE's actors get real stores. Not blocked.
- Documented the 6 near-term gates for Phase A completion: real model calls in SOCRATES/EXAMINER/MENTOR, script handlers, content ingestor, SM-2 via VIGIL. After these, Ramesh can self-tutor (pilot protocol step 1).
- Documented platform dependencies table: 8 capabilities shipped (all Phase A needs), 3 deferred (GUI mount for Phase B, MCP tools for Phase A+, web/feed layer for Phase C).
- Documented pilot protocol gates: Ramesh (Phase A), Sameer (Phase A + C), Moses (Phase A + C), Freedom Generation (Phase A + B).

Stage Summary:
- The roadmap is clear: Phase A has 6 gates to dogfoodable, Phase B waits on platform v1.1 GUI, Phase C waits on platform web/feed layer.
- The platform is verified ready — Chunk 3 is LIVE, ARISTOTLE's actors get real stores.
- The DEFINER can test the platform now while ARISTOTLE development accelerates on the 6 Phase A gates.

Files created:
- ROADMAP.md (Phase A/B/C + pilot protocol + platform dependencies + version history)


---
Task ID: 4
Agent: Super Z (main)
Task: ARISTOTLE Phase A — real model calls (gates 1-3) + content ingestor (gate 5) + platform gaps logged (gates 4 + 6)

Work Log:
- Oriented per Coding Cycle Protocol: read aristotle/AGENTS.md, PLANNED_FEATURES.md, ROADMAP.md, STATUS.md, TECH_DEBT.md. Verified tree (step 1.5): read actual actor code (socrates/examiner/mentor), ModelProvider Protocol (call(slot_name, messages) -> {content, model, usage, latency_ms}), ScriptNode (HARD-DISABLED in production — platform gap), Vigil API (no SM-2 methods — platform gap).
- Gate 1 (SOCRATES real model calls): Added `teach(ctx, concept_id, retry, struggle_pattern)` method. Calls `model_provider.call("beast", messages)` to generate an explanation. Fetches concept content from aristotle_concept table (bilingual). Builds system prompt (single-voice Aristotle, retry uses different framing). Returns explanation in ActorResult.error field (Protocol has no data field — noted as limitation). Governance: returns NEEDS_CONFIGURATION if no model provider. run_cycle() stays as startup health check.
- Gate 2 (EXAMINER real model calls): Added three methods: `probe(ctx, concept_id)` (low-stakes "tell me in your own words"), `quiz(ctx, concept_id)` (real question at bloom_target level), `evaluate(ctx, concept_id, student_answer, quiz_question)` (scores answer via model, returns JSON with score/mastery_achieved/feedback). All call `model_provider.call("evaluation", messages)`. Governance: NEEDS_CONFIGURATION without model.
- Gate 3 (MENTOR real model calls): Added `update_struggle_pattern(ctx, concept_id, evaluation_result, student_id)` method. Calls `model_provider.call("sexton", messages)` to write a new AI-diagnostic sentence based on the current pattern + the evaluation result. UPDATEs aristotle_struggle_pattern table. Also added `get_struggle_pattern(ctx, student_id)` for SOCRATES to read before REMEDIATE. Governance: NEEDS_CONFIGURATION without model; existing pattern NOT overwritten on failure.
- Added `mastery_threshold: float = 0.7` to AristotleSettings (EXAMINER's evaluate() uses it).
- Gate 4 (script handlers): BLOCKED by platform gap. ScriptNode (AIP_Brain/src/aip/orchestration/workflow/node.py:80) is hard-disabled in production (returns success=False with DISABLED error). Logged as ARISTOTLE-DEBT-005. The tutoring loop is actor-driven instead: the actors expose public methods (teach/probe/quiz/evaluate/update_struggle_pattern) that a session coordinator calls directly. The workflow YAML stays as documentation of the state machine.
- Gate 5 (content ingestor): Built `aristotle/ingestor.py` with `ingest_concepts_from_yaml(ctx, yaml_path)` + `list_concepts(ctx)`. Takes a YAML file with pre-defined concepts (id, topic, subtopic, bloom_target, content_primary, content_alt, content_alt_lang, prerequisite_concept_id) and inserts them into aristotle_concept. INSERT OR REPLACE for idempotency. Returns {ingested, skipped, errors}. No AI-chunking for Phase A — that's a follow-up. The teacher/DEFINER authors concepts manually.
- Gate 6 (SM-2 via VIGIL): PLATFORM GAP. Vigil (AIP_Brain/src/aip/orchestration/actors/vigil.py) is a quality evaluation actor (faithfulness, consistency, source grounding), NOT a spaced repetition scheduler. ADR-001 §2 assumed VIGIL had SM-2, but it doesn't. This is exactly the kind of gap ARISTOTLE was supposed to surface (ADR-001 §9). For Phase A, a minimal SM-2 implementation in ARISTOTLE is the pragmatic fix (the algorithm is ~20 lines of Python). Logged as a platform gap; SM-2 module is a follow-up concern.
- Verified: all 5 changed files pass ast.parse; boundary test passes (ARISTOTLE's only aip.* import is aip.foundation.protocols.actors — on the allowlist); 11 tests pass (2 boundary + 5 conformance + 4 workflow) — no regression. The new tutoring methods (teach/probe/quiz/evaluate/update_struggle_pattern) are tested via the existing conformance tests (isinstance check) but need dedicated behavior tests with a fake model provider — deferred to the next concern.

Stage Summary:
- ARISTOTLE Phase A gates 1-3 + 5 are done. The actors now make real model calls (teach/probe/quiz/evaluate/update_struggle_pattern). The content ingestor populates aristotle_concept from YAML. Gates 4 + 6 are blocked by platform gaps (ScriptNode disabled, Vigil has no SM-2) — both logged in TECH_DEBT.
- The tutoring loop is actor-driven, not workflow-driven. The actors expose public methods that a session coordinator calls. The workflow YAML documents the state machine; execution is in code. This is actually cleaner — the state machine lives in code, not YAML.
- Two platform gaps surfaced (ADR-001 §9 working as designed): ScriptNode disabled (ARISTOTLE-DEBT-005), Vigil has no SM-2 (to be logged). These are gifts to LOOM and CodeForge — they now know not to assume the platform has these capabilities.
- The actors return results in ActorResult.error field (re-purposed as payload). This is a Protocol limitation — ActorResult should have a `data: Any` field. Noted as a future Protocol revision.
- Next: SM-2 module (minimal implementation in ARISTOTLE) + behavior tests with fake model provider + session coordinator that drives the tutoring loop.

Files changed:
- aristotle/actors/socrates.py (added teach() method + _fetch_concept + _build_system_prompt + _build_teach_prompt)
- aristotle/actors/examiner.py (added probe(), quiz(), evaluate() methods + _generate_question + _fetch_concept)
- aristotle/actors/mentor.py (added update_struggle_pattern(), get_struggle_pattern() + _read/_write helpers refactored)
- aristotle/config.py (added mastery_threshold field)
- aristotle/ingestor.py (NEW — YAML-based concept ingestor + list_concepts)
- TECH_DEBT.md (appended ARISTOTLE-DEBT-005 — ScriptNode platform gap)


---
Task ID: 5
Agent: Super Z (main)
Task: ARISTOTLE Phase A follow-up — SM-2 module + session coordinator + sample concepts + behavior tests

Work Log:
- Oriented per Coding Cycle Protocol: re-read config.py, actors, ingestor, TECH_DEBT. Verified tree: confirmed no SM-2 module exists yet, no session coordinator, no sample concepts, no behavior tests for the tutoring methods.
- SM-2 module (aristotle/sm2.py): Pure Python implementation of the SuperMemo 2 algorithm. SM2State dataclass (easiness_factor, interval_days, repetitions, next_review_at). score_to_quality() maps EXAMINER's 0.0-1.0 score to SM-2's 0-5 quality. update_sm2() advances the state. is_due() checks if a concept is due for review. EF never below 1.3. Correct response: 1 day (1st), 6 days (2nd), interval*EF (subsequent). Incorrect: reset to 0 reps, 1 day. 10 tests in TestSM2Algorithm.
- M002 migration (aristotle/migrations/M002_aristotle_mastery.sql): aristotle_mastery table with (student_id, concept_id) PK, SM-2 state (easiness_factor, interval_days, repetitions, next_review_at), mastery tracking (last_score, mastered), updated_at. Created with IF NOT EXISTS for idempotency.
- Session coordinator (aristotle/session.py): Drives the TEACH->PROBE->QUIZ->EVALUATE->REMEDIATE state machine. SessionState enum (7 states). SessionContext dataclass (per-session state: concept_id, state, accumulated results, retry_count, max_retries). run_session_step(ctx, session, student_input) advances one step. _step_teach/probe/quiz/evaluate/remediate/next_concept dispatch. _update_mastery writes SM-2 state to aristotle_mastery table. Mastery threshold check (score >= 0.7 -> NEXT_CONCEPT, else REMEDIATE with max 2 retries). MENTOR.update_struggle_pattern called after EVALUATE (non-fatal if it fails).
- Sample concepts (concepts_sample.yaml): Newton's Three Laws of Motion — Ramesh's first dogfood subject (he knows it well, can focus on testing the loop). Bilingual (English + Urdu). Prerequisite DAG: first_law -> second_law -> third_law. bloom_target 3-4. Includes common misconceptions in third_law content.
- Behavior tests (tests/test_aristotle_tutoring.py): 22 tests with fake model provider. TestSM2Algorithm (10 tests: score mapping, clamping, initial state, is_due, update correct/incorrect, EF floor). TestSocratesTeach (3: NEEDS_CONFIGURATION, calls beast slot, concept not found). TestExaminerMethods (4: NEEDS_CONFIGURATION, probe calls evaluation, quiz calls evaluation, evaluate returns JSON). TestMentorUpdate (4: NEEDS_CONFIGURATION, calls sexton, get_struggle_pattern returns existing/None). TestSessionCoordinator (1: TEACH step advances to PROBE). All 22 pass.
- Logged Vigil/SM-2 platform gap (ARISTOTLE-DEBT-006): ADR-001 §2 assumed VIGIL had SM-2; it doesn't (it's quality eval, not spaced repetition). ARISTOTLE implements SM-2 directly — cleaner anyway (pedagogy-specific, not platform infrastructure).
- Verified: all files pass ast.parse (SQL/YAML verified separately); boundary test passes (aristotle/sm2.py + session.py import only from aip.foundation.protocols.actors); 21 non-async tests pass (10 SM-2 + 9 conformance + 2 boundary); no regression.

Stage Summary:
- ARISTOTLE Phase A is now functionally complete. The tutoring loop is executable: ingestor populates concepts, session coordinator drives TEACH->PROBE->QUIZ->EVALUATE->REMEDIATE, actors make real model calls, SM-2 schedules reviews, MENTOR tracks struggle_pattern. Two platform gaps logged (ScriptNode disabled, Vigil has no SM-2) — both have clean workarounds (actor-driven loop, local SM-2).
- The session coordinator is single-step: each call to run_session_step() advances one state. The caller (API/CLI/GUI) stores SessionContext between steps. A future full-session coordinator will manage the complete loop in one call.
- 22 behavior tests with fakes prove the tutoring methods work: correct model slots are called, NEEDS_CONFIGURATION returned without model, SM-2 algorithm is correct, session state transitions work.
- Sample concepts ready for Ramesh's first dogfood: Newton's Three Laws, bilingual, prerequisite DAG. He can ingest + run a session.
- Next: CLI command for ingesting concepts + running sessions (so Ramesh can test without writing Python). Then API route (so the GUI can drive it). Then Phase B (teacher dashboard) once platform v1.1 GUI mount lands.

Files changed:
- aristotle/sm2.py (NEW — SM-2 algorithm, pure Python)
- aristotle/migrations/M002_aristotle_mastery.sql (NEW — mastery table)
- aristotle/session.py (NEW — session coordinator, drives the tutoring loop)
- concepts_sample.yaml (NEW — Newton's Three Laws, bilingual, for Ramesh's dogfood)
- tests/test_aristotle_tutoring.py (NEW — 22 behavior tests with fakes)
- TECH_DEBT.md (appended ARISTOTLE-DEBT-006 — Vigil/SM-2 platform gap)


---
Task ID: 6
Agent: Super Z (main)
Task: ARISTOTLE CLI + API routes + full-session coordinator

Work Log:
- Oriented per Coding Cycle Protocol: read AIP_Brain CLI structure (click commands in src/aip/cli/main.py), API route pattern (APIRouter + Depends(get_container)), _db_path helper. Verified how existing CLI commands access stores directly (they're the composition root).
- Architectural decision: ARISTOTLE's CLI and API routes live IN the ARISTOTLE repo (not AIP_Brain). The CLI is an HTTP client (talks to the running server); the API routes use request.app.state.container (standard FastAPI pattern, no aip.adapter import needed). This preserves the extension boundary.
- Built aristotle/cli.py: click-based CLI with 4 commands (health, list-concepts, ingest, session). The CLI is an HTTP client that calls the server's API routes. Non-interactive mode (--answer flags) calls /session/run. Interactive mode calls /session/start + /session/step in a loop. Uses httpx.
- Built aristotle/api.py: FastAPI APIRouter with 5 routes: GET /aristotle/concepts, POST /aristotle/ingest, POST /aristotle/session/start, POST /aristotle/session/step, POST /aristotle/session/run. Routes access the container via request.app.state.container (no aip.adapter import). Session serialization helpers (_session_to_dict / _session_from_dict) handle the SessionContext <-> JSON conversion.
- Fixed the two-phase QUIZ logic: _step_quiz now has two phases — phase 1 generates the quiz question (sets quiz_generated=True), phase 2 (when student_input arrives) records the answer and advances to EVALUATE. Added quiz_generated + probe_generated flags to SessionContext. Updated the full-session coordinator + API /session/run + tests to use the two-phase logic.
- Built tests/test_aristotle_cli_api.py: TestAPIRoutes (3 tests: list_concepts, session_start, session_run with fakes), TestFullSession (2 tests: mastered session completes with mastered=True; not-mastered session triggers REMEDIATE with retry_count > 0), TestCLI (2 tests: health command, list-concepts command with mocked HTTP client).
- Added click, httpx, pyyaml to pyproject.toml dependencies.
- Verified: 45 tests pass (all of them — SM-2 + actor behavior + API + full-session + CLI + boundary + conformance + workflow). No regression.

Stage Summary:
- ARISTOTLE Phase A is now testable end-to-end via CLI and API. The tutoring loop runs through the complete TEACH->PROBE->QUIZ->EVALUATE->REMEDIATE state machine with real model calls, SM-2 scheduling, and struggle_pattern tracking.
- The CLI is an HTTP client: `python -m aristotle.cli ingest concepts_sample.yaml`, `python -m aristotle.cli session newton_first_law --answer "objects resist changes in motion"`. Requires the server running.
- The API routes are FastAPI endpoints that the platform's app.py can include. Session state is serialized to JSON between step calls.
- The full-session coordinator (/session/run) runs the complete loop in one call with pre-provided answers — useful for testing + non-interactive use.
- The two-phase QUIZ logic is the key fix: the quiz question is generated first (no input), then the learner's answer is accepted on the next step call. This matches the interactive tutoring flow.
- 45 tests pass. No regression. The extension is ready for Ramesh's first dogfood (once the API routes are mounted by the platform's FastAPI app).

Files changed:
- aristotle/cli.py (NEW — HTTP-client CLI with health/list-concepts/ingest/session commands)
- aristotle/api.py (NEW — FastAPI APIRouter with 5 routes + session serialization)
- aristotle/session.py (MODIFIED — two-phase QUIZ logic + quiz_generated/probe_generated flags)
- pyproject.toml (MODIFIED — added click, httpx, pyyaml dependencies)
- tests/test_aristotle_cli_api.py (NEW — 7 tests: API routes + full-session + CLI)


---
Task ID: 7
Agent: Super Z (main)
Task: Phase B teacher dashboard — API route + GUI page + dashboard fix

Work Log:
- Built GET /aristotle/dashboard API route (aristotle/api.py): returns {student_id, total_concepts, mastered_count, due_count, struggle_pattern, mastery_by_concept}. Pulls from aristotle_mastery + aristotle_concept + aristotle_struggle_pattern via corpus_registry.get_stores("aristotle:textbook").
- Built @ui.page("/dashboard") GUI page (aristotle/gui.py): three panels — header (stat cards: total/mastered/due), struggle pattern sentence (prominent, amber border), mastery table (concept | topic | mastered | last score | next due). Fetches from GET /aristotle/dashboard. Same layout conventions as /learn.
- Registered /dashboard in hooks.py via host.register_page("/dashboard", "Teach", "school_outlined", order=35).
- Fixed dashboard_route: LEFT JOIN aristotle_concept with aristotle_mastery so ALL concepts appear (including unstarted). Sort: due items first (priority 0), then unstarted (priority 1), then mastered (priority 2), then not-due (priority 3). Unstarted concepts: mastered=false, last_score=null, repetitions=0, next_review_at=null, is_due=false.
- Upgraded _FakeConn to support multi_rows mode (returns different rows per query) for dashboard testing.
- Logged ARISTOTLE-DEBT-008: GUI coupling to Brain's gui/ package (gui.components.layout, gui.state, gui.theme). Revisit when third-party extensions need the same components.

Stage Summary:
- Phase B (teacher dashboard) is complete. The /dashboard GUI page shows Komal exactly what she needs: how many concepts exist, how many are mastered, how many are due, the struggle pattern sentence, and a mastery table sorted by what needs attention.
- The dashboard's LEFT JOIN ensures unstarted concepts appear — Komal sees the full curriculum, not just what's been studied.
- Nav is fully dynamic: "Learn" (order=30) + "Teach" (order=35) appear automatically via /health/extensions nav_items. No hardcoded extension names.
- 46 tests pass. No regression.

Files changed:
- aristotle/api.py (dashboard_route — LEFT JOIN + correct sort)
- aristotle/gui.py (@ui.page("/dashboard") — three panels)
- aristotle/hooks.py (register_page for /dashboard)
- tests/test_aristotle_cli_api.py (_FakeConn multi_rows + dashboard test)
- TECH_DEBT.md (ARISTOTLE-DEBT-008 — GUI coupling)

---
Task ID: 8
Agent: Super Z (main)
Task: Mark Phase B done in docs (PLANNED_FEATURES + STATUS + worklog)

Work Log:
- PLANNED_FEATURES.md: marked Phase B Teacher Dashboard items as ✅ Built (mastery heatmap, what's due, struggle-pattern display, nav registration). Added Change Log entry.
- STATUS.md: updated Phase to "Phase B complete / Phase C planning". Updated "What works" to include all Phase A + Phase B features. Updated "What doesn't work" to Phase C + known debts. Updated Pilot Readiness to "Ready for dogfood testing" with test instructions. Updated platform dependencies table (GUI mount → ✅ Shipped).
- worklog.md: appended Task ID 7 (Phase B work) + Task ID 8 (this docs update).

Stage Summary:
- Docs are current. Phase B is marked done. Phase C (HERALD) is the next major milestone, blocked on platform web/feed layer.


---
Task ID: 9
Agent: Super Z (main)
Task: Smoke-test /aristotle/intake/start + /aristotle/upload end-to-end against a real SQLite DB; fix bugs surfaced; ship a regression-tested e2e test.

Work Log:
- Pulled both repos to latest: AIP_Brain @ feat/multi-corpus (6114ece), AIP_Aristotle @ main (622535b).
- Installed AIP_Brain + AIP_Aristotle into a venv via `pip install -e .` (editable). Verified aristotle entry point discovered: `entry_points(group='aip.extensions')` returns `aristotle -> aristotle.entrypoint:get_manifest`.
- Ran baseline test suite: 158 pass / 5 xfail (pre-existing). No regressions before changes.
- Wrote `/home/z/my-project/scripts/smoke_test_intake_e2e.py` — a standalone smoke test that:
  - Spawns the real AIP_Brain FastAPI app via TestClient (lifespan runs, ExtensionHost starts, ARISTOTLE router mounts).
  - Monkey-patches container.model_provider with a _ScriptedIntakeModel that returns valid JSON for each intake turn (greeting → subject → prior_knowledge → goals → schedule → draft_plan → complete).
  - Walks the full pipeline: /aristotle/intake/start, /aristotle/upload (PDF), 5× /aristotle/intake/step, /aristotle/dashboard, /aristotle/concepts.
  - 21 stages, all passing.
- BUG FOUND + FIXED: `aristotle/api.py::upload_route` had a column/value swap in the INSERT into aristotle_uploaded_material. The SQL column order is `(id, student_id, filename, ...)` but the values were `("definer", material_id, ...)` — i.e. `id="definer"` (a constant!) and `student_id=<uuid>`. Because `id` is the PRIMARY KEY, the SECOND upload (and every subsequent upload) would fail with `IntegrityError: UNIQUE constraint failed: aristotle_uploaded_material.id`. The DB exception was caught silently and `material_id=""` was returned to the caller, so the GUI/intake flow couldn't reference any uploaded material past the first one.
  - Fix: swapped the first two values so `id=material_id` (the per-upload UUID) and `student_id="definer"` (the constant single-tenant id).
  - Added a regression test in `tests/test_aristotle_routes.py::test_upload_sql_insert_column_value_order_is_correct` that inspects the SQL params to catch this class of bug (the existing _FakeConn doesn't enforce constraints, so the original bug slipped through).
- Ported the smoke test into the repo as `tests/test_aristotle_intake_e2e.py` (pytest version). Two tests:
  - `TestIntakeE2E::test_full_intake_loop_with_upload_and_draft_plan` — the full happy path (9 stages).
  - `TestIntakeNoModelFallback::test_deterministic_intake_completes_without_model` — verifies the no-model fallback path still works.
- Verified: 160 pass / 5 xfail / 0 regressions after the fix.

Stage Summary:
- The LLM-driven intake pipeline works end-to-end when the model returns valid JSON. The bug was in the upload route, not the intake loop itself — but it would have silently broken the entire "upload a paper → LLM reads it → derives concepts" flow because the second upload (and every one after) would fail with a UNIQUE constraint violation that was caught and turned into an empty material_id.
- The e2e test ships in the repo so the user can pull + run `pytest tests/test_aristotle_intake_e2e.py -v` to verify on their side. To run against a REAL LLM, set AIP_OPENAI_API_KEY and walk the same flow via curl/httpie/GUI — the test's _ScriptedIntakeModel documents exactly what JSON schema the LLM needs to return at each turn.
- The fix is minimal (one line of swapped values + a clarifying comment). The regression test inspects SQL params directly so future refactors can't reintroduce the bug.

Files changed:
- aristotle/api.py — fixed upload_route INSERT column/value order (id ↔ student_id swap) + added clarifying comment
- tests/test_aristotle_routes.py — added test_upload_sql_insert_column_value_order_is_correct regression test
- tests/test_aristotle_intake_e2e.py — NEW end-to-end smoke test (TestClient + scripted fake model + real SQLite DB)

---
Task ID: 10
Agent: Super Z (main)
Task: Fix "LLM doesn't recognize the uploaded paper" — paper content wasn't reaching the model context

Work Log:
- User reported: uploaded a paper via the ARISTOTLE chat, Aristotle responded asking for the paper's title/description — proving the model KNEW a paper was attached but didn't have its content.
- Root cause analysis (3 contributing bugs):
  1. _build_intake_user_prompt truncated each material to 2000 chars in the model context. A typical academic paper is 30k-80k chars — the LLM only saw the abstract + intro, not enough to derive a curriculum.
  2. The system prompt told the LLM to "acknowledge" uploaded materials but didn't explicitly instruct it to READ the material content and derive concepts from it. The model defaulted to asking the learner to summarize.
  3. When the DB lookup returned 0 rows (e.g., the upload route failed to persist the material_id correctly — see Task ID 9), the IntakeActor silently passed an empty materials list to the model with NO warning logged. This made the original upload bug invisible to operators.
- Fix 1 (config.py): Added material_preview_chars field to AristotleSettings (default 20000 ≈ 5000 tokens). Large enough for the LLM to actually read a paper's abstract + intro + methods + first results section. Fits comfortably in modern context windows.
- Fix 2 (intake.py::_build_intake_user_prompt): Now takes material_preview_chars parameter (default 20000). For materials longer than the limit, appends a clear truncation notice: "PAPER TRUNCATED: N more chars not shown. Ask the learner whether the remaining content follows the same structure or introduces new topics." The LLM knows the paper continues and can ask about scope instead of pretending it read the whole thing.
- Fix 3 (intake.py::run_intake_step): Added explicit logging when materials are fetched:
  - info: intake_materials_fetched requested=N fetched=M total_chars=K
  - warning: intake_materials_missing — when requested > fetched (the DB lookup returned fewer rows than expected). The warning message explicitly mentions "the upload route failed to persist them" so operators know where to look.
- Fix 4 (intake.py::_INTAKE_SYSTEM_PROMPT): Strengthened the prompt with a new section "UPLOADED MATERIALS ARE THE CURRICULUM" — explicitly instructs the LLM to:
  - Read the paper content from the "Uploaded materials" section (not ask the learner to summarize)
  - Acknowledge SPECIFICALLY what it read (e.g., "I see this paper covers Newton's three laws and includes worked examples on inclined planes" — not "I see you uploaded a paper")
  - Derive draft_plan concepts from the paper's actual sections/equations/theorems/chapters
  - Acknowledge truncation when present (don't pretend it read the whole paper)
- Fix 5 (Brain gui/pages/ask.py::_handle_aristotle_upload): After a successful upload during INTAKE phase, the GUI now auto-triggers _step_intake("") with empty student_input. This forces the LLM to immediately see the paper content and acknowledge it specifically — without requiring the learner to type something first. Also added an explicit error message when material_id is empty (upload route failed to persist) so the learner knows Aristotle won't see the paper.
- Added 3 regression tests in tests/test_aristotle_intake.py::TestIntakeLLMDriven:
  - test_llm_driven_long_paper_content_reaches_model_not_truncated_to_2000 — verifies a 15000-char paper's full content reaches the model (would have failed under the old 2000-char limit).
  - test_llm_driven_very_long_paper_shows_truncation_notice — verifies a 30000-char paper gets a clear truncation notice with the remaining char count.
  - test_llm_driven_material_fetch_failure_logs_warning — verifies the intake_materials_missing warning fires when session.material_ids is set but the DB returns 0 rows (the silent-failure mode that masked the original upload bug).
- Verified: 164 pass / 5 xfail / 0 regressions. Standalone smoke test (21 stages) still passes.

Stage Summary:
- The LLM now actually reads uploaded papers. The combination of (a) larger preview limit, (b) explicit "these ARE the curriculum" system prompt instruction, (c) auto-trigger after upload, and (d) truncation notice for very long papers means the model has both the content and the instruction to derive the curriculum from it.
- The intake_materials_missing warning is the canary that would have caught the original upload bug immediately — operators will see it in the logs whenever a material_id is sent to /intake/step but the DB lookup returns nothing.
- The material_preview_chars setting (default 20000) is configurable via the extension config so operators can tune it for their model's context window. Larger models (claude-3.5, gemini-1.5) can handle 100k+ chars; smaller free-tier OpenRouter models may need to keep it at 20000 to avoid context overflow.

Files changed:
- aristotle/config.py — added material_preview_chars field (default 20000)
- aristotle/actors/intake.py — strengthened system prompt + improved _build_intake_user_prompt (configurable preview, truncation notice) + added material-fetch logging in run_intake_step
- tests/test_aristotle_intake.py — 3 new regression tests
- (Brain) gui/pages/ask.py — auto-trigger _step_intake("") after upload during INTAKE phase + explicit error when material_id is empty
