# Contributing to AIP_Aristotle

Thank you for your interest in contributing to Aristotle — the adaptive tutor
extension for AIP Brain.

> **Pre-alpha**: Aristotle is in Phase A dogfood. Contributions should follow
> the coding cycle protocol in `AGENTS.md`. See [PLANNED_FEATURES.md](PLANNED_FEATURES.md)
> for what's built vs deferred.

## Development Setup

Aristotle is an extension of [AIP Brain](https://github.com/freedomgeneration1111-sudo/AIP_Brain).
You need both repos installed in editable mode:

```bash
# Clone both repos
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
git clone https://github.com/freedomgeneration1111-sudo/AIP_Aristotle.git

# Install the platform first (editable)
cd AIP_Brain
pip install -e .

# Install Aristotle (editable — depends on aip>=0.1.0)
cd ../AIP_Aristotle
pip install -e .
```

Editable install means changes to either repo are picked up immediately —
no reinstall on every edit. The platform discovers Aristotle via the
`aip.extensions` entry-point group automatically at startup.

## Code Style

- Python 3.11+ with type hints where practical
- Formatted with `ruff format` (line-length=120)
- Linted with `ruff check` (rules: E, F, W, I)
- All SQLite access uses `aiosqlite` (via `stores.connection_manager.write_conn`)
- Actors conform to `aip.foundation.protocols.actors.Actor` (name/cadence/run_cycle/health)

## Extension Boundary

Aristotle imports from `aip.*` ONLY through the allowlist:
- `aip.foundation.protocols.*` (Actor Protocol + future Protocols)
- `aip.adapter.extensions` (public extension API: Manifest, etc.)
- `aip.foundation.schemas` (dataclasses extensions may use)

Anything else — `aip.adapter.corpus_registry`, `aip.orchestration.*`,
`aip.adapter.api.*` — is a hard violation. Extensions reach the container
via `ctx.container` (duck-typed), not by importing it.

This boundary is machine-enforced by `tests/test_import_boundary.py`. Run
it before every commit:

```bash
pytest tests/test_import_boundary.py -v
```

## Running Tests

```bash
pytest tests/                    # Full suite
pytest tests/test_aristotle_actors.py -v    # Actor conformance + behavior
pytest tests/test_import_boundary.py -v     # Boundary enforcement
```

## Before Submitting

```bash
ruff format .
ruff check .
pytest
```

All three must pass. The boundary test is the most important — a forbidden
import inside an extension is the #1 architectural violation.

## Architecture

Aristotle is a single-package extension (`aristotle/`), not a layered
application. Its discipline is the **extension boundary** (above), not
layer discipline. The platform (AIP Brain) has the layers; Aristotle
consumes them through the sanctioned surface.

### Design Principles

- **No fake success paths.** If a feature is not implemented, return a
  structured error or degrade gracefully (EXAMINER returns ok=True without
  a model — the actor is healthy, it just can't generate questions yet).
- **DEFINER sovereignty.** No artifact promotion may bypass the DEFINER
  gates. EXAMINER scores quizzes but never auto-promotes a concept to
  "mastered" without the DEFINER's gate.
- **Single-voice principle.** Aristotle is the only character the learner
  meets. The five modes (SOCRATES/EXAMINER/VIGIL/MENTOR/HERALD) are
  internal orchestration, not personas (ADR-001 §1).
- **Bilingual.** content_primary + content_alt + content_alt_lang (ISO 639-1).
  Defaults: English primary, Urdu alternate (ADR-001 §7).
- **Concept-aware, not byte-aware.** Standard RAG token-chunking is
  pedagogically wrong. Aristotle chunks by concept with a prerequisite DAG
  (ADR-001 §4).

## Commit Messages

Write clear, concise commit messages that describe what changed and why.
Reference the ADR or task ID when relevant. Avoid references to internal
build process phases or agent workflow steps.

## Coding Cycle Protocol

Every contribution follows the coding cycle protocol in `AGENTS.md`:

1. **Orient** — read `AGENTS.md`, `PLANNED_FEATURES.md`, `TECH_DEBT.md`,
   `STATUS.md` before planning any change.
2. **Contract check** — verify attribute names match between producer and
   consumer; verify imports are on the allowlist.
3. **Code** — one concern per change; verify every import respects the
   extension boundary.
4. **Verify** — write regression tests; run `tests/test_import_boundary.py`.
5. **Document** — update AGENTS.md for every modified folder; update
   `PLANNED_FEATURES.md` if you shipped or deferred a feature.

See `AGENTS.md` for the full protocol.
