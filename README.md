# AIP_Aristotle

**Aristotle — Adaptive Tutor. The first extension on the AIP Brain platform.**

> *"The roots of education are bitter, but the fruit is sweet." — Aristotle*

Aristotle is a pedagogical state machine (TEACH → PROBE → QUIZ → EVALUATE → REMEDIATE) with concept-aware chunking, bilingual content (English + Urdu), and per-student struggle_pattern tracking. It is an extension of [AIP Brain](https://github.com/freedomgeneration1111-sudo/AIP_Brain), not a standalone application — it rides on the platform's multi-corpus foundation, actor framework, graph store, and the Phase 0 extension contract.

**Aristotle is the only character the learner ever meets.** The five internal modes (SOCRATES, EXAMINER, VIGIL, MENTOR, HERALD) are orchestration, not personas. Single voice forward; full decomposition for whoever is running it.

## Status

**Pre-alpha — Phase A dogfood.** The platform contract is proven (extension mounts, actors conform, workflow declares), but the actors are placeholders (no real model calls) and the tutoring loop isn't live yet. See [STATUS.md](STATUS.md) for details.

## Install

```bash
# Install the platform first
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Brain.git

# Then install Aristotle
pip install git+https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
```

The platform discovers Aristotle automatically at startup via the `aip.extensions` entry-point group.

## Development

```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
git clone https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git
cd AIP_Brain && pip install -e .
cd ../AIP_Aristotle && pip install -e .
```

Editable install means changes to either repo are picked up immediately — no reinstall on every edit.

## What's Here

```
AIP_Aristotle/
  AGENTS.md                   # Coding cycle protocol + extension boundary discipline
  CONTRIBUTING.md             # Dev setup + code style + boundary rules
  PLANNED_FEATURES.md         # Phase A/B/C tracker (what's built vs deferred)
  TECH_DEBT.md                # ARISTOTLE-specific debt register
  STATUS.md                   # Current operational state
  worklog.md                  # Append-only work log
  pyproject.toml              # Package + aip.extensions entry point
  aristotle/
    AGENTS.md                 # Package contract (manifest, actors, hooks, config)
    extension.yaml            # Manifest v1 (declares corpus, actors, migrations, config)
    entrypoint.py             # get_manifest() — the entry point the host discovers
    config.py                 # AristotleSettings dataclass (bilingual defaults)
    hooks.py                  # on_load registers SOCRATES + EXAMINER + MENTOR
    actors/
      socrates.py             # Teach mode (Actor Protocol)
      examiner.py             # Probe/quiz/evaluate
      mentor.py               # Long-arc tracking + struggle_pattern
    migrations/
      M001_aristotle.sql      # aristotle_concept + aristotle_struggle_pattern
    workflows/
      tutoring_session_v1.yaml  # TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE state machine
  docs/decisions/
    ADR-000-template.md       # ADR template
    ADR-001-aristotle-architecture.md  # The architecture spec
  tests/
    test_aristotle_extension.py  # Integration tests (mounts via ExtensionHost)
    test_aristotle_actors.py     # Actor conformance + behavior + workflow tests
    test_import_boundary.py      # Extension boundary enforcement
```

## Architecture

Aristotle conforms to the [ADR-014](https://github.com/freedomgeneration1111-sudo/AIP_Brain/blob/feat/multi-corpus/docs/decisions/ADR-014-phase0-extension-host.md) extension contract. It imports from `aip.foundation.protocols.actors` only (the Actor Protocol) + `aip.adapter.extensions.manifest` (the Manifest model). The platform imports nothing from Aristotle — discovery is dynamic via entry points. The boundary is machine-enforced by `tests/test_import_boundary.py`.

See [ADR-001](docs/decisions/ADR-001-aristotle-architecture.md) for the full architecture spec — the single-voice principle, the five modes, the tutoring state machine, the concept-aware knowledge model, the phased build.

### The Five Modes (ADR-001 §2)

| Mode | Role | Status |
|------|------|--------|
| SOCRATES | Teach / explain / re-explain | ✅ Placeholder (Phase A) |
| EXAMINER | Probe / quiz / evaluate | ✅ Placeholder (Phase A) |
| VIGIL | Spaced repetition (SM-2) | ⏳ Reused from core, not wired |
| MENTOR | Long-arc tracking + struggle_pattern | ✅ Placeholder (Phase A) |
| HERALD | Field awareness | ⏳ Phase C (depends on platform web/feed layer) |

### The Tutoring State Machine (ADR-001 §3)

```
TEACH ──► PROBE ──► QUIZ ──► EVALUATE ──┬─► (mastered) ─► next concept
  ▲                                      │
  └──────────── REMEDIATE ◄──────────────┘ (struggling → different framing)
```

The learner never sees the mode names. Branching is invisible — the student only feels rhythm.

## Pilot Protocol (ADR-001 §10)

1. **Ramesh** (idea originator) — self-tutors a chapter he already knows. He'll break it in the right ways first.
2. **Sameer** (SAICH, health IT) — loads pharmacy/HIS material; HERALD feeds Pakistani medical-records industry news.
3. **Moses** (DEFINER) — physics + AI foundations; HERALD serves source-first field awareness with credibility tiering.
4. **Freedom Generation School** — supervised classroom pilot with Komal's dashboard.

## License

BUSL-1.1 (same as AIP Brain)
