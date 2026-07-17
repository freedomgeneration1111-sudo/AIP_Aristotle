# Planned Features — AIP_Aristotle

> **Single source of truth for "what's built, what's planned, what's deferred."**
>
> Every agent (actor, external LLM, human, or AI assistant) MUST read this
> file BEFORE recommending changes — so no one gives advice that's already
> obsolete relative to the implementation state.
>
> **Last Updated:** 2026-06-19
> **Maintained by:** Super Z (main agent) + DEFINER review

## How to use this file

1. **Before recommending a change**, check the "Already Built" section —
   your recommendation may already be implemented.
2. **Before claiming something is "blocked" or "missing"**, check
   `TECH_DEBT.md` for the debt item's status — it may be resolved.
3. **When you ship a feature**, move it from "Near-Term" or "Long-Term"
   to "Already Built" in the same commit.
4. **When you defer a feature**, move it to "Long-Term" with the reason.

---

## Status: Already Built (Phase A dogfood)

These features are implemented and active. Recommendations to "build"
them are obsolete — the gap (if any) is operational, not architectural.

### Extension Platform Integration

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Manifest v1 | `aristotle/extension.yaml` + `aristotle/entrypoint.py` | ✅ Active | Declares textbook corpus, 3 actors, migrations, config.schema. Entry-point discoverable via `aip.extensions` group. |
| Entry-point discovery | `aristotle/entrypoint.py::get_manifest()` | ✅ Active | Loaded via `importlib.metadata.entry_points(group="aip.extensions")`. Replaces the sys.path hack. |
| Config schema | `aristotle/config.py::AristotleSettings` | ✅ Active | Dataclass with bilingual defaults (en/ur). Zero-arg construction. |
| Lifecycle hooks | `aristotle/hooks.py::on_load/on_unload` | ✅ Active | Registers SOCRATES + EXAMINER + MENTOR at stage 5. |

### Actors (ADR-001 §2)

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| SOCRATES actor | `aristotle/actors/socrates.py` | ✅ Placeholder | Verifies corpus reachability. Full teaching loop is follow-up. |
| EXAMINER actor | `aristotle/actors/examiner.py` | ✅ Placeholder | Verifies corpus + checks model availability. Degrades gracefully without model. |
| MENTOR actor | `aristotle/actors/mentor.py` | ✅ Placeholder | Reads/writes `aristotle_struggle_pattern`. Initializes placeholder if absent. |
| VIGIL (SM-2) | Reused from core | ⏳ Not wired | Core Vigil actor exists; ARISTOTLE needs to call it for spaced repetition. |

### Data Model (ADR-001 §4, §5)

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| aristotle_concept table | `migrations/M001_aristotle.sql` | ✅ Active | Concept-aware chunks with bilingual schema (content_primary + content_alt + content_alt_lang) + prerequisite_concept_id for DAG. |
| aristotle_struggle_pattern table | `migrations/M001_aristotle.sql` | ✅ Active | One persistent AI-written diagnostic sentence per student. Pre-alpha single-tenant (student_id='definer'). |
| Bilingual schema | `content_primary` + `content_alt` + `content_alt_lang` | ✅ Active | ADR-014 §1 + ADR-001 §7. Defaults: en primary, ur alt. |

### Workflow (ADR-001 §3)

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Tutoring state machine YAML | `workflows/tutoring_session_v1.yaml` | ✅ Declared | 7 nodes (teach→probe→quiz→evaluate→check_mastery→remediate→next_concept). Engine-compatible node types. |
| Workflow engine wiring | Platform-side (`container.workflow_engine`) | ✅ Active | Extensions access via `ctx.container.workflow_engine.run_workflow()`. |
| Script handlers | (not implemented) | ⏳ Not started | `aristotle_evaluate` + `aristotle_next_concept` need registration with the engine. Currently fixture/no-op mode. |

### Tests

| Feature | Implementation | Status | Notes |
|---------|----------------|--------|-------|
| Actor conformance tests | `tests/test_aristotle_actors.py` | ✅ Active | 5 conformance (isinstance + distinct names + health) + 5 behavior (fakes). |
| Workflow engine-compat tests | `tests/test_aristotle_actors.py` | ✅ Active | 4 tests: YAML parses, node types compatible, agent nodes have model_slot, condition has branches. |
| Integration tests | `tests/test_aristotle_extension.py` | ✅ Active | 7 tests: manifest validates, migrations create tables, actors register, config loads, health surfaces, stop cancels. |
| Import boundary test | `tests/test_import_boundary.py` | ⏳ This unit | Machine-enforces the extension boundary (allowlist). |

---

## Status: Near-Term (Phase A completion)

These are genuine gaps worth pursuing. They are NOT yet implemented.

### Tutoring Loop (ADR-001 §3, §11A)

