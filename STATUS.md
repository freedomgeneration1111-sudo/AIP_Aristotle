# AIP_Aristotle — Status

**Last Updated:** 2026-06-18
**Phase:** Phase A dogfood
**Operational State:** Pre-alpha, not yet dogfoodable (actors are placeholders)

---

## Current State

Aristotle is a pip-installable extension of AIP Brain. It mounts via the
platform's entry-point discovery (`aip.extensions` group) and runs through
the full ExtensionHost lifecycle: discover → validate → migrate → register →
ready. At startup, the host discovers Aristotle, parses its manifest, applies
M001_aristotle.sql to the `aristotle:textbook` corpus, calls `on_load` to
register three actors (SOCRATES, EXAMINER, MENTOR), and starts their
scheduler tasks.

**What works:**
- Extension discovery + lifecycle (mount, validate, migrate, register, ready, stop)
- Three actors conform to the foundation Actor Protocol (isinstance-validated)
- MENTOR reads/writes `aristotle_struggle_pattern` via the corpus write connection
- EXAMINER degrades gracefully without a model (returns ok=True)
- The tutoring state machine workflow is declared (engine-compatible, 7 nodes)
- Health surfaces via the platform's `/health/extensions` endpoint
- Import boundary is machine-enforced (extensions import only the allowlist)

**What doesn't work yet:**
- Actors don't make real model calls (SOCRATES doesn't generate explanations,
  EXAMINER doesn't generate/score questions, MENTOR doesn't write AI diagnostics)
- The workflow's script handlers (`aristotle_evaluate`, `aristotle_next_concept`)
  aren't registered — the workflow runs in fixture/no-op mode
- No content ingestor — `aristotle_concept` table is empty
- No SM-2 integration — VIGIL is reused from core but not wired
- No GUI learning view (platform v1.1, stage 4 mount, not yet built)

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
| GUI mount (stage 4) | ADR-014 §8 step 6 | ⏳ v1.1 (not started) |
| MCP tools | ADR-014 §8 step 7 | ⏳ v1.2 (not started) |
| Web/feed layer (HERALD dependency) | ADR-014 §3.4 | ⏳ Not started |

---

## Pilot Readiness

**Not yet dogfoodable.** The actors are placeholders (no real model calls),
the workflow runs in no-op mode, and there's no content to tutor. The
platform contract is proven, but the tutoring loop isn't live.

**Path to dogfoodable:**
1. Real model calls in SOCRATES/EXAMINER/MENTOR (Near-Term)
2. Script handlers registered (Near-Term)
3. Content ingestor — populate `aristotle_concept` (Near-Term)
4. SM-2 via core VIGIL (Near-Term)

After those four, Ramesh can self-tutor a chapter he already knows
(ADR-001 §10 pilot protocol step 1).
