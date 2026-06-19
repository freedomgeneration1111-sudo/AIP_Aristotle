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

## Status: Planned (Phase B.5 — Research-Grounded Pedagogical Improvements)

Phase B.5 is the ADR-002 Rev 2 pedagogical upgrade to the existing tutoring
loop. **It is net-new and can start immediately** — none of its deliverables
depend on the platform web/feed layer (Phase C gate) or on the
intake/placement system (Phase D). The improvements land in the existing
TEACH → PROBE → QUIZ → EVALUATE → REMEDIATE state machine and the existing
SOCRATES / EXAMINER / MENTOR actors.

Source spec: `docs/decisions/ADR-002-intake-placement-learning-plan.md`
(Part A — Pedagogical Core, §§2–8).

Internal build order (from ADR-002 §15):

| # | Deliverable | Why it matters | Dependencies |
|---|-------------|----------------|--------------|
| 1 | **PREDICT step** in `session.py` + `aristotle_predict_event` table (M003 partial) | Pre-teaches a prediction; commits the learner before exposure. New SessionState, new schema, new EXAMINER method. | None beyond Phase A. |
| 2 | **HINT_1 / HINT_2** SessionStates + `EXAMINER.generate_hint()` | Replaces the "give up and remediate" cliff with a 2-rung hint ladder before REMEDIATE. | None. |
| 3 | **Error diagnosis** in `EXAMINER.evaluate()` | EVALUATE returns a structured error type (procedural vs. conceptual vs. misread), not just a score. Feeds MENTOR. | None. |
| 4 | **Faded worked examples** in `SOCRATES.teach()` | Worked example → partially completed → learner completes. Reduces cognitive load on first exposure. | None. |
| 5 | **Session interleaving** in the session coordinator | Mix new concepts with review of due concepts (SM-2 schedule), not blocked-by-prerequisite linear. | Existing SM-2 + concept DAG (built). |
| 6 | **Transfer question type** in `EXAMINER.quiz()` | Recognition vs. transfer — different quiz types for different Bloom levels. | None. |
| 7 | **`aristotle_misconception_log`** table (M003) + MENTOR misconception tracking | MENTOR stops writing a single diagnostic sentence and starts tracking a structured misconception history per student/concept. | None. |
| 8 | **Extended mastery model** (M003 addition to `aristotle_mastery`) | Adds BKT-inspired fields (probability of mastery, last cold-check timestamp). Replaces pure SM-2 with a hybrid. | Existing `aristotle_mastery` (built). |
| 9 | **`cold_start_check()`** in EXAMINER | Periodically re-verifies "mastered" concepts unassisted. Catches overreliance on hints / memorization. Recommended frequency: every 5th session per concept once mastered. | Extended mastery model (item 8). |

**Gate:** None. Phase B.5 can ship incrementally alongside Phase D.

**Open DEFINER decisions blocking Phase B.5** (ADR-002 §16):
- #4: `ActorResult.data` field — add to platform Protocol (breaking change) or keep error-as-payload? **Recommended:** add `data: Any = None`.

---

## Status: Planned (Phase D — Onboarding: Intake + Placement + Long-Arc Plan)

Phase D is the ADR-002 Rev 2 onboarding system. New learners walk through a
five-stage intake interview, take a placement calibration, and receive a
versioned long-arc learning plan that drives session selection across
weeks. Two new actors (INTAKE, PLACER), three new tables, OCR + voice
capabilities for material upload.

Source spec: `docs/decisions/ADR-002-intake-placement-learning-plan.md`
(Part B — Onboarding, §§9–13; Part C — New capabilities, §§12–13).

**Core Phase D has no external dependencies.** Web search unlocks material
sourcing (fetching a PDF from a publisher's page); the intake/placement
loop itself runs without it.

Internal build order (from ADR-002 §15):

| # | Deliverable | Why it matters | Dependencies |
|---|-------------|----------------|--------------|
| 1 | **M003 full schema** (`aristotle_learning_plan`, `aristotle_placement_event`, `aristotle_intake_session` + the B.5 tables) | Versioned, append-only long-arc plan + placement history + intake session log. | None. |
| 2 | **INTAKE actor** (ADR-002 §11) + intake session API route | Conducts the five-stage intake interview (context, goals, prior exposure, time, friction). Uses the `synthesis` slot. | None. |
| 3 | **INTAKE GUI page** at `/intake` | Conversational UI mirroring the tutoring loop's polish. | Platform v1.1 GUI mount (built). |
| 4 | **`ui.upload` for PDF + image** | Learner uploads their textbook / worksheet / handwritten problem. | Platform NiceGUI `ui.upload` (built). |
| 5 | **OCR path** via `pytesseract` | Extracts text from uploaded images / scanned PDFs into the ingestor. `pypdf` for native PDFs (DEBT-012 resolved). | None. `pytesseract` + `Pillow` installed (ADR-002 §17). |
| 6 | **PLACER actor** (ADR-002 §11) + placement API route | Calibrates starting mastery per concept after intake. Writes `aristotle_placement_event`. Uses the `evaluation` slot. | INTAKE actor (item 2). |
| 7 | **Voice mode toggle** | Browser Web Speech API for STT (zero-dep path). Optional Whisper slot for Urdu / noisy sessions. | None for browser path. Whisper needs platform model slot. |
| 8 | **Long-arc plan executor** | Session coordinator consults the versioned `aristotle_learning_plan` to pick the next concept + session type. | M003 schema (item 1) + Phase B.5 cold-start check (B.5 item 9). |

**Gate:** None for core (items 1–7). Item 8 (long-arc plan executor) benefits
from Phase B.5's cold-start check but can ship a simpler version first.

**Open DEFINER decisions blocking Phase D** (ADR-002 §16):
- #1: Backup strategy A/B/C (ADR-014 §9.7) — **blocking**. Recommended: Option A.
- #2: OCR quality — `pytesseract` (local, free) vs vision model slot. Recommended: `pytesseract` for Phase D.
- #3: Voice STT — browser Web Speech API (zero-dep) vs Whisper slot (Urdu, noisy). Recommended: browser for Phase D.
- #5: Intake conversation language — English-only intake, bilingual tutoring? Recommended: English-only intake for Phase D.

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

---

## Cross-References

- **ADR-001** → `docs/decisions/ADR-001-aristotle-architecture.md` — the architecture spec
- **ADR-002** → `docs/decisions/ADR-002-intake-placement-learning-plan.md` — intake, placement, long-arc plan, and the Phase B.5 pedagogical upgrades
- **TECH_DEBT.md** → ARISTOTLE-specific debt items
- **STATUS.md** → current operational state
- **Platform ADR-014** → `AIP_Brain/docs/decisions/ADR-014-phase0-extension-host.md` — the extension contract
- **Platform PLANNED_FEATURES.md** → `AIP_Brain/PLANNED_FEATURES.md` — platform build tracker
