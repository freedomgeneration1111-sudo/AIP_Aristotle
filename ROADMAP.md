# AIP_Aristotle Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-19
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (Phase A + Phase B dogfood complete)

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
*Status: ✅ COMPLETE — tutoring loop runs end-to-end (verified 2026-06-19 dogfood)*

Phase A is done. The TEACH → PROBE → QUIZ → EVALUATE → REMEDIATE state
machine runs through `aristotle.session.run_session_step`, the CLI drives
it (`aristotle session <concept>`), the API exposes it
(`/aristotle/session/{start,step,run}`), the GUI learning view at `/learn`
renders it, and the sample concepts (`concepts_sample.yaml`) include the
prerequisite DAG (Newton's Three Laws, bilingual en/ur).

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
*Status: ✅ COMPLETE — GET /aristotle/dashboard + /dashboard GUI page shipped (verified 2026-06-19 dogfood)*

Phase B is done. The dashboard API returns `mastery_by_concept` via a
LEFT JOIN (all concepts appear, including never-started ones), the sort
order is due → unstarted → mastered, and the GUI renders a stats header,
the struggle-pattern panel, and the mastery table. Nav item "Teach"
(order=35) is registered dynamically via `/health/extensions`.

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

## Phase B.5 — Research-Grounded Pedagogical Improvements
*Tutoring loop upgrade from ADR-002 Rev 2. No external dependencies — shipped immediately.*
*Status: ✅ COMPLETE — all 9 deliverables shipped (2026-06-19)*

Phase B.5 layered the ADR-002 Rev 2 pedagogical upgrades onto the existing
tutoring loop. The state machine now has PREDICT (generation effect) +
HINT_1/HINT_2 (2-rung hint ladder); SOCRATES has mastery-adaptive faded
worked examples; EXAMINER has error diagnosis, transfer questions, and a
cold-start check; MENTOR writes a structured misconception log; the
mastery model has a BKT-inspired `mastery_probability()` extension on top
of SM-2; the session coordinator interleaves due review concepts.

All 9 ADR-002 §15 deliverables shipped across 8 commits. See
PLANNED_FEATURES.md → Phase B.5 table for the commit map.

**DEFINER decision resolved:** ADR-002 §16 #4 — `ActorResult.data` field
added to the platform Protocol (Brain commit `ce44e53`). All ARISTOTLE
actors migrated to `data=` (error-as-payload eliminated — see
ARISTOTLE-DEBT-011).

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

## Phase D — Onboarding (Intake + Placement + Long-Arc Plan)
*Five-stage intake, placement calibration, versioned long-arc plan. ADR-002 Rev 2.*
*Status: 🔲 PLANNED (spec committed 2026-06-19; awaiting DEFINER review of ADR-002)*

Phase D is the ADR-002 Rev 2 onboarding system. New learners walk through a
five-stage intake interview (context, goals, prior exposure, time, friction),
take a placement calibration, and receive a versioned long-arc learning plan
that drives session selection across weeks. Two new actors (INTAKE, PLACER),
three new tables, OCR + voice capabilities for material upload.

Source spec: `docs/decisions/ADR-002-intake-placement-learning-plan.md`
(Part B — §§9–13; Part C — §§12–13). Build order: ADR-002 §15 (8 items,
listed in PLANNED_FEATURES.md → Phase D table).

**Gate:** None for core (M003 schema, INTAKE actor + GUI, `ui.upload`, OCR,
PLACER actor, voice toggle). The long-arc plan executor benefits from
Phase B.5's cold-start check but can ship a simpler version first.
Web search (a platform-side capability) unlocks material sourcing —
fetching a PDF from a publisher's page — but the intake/placement loop
itself runs without it.

**Open DEFINER decisions blocking Phase D:** ADR-002 §16 #1 (backup
strategy — blocking), #2 (OCR quality), #3 (voice STT), #5 (intake
language).

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
| 2026-06-19 | ADR-002 Rev 2 committed. Phase A and Phase B marked COMPLETE (verified via 2026-06-19 dogfood run). Phase B.5 added (research-grounded pedagogical improvements — no external dependencies). Phase D added (intake, placement, long-arc plan, OCR, voice — no external dependencies for core; web search unlocks material sourcing). Phase C unchanged (still gated on platform web/feed layer). | Super Z (main) |
| 2026-06-19 | **Phase B.5 ✅ COMPLETE.** All 9 deliverables shipped: PREDICT step, HINT ladder, error diagnosis, faded worked examples, session interleaving, transfer questions, misconception log wiring, extended mastery model (mastery_probability), cold-start check. ActorResult.data migration complete for all actors. 89 tests, 0 warnings. Phase C unchanged (still gated on platform web/feed layer). | Super Z (main) |

---

## Ongoing / Evergreen

- 🔄 Keep `PLANNED_FEATURES.md` current (move items from Near-Term to Already Built when shipped)
- 🔄 Keep `STATUS.md` current after each build session
- 🔄 Keep `TECH_DEBT.md` current (file new debt, mark resolved debt)
- 🔄 Write ADRs for each significant architectural decision (start at ADR-002)
- 🔄 Log every platform-reach as a Phase 0 protocol gap (ADR-001 §9) — that list is the gift the reference extension gives LOOM and CodeForge