| Feature | Why it matters | Effort | Dependencies |
|---------|----------------|--------|--------------|
| **Script handlers (`aristotle_evaluate`, `aristotle_next_concept`)** | Makes the workflow executable — the difference between "has a state machine" and "is a tutor" | ~1 day | Workflow engine wiring (done). Handlers update mastery, consult prerequisite DAG, update struggle_pattern. |
| **Real model calls in SOCRATES** | Currently verifies reachability but doesn't generate explanations. The tutoring loop needs actual model dispatch. | ~half day | Model provider configured on container. SOCRATES calls `ctx.container.model_provider` with the beast slot. |
| **Real model calls in EXAMINER** | Currently checks model availability but doesn't generate/score questions. | ~half day | Model provider. EXAMINER generates probe/quiz questions, scores answers. |
| **Real model calls in MENTOR** | Currently reads/initializes struggle_pattern but doesn't write AI-diagnostic sentences. | ~half day | Model provider. MENTOR calls a model to write the diagnostic sentence after EVALUATE. |
| **Content ingestor** | Populates `aristotle_concept` with real bilingual content. Concept-chunking per ADR-001 §4. | ~1-2 days | Textbook material to ingest. May surface platform gaps (concept-graph integration). |
| **SM-2 via core VIGIL** | Spaced repetition — VIGIL is reused from core, not re-implemented. | ~half day | May surface a protocol gap if Vigil's API doesn't expose what ARISTOTLE needs. |

### Teacher Dashboard (ADR-001 §8, Phase B) — ✅ BUILT

| Feature | Status | Implementation |
|---------|--------|----------------|
| **Mastery heatmap** | ✅ Built | `GET /aristotle/dashboard` returns `mastery_by_concept` with LEFT JOIN (all concepts, including unstarted). Sort: due → unstarted → mastered. `/dashboard` GUI renders as a table. |
| **What's due (SM-2)** | ✅ Built | `is_due` computed from `next_review_at` + `repetitions`. Due count in dashboard header. Due items marked ⚠ in GUI table. |
| **Struggle-pattern display** | ✅ Built | `struggle_pattern` from `aristotle_struggle_pattern` table. Prominent panel in `/dashboard` GUI (amber border, raised background). |
| **Nav registration** | ✅ Built | `host.register_page("/dashboard", "Teach", "school_outlined", order=35)` in hooks.py. Appears dynamically in layout via `/health/extensions` nav_items. |

---

## Status: Long-Term (Phase C — HERALD)

These are architecturally significant features that are designed but not
scheduled for the next 1-3 sessions. They belong in roadmap planning.

### HERALD — Field Awareness (ADR-001 §6)

| Feature | Why it matters | Dependencies |
|---------|----------------|--------------|
| **Feed poller** | Per-subject curated feeds (HIMSS, arXiv, PITB, etc.). Scheduled poll → fetch → embed → file. | Phase 0 web/feed layer (ADR-014 §3.4) — not yet built in the platform. |
| **News-to-curriculum matching** | Incoming items scored against the concept graph; SOCRATES surfaces real-world examples. | Concept graph populated (ingestor). Feed poller. |
| **Field-map graph** | Players (institutions, vendors, regulators, labs) thread into a graph over months. | Graph store (platform — built). Feed poller. |
| **Source credibility tiering** | Each item tagged by provenance (peer-reviewed > preprint > journalism > blog > speculative). | Feed poller. |

HERALD depends on the Phase 0 web layer (ADR-014 §3.4), which is not yet
built. The tutoring loop ships without it; HERALD layers on when feeds land.

---

## Status: ✅ COMPLETE (Phase B.5 — Research-Grounded Pedagogical Improvements)

Phase B.5 is the ADR-002 Rev 2 pedagogical upgrade to the existing tutoring
loop. **All 9 deliverables shipped.** The improvements landed in the
existing PREDICT → TEACH → PROBE → QUIZ → EVALUATE → [HINT_1 → HINT_2 →]
REMEDIATE state machine and the existing SOCRATES / EXAMINER / MENTOR
actors.

Source spec: `docs/decisions/ADR-002-intake-placement-learning-plan.md`
(Part A — Pedagogical Core, §§2–8).

Build order (from ADR-002 §15) — all COMPLETE:

| # | Deliverable | Status | Commit |
|---|-------------|--------|--------|
| 1 | **PREDICT step** + `aristotle_predict_event` table (M003) | ✅ | `6dfcb5d` |
| 2 | **HINT_1 / HINT_2** SessionStates + `EXAMINER.generate_hint()` | ✅ | `e75906e` |
| 3 | **Error diagnosis** in `EXAMINER.evaluate()` | ✅ | `95d00d2`+`a6cd987` |
| 4 | **Faded worked examples** in `SOCRATES.teach()` (mastery-adaptive) | ✅ | `b803ef9` |
| 5 | **Session interleaving** (concept queue with due reviews) | ✅ | `2079f0c` |
| 6 | **Transfer question type** in `EXAMINER.quiz()` | ✅ | `0352708` |
| 7 | **`aristotle_misconception_log`** (M003) + MENTOR misconception tracking | ✅ | `1be28f7` |
| 8 | **Extended mastery model** — `mastery_probability()` BKT-inspired | ✅ | `d20fd3a` |
| 9 | **`cold_start_check()`** — unassisted retrieval for mastered concepts | ✅ | `d20fd3a` |

**DEFINER decision resolved:** ADR-002 §16 #4 — `ActorResult.data` field
added to the platform Protocol (Brain commit `ce44e53`). All ARISTOTLE
actors now use `data=` (error-as-payload fully eliminated — see
ARISTOTLE-DEBT-011).

---

## Status: ✅ Backend COMPLETE / Surface Layer Planned (Phase D — Onboarding)

