# AIP_Aristotle — Status

**Last Updated:** 2026-06-20
**Phase:** GUI Phase ✅ COMPLETE / Phase E (multi-student) deferred / Phase C (HERALD) blocked
**Operational State:** Pre-alpha — Phase A tutoring loop + Phase B teacher dashboard + Phase B.5 pedagogical upgrades + Phase D onboarding + GUI surface layer shipped. 149 tests, 0 warnings. Most recent milestone: GUI phase completion + ADR-001 Research Annex (2026-06-20).

---

## Current State

Aristotle is a pip-installable extension of AIP Brain. It mounts via the
platform's entry-point discovery (`aip.extensions` group) and runs through
the full ExtensionHost lifecycle: discover → validate → migrate → register →
mount → ready. At startup, the host discovers Aristotle, parses its manifest,
applies M001 + M002 migrations to the `aristotle:textbook` corpus, calls
`on_load` to register three actors (SOCRATES, EXAMINER, MENTOR), two GUI
pages (/learn, /dashboard), and one API router, then starts actor scheduler
tasks.

**What works:**
- Extension discovery + lifecycle (mount, validate, migrate, register, mount, ready, stop)
- Three actors conform to the foundation Actor Protocol (isinstance-validated)
- Real model calls: SOCRATES.teach(), EXAMINER.probe()/quiz()/evaluate(), MENTOR.update_struggle_pattern()
- SM-2 spaced repetition module (aristotle/sm2.py) + aristotle_mastery table
- Session coordinator drives PREDICT→TEACH→PROBE→QUIZ→EVALUATE→[HINT_1→HINT_2→]REMEDIATE state machine
- Curiosity path — open learner model (ADR-002 Amendment A1): dual-mode session (structured ANSWER vs conversational QUESTION/TANGENT/CHAT)
- Content ingestor (YAML → aristotle_concept, bilingual)
- CLI (HTTP client): health, list-concepts, ingest, session (interactive + non-interactive)
- API routes: /concepts, /ingest, /session/{start,step,run}, /intake/{start,step}, /placer/{start,step}, /dashboard, /misconceptions, /settings (GET+POST), /upload, /session-history
- Expanded /upload endpoint: PDF (pypdf), images (pytesseract OCR), txt/md/csv/html/json/yaml/docx
- GUI learning view at /learn (concept selector + tutoring session)
- GUI teacher dashboard at /dashboard (stats header, struggle pattern, mastery table)
- ARISTOTLE GUI pages: /aristotle/stats, /aristotle/map, /aristotle/settings (save wired), /aristotle/teacher (Komal's view: mastery heatmap, struggle patterns, session history reconstruction)
- Brain + menu: real file upload (UI → ARISTOTLE /upload via hidden ui.upload), voice mode (Web Speech API)
- Dashboard LEFT JOIN: all concepts appear including unstarted; sort: due → unstarted → mastered
- Nav items dynamic via /health/extensions nav_items (no hardcoded extension names)
- Health surfaces via the platform's /health/extensions endpoint
- Import boundary machine-enforced (extensions import only the allowlist; gui/ scanned too)
- ADR-001 Research Annex committed (2026-06-20) — full evidence base underlying every design decision

**What doesn't work yet:**
- Phase C (HERALD): field awareness — blocked on platform web/feed layer (ADR-014 §3.4)
- Phase E (multi-student): deferred per ADR-014 §1 (one install per learner pre-alpha; multi-tenant is the deferred enterprise version)
- SOCRATES uses raw SQL on aristotle_concept, not the Brain's retrieval pipeline (ARISTOTLE-DEBT-007)
- GUI coupling to Brain's gui/ package (ARISTOTLE-DEBT-008)

---

## Install

```bash
# Install the platform first
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Brain.git

# Then install Aristotle
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
```

The platform discovers Aristotle automatically at startup.

## Development

```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
git clone https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
cd AIP_Brain && pip install -e .
cd ../AIP_Aristotle && pip install -e .
```

## Test

```bash
cd AIP_Aristotle
pytest tests/ -v
```

---

## Actor Status

| Actor | Status | cadence | Role |
|-------|--------|---------|------|
| SOCRATES | ✅ Placeholder | 0.0 (manual) | Teach / explain / re-explain (ADR-001 §2) |
| EXAMINER | ✅ Placeholder | 0.0 (manual) | Probe / quiz / evaluate (ADR-001 §2) |
| MENTOR | ✅ Placeholder | 0.0 (manual) | Long-arc tracking + struggle_pattern (ADR-001 §2) |
| VIGIL | ⏳ Not wired | (core) | SM-2 spaced repetition — reused from core, not re-implemented |
| HERALD | ⏳ Phase C | (n/a) | Field awareness — depends on platform web/feed layer (ADR-014 §3.4) |

All three active actors conform to `aip.foundation.protocols.actors.Actor`
(runtime_checkable). The host validates via `isinstance(actor, Actor)` at
scheduler start.

---

## Data Model Status

| Table | Corpus | Status | Schema |
|-------|--------|--------|--------|
| `aristotle_concept` | `aristotle:textbook` | ✅ Created (empty) | id, textbook_chapter, topic, subtopic, bloom_target, content_primary, content_alt, content_alt_lang, prerequisite_concept_id, created_at |
| `aristotle_struggle_pattern` | `aristotle:textbook` | ✅ Created (placeholder row) | student_id (PK, default 'definer'), pattern_text, updated_at |

**Note:** Progress tables are in `aristotle:textbook`, not `definer` (see
TECH_DEBT-001). Revisit at Phase B when cross-student aggregation matters.

---

## Workflow Status

| Workflow | Status | Nodes | Executable? |
|----------|--------|-------|-------------|
| `tutoring_session_v1` | ✅ Declared | 7 (teach→probe→quiz→evaluate→check_mastery→remediate→next_concept) | ⚠️ Fixture/no-op mode (script handlers not registered — see TECH_DEBT-003) |

The workflow is engine-compatible (L5 loader parses it). The platform's
`container.workflow_engine.run_workflow()` can load it. Script nodes
(`evaluate`, `next_concept`) run in no-op mode until handlers are registered.

---

## Platform Dependencies

Aristotle depends on these platform capabilities (all shipped in AIP_Brain
`feat/multi-corpus`):

| Platform capability | ADR | Status |
|---------------------|-----|--------|
| ExtensionHost lifecycle (discover→validate→migrate→register→ready→stop) | ADR-014 §4 | ✅ Shipped |
| Entry-point discovery (`aip.extensions` group) | ADR-014 §6.4 | ✅ Shipped |
| Manifest v1 (pydantic v2) | ADR-014 §6 | ✅ Shipped |
| Actor Protocol (`Actor`/`ActorContext`/`ActorResult`) | ADR-014 §5.2 | ✅ Shipped |
| MigrationLoader (separate `extension_applied_migrations` table) | ADR-014 §9 | ✅ Shipped |
| WorkflowRegistry.add_path (per-extension workflow dirs) | ADR-014 §5.4 | ✅ Shipped |
| WorkflowEngine wired into container | ADR-014 §8 step 2 | ✅ Shipped |
| `/health/extensions` endpoint | ADR-014 §7 | ✅ Shipped |
| Import boundary enforcement | ADR-014 §5.3 | ✅ Shipped |
| GUI mount (stage 4) | ADR-014 §8 step 6 | ✅ Shipped (v1.1) |
| MCP tools | ADR-014 §8 step 7 | ⏳ v1.2 (not started) |
| Web/feed layer (HERALD dependency) | ADR-014 §3.4 | ⏳ Not started |

---

## Pilot Readiness

**Ready for dogfood testing.** Phase A (tutoring loop) + Phase B (teacher
dashboard) + Phase B.5 (9 pedagogical upgrades) + Phase D backend
(onboarding: intake + placement + long-arc plan) + GUI surface layer
(stats, map, settings, teacher dashboard, session history, curiosity
path, expanded upload, Brain + menu + voice wired) are all shipped. 149
tests pass with 0 warnings. The tutoring loop runs end-to-end with real
model calls, SM-2 scheduling, struggle_pattern tracking, and a GUI
learning view. The teacher dashboard shows mastery, due items, and the
struggle pattern.

**What to test:**
1. Install both repos (pip install -e)
2. Start the server (./start.sh from AIP_Brain)
3. Ingest concepts: `python -m aristotle.cli ingest concepts_sample.yaml`
4. Run a session: `python -m aristotle.cli session newton_first_law --answer "objects resist changes in motion"`
5. Open the GUI: http://localhost:8080/learn (learner view) + http://localhost:8080/dashboard (teacher view)

**Next major milestone: Phase C (HERALD)** — field awareness. Blocked on
the platform's web/feed layer (ADR-014 §3.4), which is not yet built.
The tutoring loop ships without it; HERALD layers on when feeds land.
**Phase E (multi-student)** is deferred per ADR-014 §1 (one install per
learner pre-alpha) — not blocked, just not yet scheduled.
