# Planned Features — AIP_Aristotle

> **Single source of truth for "what's built, what's planned, what's deferred."**
>
> Every agent (actor, external LLM, human, or AI assistant) MUST read this
> file BEFORE recommending changes — so no one gives advice that's already
> obsolete relative to the implementation state.
>
> **Last Updated:** 2026-06-18
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

## Change Log

| Date | Change | Agent |
|------|--------|-------|
| 2026-06-18 | Created file. Seeded with Phase A dogfood status + Near-Term/Long-Term from ADR-001 §11. | Super Z (main) |
| 2026-06-18 | Phase B (teacher dashboard) shipped: GET /aristotle/dashboard API (LEFT JOIN, all concepts, correct sort), /dashboard GUI page (3 panels: stats, struggle pattern, mastery table), nav registration ("Teach", order=35). Dashboard fix: LEFT JOIN so unstarted concepts appear + correct sort order (due → unstarted → mastered). | Super Z (main) |

---

## Cross-References

- **ADR-001** → `docs/decisions/ADR-001-aristotle-architecture.md` — the architecture spec
- **TECH_DEBT.md** → ARISTOTLE-specific debt items
- **STATUS.md** → current operational state
- **Platform ADR-014** → `AIP_Brain/docs/decisions/ADR-014-phase0-extension-host.md` — the extension contract
- **Platform PLANNED_FEATURES.md** → `AIP_Brain/PLANNED_FEATURES.md` — platform build tracker