Phase D is the ADR-002 Rev 2 onboarding system. New learners walk through a
five-stage intake interview, take a placement calibration, and receive a
versioned long-arc learning plan that drives session selection across
weeks. Two new actors (INTAKE, PLACER), three new tables, OCR + voice
capabilities for material upload.

Source spec: `docs/decisions/ADR-002-intake-placement-learning-plan.md`
(Part B — Onboarding, §§9–13; Part C — New capabilities, §§12–13).

### Phase D Backend — ✅ COMPLETE (5 commits)

| # | Deliverable | Status | Commit |
|---|-------------|--------|--------|
| 1 | **M004 schema** (`aristotle_intake_session`, `aristotle_learning_plan`, `aristotle_placement_event`) | ✅ | `fc7c89d` |
| 2 | **INTAKE actor** + intent detection + trigger checking + API routes | ✅ | `5128caa` |
| 6 | **PLACER actor** + PlacerSession + placement calibration + API routes | ✅ | `2322f0f` |
| 8 | **Long-arc plan executor** — plan_id on SessionContext, concept queue reads from plan, cursor advances, long-arc continuation | ✅ | `228d440` |
| — | **MENTOR pattern recognition** — synthesize struggle patterns from misconception log (ADR-002 §7) | ✅ | `a72e3db` |

### Phase D Surface Layer — 🔲 PLANNED (no blockers)

Per `docs/UI_CONVENTIONS.md` (AIP_Brain): the INTAKE interview runs in
the main Brain chat — NOT a separate /intake page. Chat IS the intake
surface. ARISTOTLE registers three pages only:

| # | Deliverable | Why it matters | Dependencies |
|---|-------------|----------------|--------------|
| 3a | **`/aristotle/stats`** — mastery, misconceptions, struggle patterns | Teacher/learner analytics view. Surfaces the data already in the DB (aristotle_mastery, aristotle_misconception_log, aristotle_struggle_pattern). | Brain GUI phase: three-panel shell + ADR-014 A1 sidebar visibility. |
| 3b | **`/aristotle/map`** — concept graph, progress visualization | Visual DAG of the learning plan with mastery state per concept. | Brain GUI phase: three-panel shell. |
| 3c | **`/aristotle/settings`** — ARISTOTLE preferences | Bloom target, mastery threshold, primary/alt language, schedule. | Brain GUI phase: three-panel shell. |
| 4 | **Right panel: mastery state + concept progress** | Collapses when not in extension session (per UI_CONVENTIONS.md). Shows the current concept, mastery level, score history. | Brain GUI phase: right drawer + extension mode shift. |
| 5 | **OCR path** via `pytesseract` | Extracts text from uploaded images / scanned PDFs into the ingestor. `pypdf` for native PDFs (fix already committed — DEBT-012 resolved). | None. `pytesseract` + `Pillow` installed. Upload via Brain core + menu. |
| 7 | **Voice mode toggle** | Browser Web Speech API for STT (zero-dep path). Contributed via Brain core + menu, not a separate ARISTOTLE UI element. | None for browser path. |

**Intake via Brain chat:** The INTAKE actor (built — commit `5128caa`)
drives the conversation via the `/intake/start` and `/intake/step` API
routes (wired — commit `baf6ef2`). The Brain chat bar IS the intake
surface — ARISTOTLE does not register a separate /intake page. The
chat-primary extension pattern (UI_CONVENTIONS.md) means the chat bar
stays as the main view during intake, with ARISTOTLE's mode label in
the header.

**Status (2026-06-26):** The chat-primary GUI shipped in AIP_Brain
commit `a6f59bc` (feat/multi-corpus). `_ask_page_aristotle()` in
`gui/pages/ask.py` now drives the full INTAKE → PLACER → TUTORING
flow through a single chat surface — no concept cards, no START
button, no autostart. The chat bar is visible from the first render.
Phase label in the header (Onboarding / Placement / Tutoring /
Complete) is operator-only — the learner never sees the phase names.

**+ menu:** ARISTOTLE does not register new + menu items — Upload PDF
and Voice mode are Brain core features. ARISTOTLE consumes them via
the standard upload → OCR → ingest pipeline.

**Gate:** None. The surface layer depends on the Brain GUI phase
(three-panel shell, ADR-014 A1 sidebar visibility, extension mode shift)
which has no external blockers.

---

## Status: Platform — Planned (Pre-ADR)

Two architectural directions were discussed but are not yet fully
architected. Captured here so they don't get lost. **No ADR numbers
yet — these are pre-ADR.** A full spec must be written + reviewed as
an ADR before any implementation work begins. Both directions have no
external blockers — they ship on the AIP_Brain platform side, consumed
by ARISTOTLE + future extensions.

### Entry 1 — Extension Corpus Isolation and Access Control

**Problem:** Today every extension can reach every corpus via
`CorpusRegistry.get_stores(corpus_id)`. This works for a single
extension (ARISTOTLE) but does not scale to a multi-extension future
where extensions owned by different authors must not freely read each
other's data (e.g. a third-party "study buddy" extension must not
silently read ARISTOTLE's per-student struggle patterns without an
explicit grant).

**Default:** each extension owns its contributed corpus in isolation.
No other extension can read or write it without an explicit grant.

