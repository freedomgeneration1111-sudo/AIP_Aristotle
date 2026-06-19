# ============================================================

# AIP_Aristotle — Agent Navigation Root
> Aristotle — Adaptive Tutor. The first extension on the AIP Brain platform.
> Phase A dogfood | Status: pre-alpha

## Purpose
Aristotle is an adaptive tutor: a pedagogical state machine (TEACH → PROBE →
QUIZ → EVALUATE → REMEDIATE) with concept-aware chunking, bilingual content
(English + Urdu), and per-student struggle_pattern tracking. It is an
extension of AIP Brain, not a standalone application — it rides on the
platform's multi-corpus foundation, actor framework, graph store, and the
Phase 0 extension contract (ADR-014).

Aristotle is the only character the learner ever meets. The five internal
modes (SOCRATES, EXAMINER, VIGIL, MENTOR, HERALD) are orchestration, not
personas. Single voice forward; full decomposition for whoever is running it
(ADR-001 §1).

## Governance Invariants — DEFINER Law (Non-Negotiable)
These apply everywhere in this codebase. Violation = blocker.

Aristotle is bound by the [AIP Governance Contract](https://github.com/freedomgeneration1111-sudo/AIP_Brain/blob/feat/multi-corpus/AIP_GOVERNANCE.md)
(hosted in AIP_Brain, linked not copied — three copies would drift). The
core invariants:

- **§1.7 — No bypass**: No actor, workflow, or queued task may promote an
  artifact without explicit DEFINER approval. Aristotle's EXAMINER scores
  quizzes but never auto-promotes a concept to "mastered" without the
  DEFINER's gate.
- **No silent model calls**: A model call that cannot dispatch must return
  NEEDS_CONFIGURATION, not a placeholder string. EXAMINER returns ok=True
  even without a model — the actor is healthy, it just can't generate
  questions yet. The tutoring loop checks model availability before
  attempting a quiz.
- **Async-safe storage**: All SQLite access uses `aiosqlite`. No
  `sqlite3.connect()` inside any async method. MENTOR's struggle_pattern
  reads/writes go through `stores.connection_manager.write_conn` (an
  aiosqlite connection).
- **Source-grounded answers only**: Every explanation SOCRATES generates
  must include provenance back to the textbook corpus. No fabricated
  content.

## Extension Boundary Discipline — Check Every Import
This is the #1 source of architectural violations. Before adding any import:

```
aristotle.*  → imports from aip.* ONLY through the allowlist:
                  aip.foundation.protocols.*   (Actor Protocol + future Protocols)
                  aip.adapter.extensions       (public extension API: Manifest, etc.)
                  aip.foundation.schemas       (dataclasses extensions may use)

aip.*        → NEVER imports from aristotle.* (or any extension)
                  The platform discovers extensions dynamically; it never
                  imports them by name.
```

The allowlist is machine-enforced by `tests/test_import_boundary.py`. A
forbidden `from aip.adapter.corpus_registry import ...` inside this repo
will fail CI loudly. Extensions reach the container via `ctx.container`
(duck-typed as `Any` in the foundation Protocol), not by importing it.

If you find yourself writing `from aip.adapter.corpus_registry import ...`
inside `aristotle/`, stop. Use `ctx.container.corpus_registry` at runtime
instead.

## Configuration Entry Points
- Extension manifest: `aristotle/extension.yaml`
- Extension config: `aristotle.config:AristotleSettings` (dataclass, loaded
  by the host at stage 1 validate via `importlib.import_module`)
- Entry point: `aristotle.entrypoint:get_manifest` (declared in
  `pyproject.toml` under `[project.entry-points."aip.extensions"]`)
- Bilingual defaults: `primary_language="en"`, `alt_language="ur"`
  (ADR-001 §7)

## Brand System (inherited from AIP Brain)
- Background: dark field `#0d1117`
- Accent 1: slate-teal `#4A9B8E`
- Accent 2: amber `#D4A843`
- Text: cream `#F5F0E8`
- Display font: Fraunces | Editorial: Newsreader | UI: Inter | Code: IBM Plex Mono

## Docs Framework Rules (for all agents working in this repo)
1. Read the full doc chain root→target before touching any code.
2. Make the minimal edit. No scope expansion without explicit instruction.
3. After any edit, update this file and all parent AGENTS.md on the path.
4. Create or update the leaf AGENTS.md for the folder you edited.
5. If a shared convention changes, update root AGENTS.md first.
6. Sibling folders are invisible unless explicitly linked. Do not assume context
   from adjacent folders you haven't read.
7. **Read the status-tracking docs before recommending changes.** The root
   docs (`PLANNED_FEATURES.md`, `TECH_DEBT.md`, `STATUS.md`) are the single
   source of truth for what's built, what's planned, and what's deferred.
   `PLANNED_FEATURES.md` is the canonical tracker; `TECH_DEBT.md` has the
   resolution status; `STATUS.md` has the current operational state.
8. **Read the platform's ADR-014** (in AIP_Brain) before changing anything
   that touches the extension contract. Aristotle is the first consumer;
   if the contract needs to change, that's a platform-side decision.

## ============================================================
## CODING CYCLE PROTOCOL (Mandatory — Every Agent, Every Cycle)
## ============================================================

Every coding cycle — whether new feature, bug fix, or refactor — follows this
sequence. There are no exceptions.

### 1. Orient (Read Phase)
- Read root AGENTS.md (you are here)
- Read the root status-tracking docs before recommending or planning any change:
  - `PLANNED_FEATURES.md` — canonical tracker: what's Already Built / Near-Term / Long-Term
  - `TECH_DEBT.md` — debt items with resolution status (don't recommend fixing a resolved debt)
  - `STATUS.md` — current operational state
- Read AGENTS.md for every folder you will **MODIFY**
- Read AGENTS.md for every folder that **CONSUMES** what you will produce
  (the bug is always in the gap between producer and consumer)
- If a consumer folder lacks AGENTS.md, **create one before coding**
- **Read the platform's ADR-014** if your change touches the extension
  contract (manifest schema, actor registration, lifecycle stages)

### 2. Contract Check (Before Writing Code)
- Identify the data flow: what leaves this module, what enters it
- Verify **attribute names match** between producer and consumer
- If adding a new state machine or API field: write the contract
  into AGENTS.md **BEFORE** writing the code (contract-first, not afterthought)
- If the change touches the extension boundary: verify the import is on
  the allowlist (`aip.foundation.protocols.*`, `aip.adapter.extensions`,
  `aip.foundation.schemas`). If not, redesign — don't expand the allowlist
  without a deliberate decision.
- If the change reaches into platform internals via `ctx.container`:
  document the access pattern in AGENTS.md so it's visible

### 3. Code (Minimal Change Discipline)
- **One concern per change**. Resist fixing 5 things at once unless
  they are the SAME root cause.
- Every import: verify it respects the extension boundary (allowlist above)
- Every async handler: **definition before reference** (define functions
  BEFORE the `ui.button(on_click=...)` or equivalent that references them)
- Every state transition: verify it's in the tutoring state machine
  (TEACH→PROBE→QUIZ→EVALUATE→REMEDIATE), not ad-hoc
- Every error path: **surface, don't swallow**. No silent failures.
- Every cross-module data reference: verify the attribute name exists
  on the producer (do NOT assume — check the producer's AGENTS.md Contracts)
- Every actor: conform to `aip.foundation.protocols.actors.Actor`
  (name/cadence/run_cycle/health). The host validates via `isinstance`.

### 4. Verify (Test + Smoke)
- Write regression tests for the specific bug or new behavior
- Tests must test **behavior**, not source text (no `Path().read_text()`
  when you can import and call)
- If the test env can't import the module, that's a signal the
  dependency graph needs attention, not a workaround
- Run `tests/test_import_boundary.py` to verify no forbidden imports
- Run `tests/test_aristotle_actors.py` to verify actor conformance

### 5. Document (Update Phase)
- Update AGENTS.md for **every folder you modified**
- Update AGENTS.md for **every consumer folder** whose data flow changed
- Add any bug you fixed as a **"Known Gotcha"** in the relevant AGENTS.md
- Update the **"Last Cycle"** section with what changed and why
- If you created a new contract (state machine, API field, config key),
  it MUST appear in the AGENTS.md of **both producer and consumer**
- Update `PLANNED_FEATURES.md` if you shipped a feature (move it from
  Near-Term/Long-Term to Already Built) or deferred one (move to Long-Term
  with the reason). This keeps the canonical tracker current so no future
  agent gives advice that's already obsolete.
- Commit AGENTS.md changes alongside code changes, never separately

## ============================================================
## AGENTS.md SECTION TEMPLATE (Required for Every Folder)
## ============================================================

Every AGENTS.md must include these sections. Existing content maps into
the appropriate section; do NOT duplicate.

1. **Purpose** — What this folder is for
2. **Architecture Constraints** — Extension boundary rules, import allowlist
3. **Contracts** — What this module PROMISES to consumers
   - Attribute names, API response fields, state machine values
   - Mismatches here are the #1 bug class
4. **Data Flows (In / Out)** — What enters, what leaves, attribute names
   - Cross-folder flows: `producer → consumer` with specific fields
5. **Known Gotchas** — Every bug that happened here, one line each
6. **Last Cycle** — What changed most recently and why
7. **Key Files** — File → role mapping
8. **Work Guidance** — How to edit safely
9. **How to Test** — Commands to verify

## Child Docs Index

| Subsystem | AGENTS.md Path | One-line description |
|-----------|----------------|----------------------|
| Aristotle package | `aristotle/AGENTS.md` | The extension package — manifest, actors, hooks, config |

## Root Status-Tracking Docs (read before recommending changes)

| Doc | Path | Role |
|-----|------|------|
| Planned Features | `PLANNED_FEATURES.md` | Canonical tracker: Already Built / Near-Term / Long-Term |
| Tech Debt | `TECH_DEBT.md` | Debt items with resolution status |
| Status | `STATUS.md` | Current operational state |
| Worklog | `worklog.md` | Append-only work log (Task ID, Agent, Work Log, Stage Summary) |

## Platform References (read-only, hosted in AIP_Brain)

| Doc | Location | Role |
|-----|----------|------|
| ADR-014 | `AIP_Brain/docs/decisions/ADR-014-phase0-extension-host.md` | The extension platform contract Aristotle consumes |
| ADR-008 | `AIP_Brain/docs/decisions/ADR-008-multi-corpus-architecture-rev3.md` | The multi-corpus foundation Aristotle rides on |
| Governance | `AIP_Brain/AIP_GOVERNANCE.md` | Binding invariants on all AIP components |
| Actor Protocol | `AIP_Brain/src/aip/foundation/protocols/actors.py` | The `Actor`/`ActorContext`/`ActorResult` Protocol |

# ============================================================
