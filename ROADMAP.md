# AIP_Aristotle Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-18
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (Phase A dogfood)

---

## How to Read This Document

Status indicators:
- ✅ COMPLETE — built, tested, in use
- ⏳ IN PROGRESS — actively being built
- 🔲 PLANNED — decided, not yet started
- 💡 PROPOSED — under consideration, not yet decided
- ❌ DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`. When a decision changes
the roadmap, update both documents. The platform's ADR-014 (extension contract)
is the upstream dependency — read it before changing anything that touches the
extension boundary.

---

## Phase A — Tutoring Loop (ships first)
*The dogfood drop. Proves the platform contract end-to-end.*
*Status: ⏳ IN PROGRESS (platform integration done; actors are placeholders)*

### What's done (Phase A foundation)

| Feature | Status | Notes |
|---------|--------|-------|
| Extension manifest v1 + entry-point discovery | ✅ | `extension.yaml` + `entrypoint.py:get_manifest()`. Pip-installable. |
| Config schema (AristotleSettings) | ✅ | Dataclass, bilingual defaults (en/ur). |
| Migration (M001_aristotle.sql) | ✅ | `aristotle_concept` + `aristotle_struggle_pattern` tables. Bilingual schema. |
| SOCRATES actor | ✅ placeholder | Conforms to Actor Protocol. Verifies corpus reachability. No real model calls yet. |
| EXAMINER actor | ✅ placeholder | Conforms to Actor Protocol. Checks model availability, degrades gracefully. |
| MENTOR actor | ✅ placeholder | Conforms to Actor Protocol. Reads/writes `aristotle_struggle_pattern`. |
| Tutoring state machine workflow | ✅ declared | 7 nodes (teach→probe→quiz→evaluate→check_mastery→remediate→next_concept). Engine-compatible. |
| Import boundary test | ✅ | `tests/test_import_boundary.py` — self-defending SoC. |
| Convention framework | ✅ | AGENTS.md, PLANNED_FEATURES, TECH_DEBT, STATUS, worklog, ADRs, CONTRIBUTING. |

### Near-term (Phase A completion — path to dogfoodable)

These are the gates between "platform contract proven" and "Ramesh can self-tutor a chapter."

| # | Feature | Why it matters | Effort | Dependencies |
|---|---------|----------------|--------|--------------|
| 1 | **Real model calls in SOCRATES** | Currently verifies reachability but doesn't generate explanations. The tutoring loop needs actual model dispatch. | ~half day | Model provider configured on container. SOCRATES calls `ctx.container.model_provider` with the beast slot. |
| 2 | **Real model calls in EXAMINER** | Currently checks model availability but doesn't generate/score questions. | ~half day | Model provider. EXAMINER generates probe/quiz questions, scores answers. |
| 3 | **Real model calls in MENTOR** | Currently reads/initializes struggle_pattern but doesn't write AI-diagnostic sentences. | ~half day | Model provider. MENTOR calls a model to write the diagnostic sentence after EVALUATE. |
| 4 | **Script handlers** (`aristotle_evaluate`, `aristotle_next_concept`) | Makes the workflow executable — the difference between "has a state machine" and "is a tutor." | ~1 day | Workflow engine wiring (done, platform-side). Handlers update mastery, consult prerequisite DAG, update struggle_pattern. |
| 5 | **Content ingestor** | Populates `aristotle_concept` with real bilingual content. Concept-chunking per ADR-001 §4. | ~1-2 days | Textbook material to ingest. May surface platform gaps (concept-graph integration). |
| 6 | **SM-2 via core VIGIL** | Spaced repetition — VIGIL is reused from core, not re-implemented. | ~half day | May surface a protocol gap if Vigil's API doesn't expose what ARISTOTLE needs. |

**After items 1–6:** Ramesh can self-tutor a chapter he already knows (ADR-001 §10 pilot protocol step 1). This is the dogfood moment.

### What's NOT blocking Phase A

- ❌ Platform GUI mount (v1.1, stage 4) — ARISTOTLE is backend-testable via API/CLI now. The GUI learning view is v1.1.
- ❌ Platform McpToolRegistry (v1.2) — the calculator/step-checker tools (ADR-001 §9) are Phase A+ but not needed for the basic tutoring loop.
- ❌ Platform web/feed layer (ADR-014 §3.4) — HERALD is Phase C.

---

## Phase B — Teacher Dashboard
*Read-views into every student's state — leverage, not surveillance.*
*Status: 🔲 PLANNED (depends on Phase A completion + platform v1.1 GUI mount)*

The one place the actor decomposition is visible (ADR-001 §8). Komal's scarce
human time goes where only a human can go; the tutor absorbs infinite patient
repetition.

| Feature | Why it matters | Dependencies |
|---------|----------------|--------------|
| Mastery heatmap | Komal sees per-student mastery per concept. Read-view into MENTOR's data. | Real MENTOR data (Phase A completion). Platform v1.1 GUI mount. |
| What's due (VIGIL) | Komal sees what's due for each student. Read-view into VIGIL's SM-2 schedule. | SM-2 wiring (Phase A completion). |
| Struggle-pattern display | Komal sees the diagnostic sentence per student. | Real MENTOR data. |
| Avoidance / readiness indicators | Komal sees which students are avoiding topics / ready to advance. | Mastery data + concept graph. |

**Gate:** Phase B requires the platform's v1.1 GUI mount (stage 4). The backend
data is available once Phase A is complete; the GUI surface is the platform dependency.

---

## Phase C — HERALD (Field Awareness)
*Source-first news tied to the curriculum. Builds the who's-who/what's-what.*
*Status: 🔲 PLANNED (depends on platform web/feed layer — not yet built)*

HERALD turns a textbook tutor into a *field apprenticeship*: the learner
absorbs not just concepts but the living landscape they sit in (ADR-001 §6).

| Feature | Why it matters | Dependencies |
|---------|----------------|--------------|
| Feed poller | Per-subject curated feeds (HIMSS, arXiv, PITB, etc.). Scheduled poll → fetch → embed → file. | **Platform web/feed layer (ADR-014 §3.4) — not yet built.** This is the hard gate. |
| News-to-curriculum matching | Incoming items scored against the concept graph; SOCRATES surfaces real-world examples. | Concept graph populated (ingestor). Feed poller. |
| Field-map graph | Players (institutions, vendors, regulators, labs) thread into a graph over months. | Graph store (platform — built). Feed poller. |
| Source credibility tiering | Each item tagged by provenance (peer-reviewed > preprint > journalism > blog > speculative). | Feed poller. |

**Gate:** Phase C is blocked on the platform's web/feed layer (ADR-014 §3.4).
The tutoring loop ships without HERALD; HERALD layers on when feeds land.

---

## Pilot Protocol (ADR-001 §10)

1. **Ramesh** (idea originator) — self-tutors a chapter he already knows. He'll break it in the right ways first. **Gate: Phase A complete.**
2. **Sameer** (SAICH, health IT) — loads pharmacy/HIS material; HERALD feeds Pakistani medical-records industry news → he builds the field's who's-who from day one. **Gate: Phase A complete + Phase C (HERALD).**
3. **Moses** (DEFINER) — physics + AI foundations under existing intuitions (EZ water, NBCM, AGI); HERALD serves source-first field awareness with credibility tiering → foundation + calibration, and the raw material to become a primary voice rather than a downstream one. **Gate: Phase A complete + Phase C (HERALD).**
4. **Freedom Generation School** — supervised classroom pilot with Komal's dashboard. **Gate: Phase A + Phase B (teacher dashboard).**

---

## Platform Dependencies

ARISTOTLE depends on these platform capabilities (all in AIP_Brain `feat/multi-corpus`):

| Platform capability | ADR | Status | ARISTOTLE phase |
|---------------------|-----|--------|-----------------|
| ExtensionHost lifecycle | ADR-014 §4 | ✅ Shipped | A |
| Entry-point discovery | ADR-014 §6.4 | ✅ Shipped | A |
| Actor Protocol | ADR-014 §5.2 | ✅ Shipped | A |
| MigrationLoader | ADR-014 §9 | ✅ Shipped | A |
| WorkflowRegistry + WorkflowEngine | ADR-014 §5.4, §8 | ✅ Shipped | A |
| Multi-corpus foundation (CorpusRegistry) | ADR-008 | ✅ Shipped (Chunk 3 verified LIVE) | A |
| `/health/extensions` endpoint | ADR-014 §7 | ✅ Shipped | A |
| Import boundary enforcement | ADR-014 §5.3 | ✅ Shipped | A |
| GUI mount (stage 4) | ADR-014 §8 step 6 | 🔲 v1.1 | B (teacher dashboard) |
| MCP tools | ADR-014 §8 step 7 | 🔲 v1.2 | A+ (calculator/step-checker) |
| Web/feed layer | ADR-014 §3.4 | 🔲 not started | C (HERALD) |

---

## Version History

| Date | Change | Author |
|------|--------|--------|
| 2026-06-18 | Created roadmap. Seeded with Phase A/B/C from ADR-001 §11. Phase A foundation done; near-term gates identified. | Super Z (main) |

---

## Ongoing / Evergreen

- 🔄 Keep `PLANNED_FEATURES.md` current (move items from Near-Term to Already Built when shipped)
- 🔄 Keep `STATUS.md` current after each build session
- 🔄 Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- 🔄 Write ADRs for each significant architectural decision (start at ADR-002)
- 🔄 Log every platform-reach as a Phase 0 protocol gap (ADR-001 §9) — that list is the gift the reference extension gives LOOM and CodeForge