**Mechanism:**
- **Manifest declares corpus ownership + access requests.** An
  extension's manifest lists the corpora it contributes (owns) and the
  corpora it wants to read (or read/write) from other extensions.
- **Runtime grant table** stores approvals (operator-approved or
  auto-approved in dev mode). A grant is `(requesting_ext_id,
  target_corpus_id, access_level)` where `access_level ∈ {read,
  read_write}`.
- **Dev mode auto-approves** declared requests — keeps the dogfood
  loop friction-free. Production requires explicit operator approval
  (a platform-side admin UI or config file).
- **Enforcement at `CorpusRegistry.get_stores()`.** Add an optional
  `requesting_extension_id: str | None` param. When non-None, the
  registry checks the grant table before returning stores. `None`
  means "platform-internal call" (unrestricted — backwards-compatible).
- **`ReadOnlyCorpusStores` wrapper** enforces read grants — wraps the
  real `CorpusStores` and blocks write methods (`execute`, `commit`,
  etc.) when the grant is read-only.
- **Sensitive corpus flag (ADR-008) composes with grants** — both must
  pass. A sensitive corpus requires BOTH a grant AND the existing
  sensitivity checks. Neither alone is sufficient.
- **`ActorContext` gains `extension_id`** so actors know which
  extension they're running under. The host sets this at actor
  registration time. Actors pass it through to
  `corpus_registry.get_stores(corpus_id, requesting_extension_id=...)`.

**Backwards compatibility:** Fully backwards-compatible.
`requesting_extension_id=None` (the default) means "unrestricted
platform call" — every existing call site continues to work unchanged.
The new enforcement only activates when a non-None extension_id is
passed. ARISTOTLE + future extensions opt in incrementally.

**No external blockers.** Pre-ADR — full spec before implementation.

### Entry 2 — Actor Prompt Customization

**Problem:** Today actor prompts are hardcoded in the actor's Python
source (e.g. `SocratesActor._build_system_prompt`, `_build_teach_prompt`).
The learner (or teacher) cannot customize how Aristotle talks to them
without editing source code. Phase D's onboarding flow surfaces this —
a learner who wants Aristotle to "use cricket analogies" or "avoid
Urdu transliteration" has no way to express that.

**Design:**
- **Actor prompts composed of three layers:**
  1. **Platform-managed template** (versioned, not user-editable).
     Ships with the extension. Updated on extension version bumps.
  2. **User instructions slot** (editable). Stored per-learner (or
     per-teacher for class-wide overrides). Free text.
  3. **Optional per-actor override** on top of global extension
     instructions. Lets a learner say "for SOCRATES specifically, be
     more concise" without changing EXAMINER or MENTOR.
- **Resolution order:** `template + global_instructions + actor_override`.
  The final prompt sent to the model is the template with the user
  instructions + per-actor override appended (or injected at a marker).
- **Reset = clear user instructions, template unchanged.** A learner
  can always return to the extension's default voice.
- **Validation:** run the customized prompt through `ci_mode` before
  accepting it. Catches prompt injections or formatting that breaks
  the model's expected input shape. Reject + explain on failure.
- **DB:** `extension_actor_instructions` table —
  `(extension_id, actor_id_or_global, instructions, updated_at,
  template_version)`. `actor_id_or_global = 'global'` for the
  extension-wide instructions; a specific actor name for per-actor
  overrides.
- **Versioning:** when the platform template is updated (extension
  version bump), flag every customized actor row so the UI can prompt
  the learner: "Aristotle's default voice changed. Keep your
  customization, or reset to the new default?"
- **Ownership split:** Platform owns the mechanism (the table, the
  resolution, the validation). Extension owns its default templates.
  UI (learner settings page) ships in Phase D.

**Backwards compatibility:** Fully backwards-compatible. Empty user
instructions + empty per-actor override = the template alone (today's
behavior). The resolution layer is a no-op until a learner sets
instructions.

**No external blockers.** Pre-ADR — full spec before implementation.

---

## Change Log

| Date | Change | Agent |
|------|--------|-------|
| 2026-06-18 | Created file. Seeded with Phase A dogfood status + Near-Term/Long-Term from ADR-001 §11. | Super Z (main) |
| 2026-06-18 | Phase B (teacher dashboard) shipped: GET /aristotle/dashboard API (LEFT JOIN, all concepts, correct sort), /dashboard GUI page (3 panels: stats, struggle pattern, mastery table), nav registration ("Teach", order=35). Dashboard fix: LEFT JOIN so unstarted concepts appear + correct sort order (due → unstarted → mastered). | Super Z (main) |
| 2026-06-19 | ADR-002 Rev 2 committed (`docs/decisions/ADR-002-intake-placement-learning-plan.md`). Added Phase B.5 (research-grounded pedagogical improvements — PREDICT, hints, error diagnosis, faded examples, interleaving, transfer questions, misconception log, mastery model extension, cold-start check) and Phase D (intake, placement, long-arc plan, OCR, voice) as planned phases with their ADR-002 §15 build orders. No code changes. | Super Z (main) |
| 2026-06-19 | Added "Platform — Planned (Pre-ADR)" section with two entries: (1) Extension Corpus Isolation and Access Control — default isolation + configurable grants, enforcement at CorpusRegistry.get_stores(), ReadOnlyCorpusStores wrapper, ActorContext.extension_id, fully backwards-compatible; (2) Actor Prompt Customization — three-layer prompt composition (platform template + user instructions + per-actor override), ci_mode validation, extension_actor_instructions table, versioning on template bumps, UI in Phase D. Both pre-ADR — full spec before implementation. No code changes. | Super Z (main) |
| 2026-06-19 | **Phase B.5 ✅ COMPLETE.** All 9 deliverables shipped across 8 commits: PREDICT step (6dfcb5d), HINT ladder (e75906e), error diagnosis (95d00d2+a6cd987), faded worked examples (b803ef9), session interleaving (2079f0c), transfer questions (0352708), misconception log wiring (1be28f7), extended mastery model + cold-start check (d20fd3a). ActorResult.data migration complete for all actors (ARISTOTLE-DEBT-011 resolved). 89 tests, 0 warnings. | Super Z (main) |
| 2026-06-19 | **Phase D backend ✅ COMPLETE.** 5 deliverables shipped: M004 schema (fc7c89d), INTAKE actor + intent detection + API routes (5128caa), PLACER actor + placement calibration (2322f0f), plan executor bridge — long-arc sessions (228d440), MENTOR pattern recognition — synthesize struggle patterns from misconception log (a72e3db). Phase D surface layer (GUI, upload, OCR, voice) remains planned — no blockers. 124 tests, 0 warnings. | Super Z (main) |
| 2026-06-20 | Phase D surface layer revised per UI_CONVENTIONS.md (AIP_Brain): no /intake page — intake runs in Brain main chat. ARISTOTLE registers three pages only: /aristotle/stats, /aristotle/map, /aristotle/settings. Right panel: mastery state + concept progress. + menu: no new items (upload + voice are Brain core). No code changes. | Super Z (main) |
| 2026-06-20 | **GUI Phase ✅ COMPLETE.** All surface layer deliverables shipped: stats page (/aristotle/stats), learning map (/aristotle/map), settings page (/aristotle/settings, save wired), teacher dashboard (/aristotle/teacher + session-history route), curiosity path — open learner model (ADR-002 Amendment A1), expanded upload endpoint (txt/md/csv/html/json/yaml/docx + images), Brain + menu upload and voice wired. 149 tests, 0 warnings. ADR-001 Research Annex committed. | Moses + Claude (main) |
| 2026-07-15 | **Task 19 ✅ COMPLETE — GUI port fix + plan picker (ADR-004 GUI half).** Two bugs, one fix each. (1) `aristotle/gui/api_client.py` was hardcoded to `http://localhost:8001` — a port nothing was listening on. Aristotle's API is mounted on Brain's backend at :8000 (extension_api_router_mounted ext='aristotle'). Every dashboard / stats / map / settings / session-history call silently failed into {} / [] and the GUI rendered empty. Fixed: `_BASE` now reads `ARISTOTLE_BACKEND_URL` → `AIP_BACKEND_URL` → `http://127.0.0.1:8000` (same env var + default as Brain's `gui/pages/ask.py`). (2) `_ask_page_aristotle` in `AIP_Brain/gui/pages/ask.py` unconditionally called `_start_intake()` with `plan_id=None` on page load — every page load started fresh intake, existing plans were undiscoverable, "resume my lessons" went into the intake as a regular reply. Fixed: page load now calls `_show_plan_picker()` which fetches existing plans via `GET /aristotle/plans` (Task 18 API) and renders a Resume/Start-New picker. Clicking Resume calls `/intake/start` with the plan_id; `check_intake_triggers()` returns `trigger=None` for healthy plans (skip intake, jump straight to PLACER) or the right re-engagement trigger for stale/completed plans. Chat bar gated while picker is showing so typing "resume my lessons" can't bypass the picker. 208 passed / 5 xfailed / 0 failures (unchanged from Task 18). | Super Z (main) |
| 2026-07-15 | **Task 20 ✅ COMPLETE — DELETE plan route + plan-scoped GUI pages + delete affordance.** Two gaps in Task 18-19 work: (A) no way to delete a plan (Task 19's picker surfaced duplicates from before the picker existed, but the user couldn't remove them); (B) three GUI pages (Curriculum Map, Session Stats, Teacher Dashboard) called get_concepts()/get_mastery()/etc. completely unscoped — every subject's concepts mixed into one list. Part A: new `DELETE /aristotle/plans/{plan_id}` route — explicit cascade in dependency order (placement_event → intake_session → per-concept mastery/predict/misconception → concept → plan_job → learning_plan), single transaction with rollback, does NOT touch aristotle_uploaded_material (material may be shared). Returns {deleted, plan_id, subject, concepts_deleted, cascade_rows_deleted}. 404 on unknown plan_id. Part B1: api_client.py helpers gained `plan_id` param (get_mastery, get_concepts, get_misconceptions, get_struggle_patterns, get_session_history) + new `delete_plan()` helper. Settings helper deliberately NOT given plan_id — confirmed aristotle_settings (M005) is keyed by student_id only, no plan_id column; per-plan scoping there would be fake. Part B2: /aristotle/stats and /aristotle/map get a plan selector (defaults to most-recently-active plan); /aristotle/teacher gets an optional "All subjects" filter (default preserves cross-subject aggregation) + subject labels on every row ("Unlabeled" for pre-Task-18 legacy rows); /aristotle/settings gets NO selector (settings are student-global). GET /dashboard response's mastery_by_concept[] rows now include plan_id (8th SELECT column) so the dashboard can label rows without a second lookup. Part B3: _show_plan_picker() in ask.py now renders a trash icon next to each Resume button; click opens a modal dialog with "Delete permanently" + "Cancel" — two-step confirm, no one-click delete on live student data. 213 passed / 5 xfailed / 0 failures (was 208, +5 new DELETE route tests). | Super Z (main) |
| 2026-07-16 | **Task 21 ✅ COMPLETE — Tutoring session-quality fixes (examiner resilience + teach prompt length + intake idempotency).** Five surgical fixes from a live dogfood session (pharmacognosy material) + one investigation-only item. **Fix 1** (`examiner.py::evaluate()`): strip markdown ```json fences before `json.loads()` — the evaluation model sometimes wraps its JSON in a code fence, which scored the student 0.0 for a valid response. Added `_strip_json_fences()` helper. **Fix 2** (`examiner.py::_generate_question()`, shared by `probe()` + `quiz()`): `model_provider.call()` does NOT raise on 429 — it returns `{"error": True, "content": ""}`. Previous code only caught raised exceptions, so a rate-limited call fell through to empty-string question, logged as `examiner_probe_ok ... question_len=0` (success!). Added the canonical `_model_call_failed()` check (matches `ModelSlotResolver`'s `primary_failed` pattern) + retry-with-backoff helper `_call_with_retry()` (max_retries=2, 1s then 2s sleep — same shape as `intake.py`'s beast-slot loop). **Fix 3** (`examiner.py::evaluate()`): same root cause as Fix 2, different symptom. A rate-limited evaluation-slot call returned empty content, indistinguishable from genuinely-malformed JSON — both fell into the same score=0.0 fallback. Infrastructure flakiness was scoring the student's answer as WRONG. Now `_call_with_retry()` runs first; only after retries are exhausted does it fall through to score=0.0. **Fix 4** (`socrates.py::_build_system_prompt()`): confirmed via full-file grep — no length/register constraint anywhere. The `full_worked_example` instruction "Show every step — do not skip anything" had no counterweight, producing ~5000-char explanations. Added a `LENGTH AND REGISTER` block to the base prompt (2-4 short paragraphs max, plain everyday English, short sentences — applies across ALL fading modes). Softened `full_worked_example` to "Show every step, but keep each step to 1-2 sentences — completeness of steps, not verbosity per step." **Fix 5** (`intake.py::run_intake_step` COMPLETE-trigger block, must-have only): added post-COMPLETE idempotency guard — if `session.state == COMPLETE and session.plan_id`, return the existing plan_id without re-launching `create_plan_job`/`generate_plan_pipeline`. Blocks the dogfood-session duplicate-plan bug (re-confirmation message ~20 min after first COMPLETE fired a SECOND background pipeline). The secondary in-flight race (second COMPLETE WHILE the first pipeline is still running, before plan_id is set) is **flagged back per Task 21 prompt** — closing it requires disambiguating "PLAN_DRAFT focus, GENERATING_PLAN state" (legitimate first-time COMPLETE) from "pipeline in-flight, GENERATING_PLAN state" (re-trigger), which exceeds the one-line-guard scope. See the long comment at the COMPLETE-trigger block in `aristotle/actors/intake.py` for the three structural fixes that would close the gap. **Investigation item** (NO code changes per prompt): confirmed bug in `/aristotle/session/step` route — its `output` field only reads `result.data.get("prompt", "")`, but `_step_teach()` returns `data.explanation`, `_step_probe()`/`_step_quiz()` return `data.question`. TEACH/PROBE/QUIZ outputs are silently dropped by the API; only PREDICT reaches the chat UI. Proposed minimal fix (one expression, NOT implemented): `result.data.get("prompt") or result.data.get("explanation") or result.data.get("question") or result.data.get("feedback") or ""`. Full report at `docs/investigations/task-21-ask-py-teach-rendering.md`. 225 passed / 1 skipped / 5 xfailed / 0 failures (was 213/1/5/0; +12 new tests: 8 in `TestExaminerResilience`, 3 in `TestSocratesPromptLength`, 1 in `TestIntakeLLMDriven`). | Super Z (main) |
| 2026-07-16 | **Task 22 ✅ COMPLETE — Fix /session/step output field (TEACH/PROBE/QUIZ outputs were silently dropped).** The Task 21 investigation item confirmed the bug; Task 22 fixes it. **Fix 1** (`api.py::session_step_route`): the `output` field was computed as `result.error or result.data.get("prompt", "") or ""` — only ever read the `prompt` key. But `_step_teach()` returns `data.explanation`, `_step_probe()`/`_step_quiz()` return `data.question`, `_step_evaluate()` returns `data.feedback` — only `_step_predict()` uses `data.prompt`. Every step except PREDICT was silently dropped from the chat UI. Fixed: `output = data.get("prompt") or data.get("explanation") or data.get("question") or data.get("feedback") or ""`. Order matches the tutoring state-machine order (PREDICT, TEACH, PROBE/QUIZ, EVALUATE) for traceability. **Fix 1 contract fix** (`session.py::_step_evaluate`): while writing the EVALUATE regression test, discovered `_step_evaluate` re-wrapped its return as `ActorResult(ok=True, error=session.last_evaluation)` — the legacy error-as-payload pattern from pre-Phase-B.5. This put the JSON string in `error` and left `data=None`, so the API couldn't read `data.feedback` even after Fix 1. Changed to `ActorResult(ok=True, error=session.last_evaluation, data=eval_data)` — keeps `error=` for backward compat, adds `data=` as the canonical channel. **Fix 2** (`api.py::session_step_route`): when `not result.ok`, `output` was `""` — the student's screen went blank. After Task 21 Fix 3, `evaluate()` legitimately returns `ok=False` on an exhausted-retry infra failure (429), so this case now happens in production. Fix: return a short, honest, non-alarming student-facing message ("I had trouble with that just now — could you send your answer again?") instead of `""`. The raw `result.error` is still in the separate `"error"` key for logs — never exposed to the student in `output`. **Tests**: 5 new integration-level tests in `tests/test_aristotle_routes.py` hitting `/session/step` directly (TEACH, PROBE, EVALUATE, PREDICT regression, ok=False infra failure). These are the regression tests that should have existed already — the whole reason the bug shipped unnoticed at Task 21 is that no test exercised the actual API response shape for anything but PREDICT. **ARISTOTLE-DEBT-013 resolved.** 230 passed / 1 skipped / 5 xfailed / 0 failures (was 225/1/5/0; +5 new tests, no regressions). | Super Z (main) |
| 2026-07-16 | **Task 23 ✅ COMPLETE — Decline-to-answer gate + mastery-scoring integrity + audit trail.** A live placement run showed the tutor walking through 7 concepts while the student answered "no. teach me about it." to every single one — and the system kept advancing to a NEW concept each time instead of teaching any of them. Root cause: the free-tier model mishandled refusal-style non-content answers and self-reported `mastery_achieved=true`, so `run_placer_step` wrote permanent `aristotle_mastery` rows (repetitions=3, mastered=1) for concepts the student explicitly said they didn't know. **Fix 1** (`examiner.py::evaluate()`): added `_DECLINE_PATTERNS` (tight frozenset) + `_is_decline_to_answer()` helper. At the top of `evaluate()`, BEFORE any model call, the gate checks the normalized student_answer. If it matches: skip the model call entirely, return `score=0.0, mastery_achieved=False` with a gentle "No problem — let's move on to teaching this one." feedback. Lives INSIDE `evaluate()` so every caller (placement, quiz-check, any future caller) gets it for free. Pattern list intentionally tight — false negatives safe, false positives dangerous. A genuine content answer beginning with "no" (e.g. "no, it binds non-covalently") is NOT gated (regression test pins this). **Fix 2** (`examiner.py::evaluate()`): replaced `bool(eval_data.get("mastery_achieved", False))` with `parsed_score >= mastery_threshold`. The model's self-reported boolean is now IGNORED — only its `score` is trusted. Closes the gap where a model could return an internally inconsistent response (e.g. `score=0.3, mastery_achieved=true`) and have it pass through unverified. **Fix 3** (new migration `M010_placement_event_answer_text.sql` + INSERT update in `run_placer_step`): added nullable `student_answer TEXT` column to `aristotle_placement_event`. `run_placer_step` now writes the raw `student_input` there. Old rows have NULL (correct — old answers were never captured). This is the audit-trail foundation. **Fix 4** (read-only audit + targeted reset scripts in `scripts/`): `audit_placement_mastery.py` takes a `plan_id`, prints every `aristotle_mastery` row with `mastered=1` joined against its originating `aristotle_placement_event` row, showing concept_id/score/assessed_at/student_answer. `reset_mastery_for_concepts.py` takes a `plan_id` + an EXPLICIT list of `concept_id`s and deletes those specific mastery rows — NEVER a blanket plan-wide reset. Has `--dry-run` + interactive TTY confirmation. **No automated bulk cleanup** — there isn't enough retroactive data to safely automate identifying corrupted rows; Moses audits the output and feeds an explicit list to the reset script. 240 passed / 1 skipped / 5 xfailed / 0 failures (was 230/1/5/0; +10 new tests: 4 in `TestExaminerDeclineGate`, 4 in `TestExaminerMasteryDerivation`, 1 placer audit-trail test, 1 M010 migration test). No regressions. | Super Z (main) |
| 2026-07-17 | **Task 24 ✅ COMPLETE — Wire intent classification into placement (fixes the off-topic-question screenshot).** Two screenshots from a live placement session showed the tutor completely ignoring on-topic student questions ("give me a rundown on our learning plan", "this is my second session. i want to be oriented in what we are learning next") and instead asking a brand-new unrelated probe question each time. Root cause: `session.py` already had a working intent-classification system (`_classify_student_input`, ADR-002 Amendment A1) that routes student input into ANSWER/QUESTION/TANGENT/CHAT with `_step_curiosity` and `_step_chat` handling the non-ANSWER cases — but it was never called from `run_placer_step()`. Every placement input, including genuine questions, went straight to `examiner.evaluate()` to be graded as a content answer. **Fix 1** (`session.py`): refactored `_step_curiosity` + `_log_curiosity_event` to take explicit `concept_id` + `session_id` keyword args (default `None`, falling back to `session.concept_id` / `_derive_session_id(session)` for backward compat). This lets them be called from placement, which uses `PlacerSession` (no `concept_id` or `student_id` field). Also made `_step_chat`'s log line defensive (`getattr(session, "concept_id", None)`). Existing `SessionContext` call path needed zero changes; all 7 existing curiosity tests pass unchanged. **Fix 2** (`session.py::_classify_student_input`): sentence-level pattern matching — input split into clauses on `.`, `!`, `?`, newline, each trigger phrase checked against the START of EACH clause, not just the whole input. Catches "this is my second session. i want to be oriented..." where the question-like part is in the second clause. Added new `question_starters`: "give me", "show me", "walk me through", "orient me", "remind me", "help me understand". Added new `tangent_markers`: "i want to", "i'd like to", "i wanted to". Existing whole-string `endswith("?")` check preserved (additive). "let's see" deliberately left out (too likely to be a genuine tentative answer). 7 new tests including the 2 EXACT screenshot inputs as literal test cases + a regression test confirming "give up? no, resins are..." still classifies as ANSWER. **Fix 3** (`intake.py::run_placer_step`): in the Phase 2 branch, classify `student_input` BEFORE `examiner.evaluate()`. QUESTION/TANGENT → `_step_curiosity` (answer, don't advance, same probe stays pending). CHAT → `_step_chat` (acknowledge, don't advance). ANSWER → existing evaluate path, unchanged. Placer passes `concept_id` + a placement-derived `session_id` (`"placer:{plan_id}:{concept_id}"`) so the curiosity event log row is written correctly. 5 new tests: QUESTION/TANGENT/CHAT inputs don't advance or call evaluate; ANSWER input still reaches evaluate (regression); question-then-answer sequence verifies non-advancing behavior leaves the session ready for the next real answer. 252 passed / 1 skipped / 5 xfailed / 0 failures (was 240/1/5/0; +12 new tests, no regressions). **Flagged back**: the task spec said `_step_chat()` needs no changes, but it reads `session.concept_id` in a log line — made it defensive with `getattr` so it tolerates a `PlacerSession`. | Super Z (main) |
| 2026-07-17 | **Task 25 ✅ COMPLETE — Surface curiosity answers through the placement route and frontend.** Task 24's backend logic was correct and confirmed working via live logs (`curiosity_path concept=pharmacognosy_000 intent=QUESTION response_len=1092`), but the curiosity answer was dropped two layers above: (1) `placer_step_route` built its HTTP response from only `state`/`question`/`concepts_placed` — never read the `"response"` key that `run_placer_step`'s Phase 2 QUESTION/TANGENT/CHAT branch now returns; (2) `ask.py`'s `_step_placer` only checked `data.get("question")` which was always present (echoes the still-pending probe), so it re-rendered the probe instead of the answer. **Fix 1** (AIP_Aristotle `api.py::placer_step_route`): added `"response": result.get("response")` + `"intent_class": result.get("intent_class")` to the HTTP response dict. Both are `None` for normal ANSWER turns — existing behavior unchanged. Updated the route docstring. 2 new integration-level tests in `TestPlacerRoutes`: QUESTION input returns non-empty `response` + `intent_class="QUESTION"`; normal ANSWER returns `None` for both (regression). These are the tests that would have caught the bug the first time — they hit the route function directly, not just `run_placer_step`. **Fix 2** (AIP_Brain `gui/pages/ask.py::_step_placer`): reads `data.get("response")` and, if present, renders it IN PLACE OF the question (`elif curiosity_response:` before `elif question:`). The curiosity response already includes a natural weave-back ("Want to keep exploring this, or shall we continue where we left off?") so rendering it alone is sufficient — no need to also paste the raw question text. No test — `ask.py`'s NiceGUI rendering functions are not unit-tested in the Brain suite; manually traced the code path. **Weave-back wording decision**: already handled by `_step_curiosity`'s existing `full_response` append — rendered as-is, no flag-back. **Duplicate-send investigation**: the screenshot's duplicate message is explained by the bug itself (no visible response → reasonable retry), NOT a separate frontend double-submit. However, `ask.py`'s `_on_aristotle_send` has NO disable-while-pending guard — both Send button and Enter key call `asyncio.create_task(_on_aristotle_send())` with no guard against concurrent sends. NOT a one-line obviously-safe fix (needs try/finally + multiple early-return paths). Flagged back, not fixed. 254 passed / 1 skipped / 5 xfailed / 0 failures in AIP_Aristotle (was 252/1/5/0; +2 new tests). AIP_Brain: no test changes (GUI code not unit-tested), `ask.py` parses cleanly. | Super Z (main) |

---

## Cross-References

- **ADR-001** → `docs/decisions/ADR-001-aristotle-architecture.md` — the architecture spec
- **ADR-002** → `docs/decisions/ADR-002-intake-placement-learning-plan.md` — intake, placement, long-arc plan, and the Phase B.5 pedagogical upgrades
- **TECH_DEBT.md** → ARISTOTLE-specific debt items
- **STATUS.md** → current operational state
- **Platform ADR-014** → `AIP_Brain/docs/decisions/ADR-014-phase0-extension-host.md` — the extension contract
- **Platform PLANNED_FEATURES.md** → `AIP_Brain/PLANNED_FEATURES.md` — platform build tracker
