# AIP_Aristotle Technical Debt Register

**Owner:** B. Moses Jorgensen
**Last Updated:** 2026-06-18 (initial creation — seeded with Phase A debt)

Each entry records a deliberate deferral — what was skipped, why, and what triggers remediation.

---

## ARISTOTLE-DEBT-001 — Progress Tables in aristotle:textbook, Not Definer

**Status:** Active — revisit at Phase B (teacher dashboard)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
ADR-014 §1 (the platform's settled decisions) says progress tables go in
the `definer` corpus with `aristotle_*` naming. But the migration_loader
(step 1 of the platform build) applies extension migrations to the
extension's OWN contributed corpus (`aristotle:textbook`), not to the
definer corpus. For pre-alpha dogfood, per-corpus is simpler and matches
the loader's behavior.

The `aristotle_concept` and `aristotle_struggle_pattern` tables are in
`aristotle:textbook`, not `definer`.

**Why deferred:**
The migration_loader's behavior (apply to the extension's first declared
corpus) is simpler than targeting the definer corpus, which would require
cross-corpus migration targeting. Pre-alpha single-tenant doesn't need
cross-corpus aggregation. The `aristotle_*` naming convention is preserved
either way, so the tables are identifiable.

**Remediation trigger:**
Phase B (teacher dashboard) — when Komal needs cross-student aggregation
(mastery heatmaps, what's due across the class), the progress tables need
to be in a shared corpus (definer) or a dedicated progress corpus. At that
point, either:
1. Move the tables to `definer` (requires a migration + the platform
   gaining cross-corpus migration targeting), OR
2. Add a dedicated `aristotle:progress` corpus (cleaner separation; the
   manifest declares a second corpus).

**Related work:**
- ADR-014 §1 (platform — progress store location decision)
- `aristotle/migrations/M001_aristotle.sql` (the tables themselves)
- `AIP_Brain/src/aip/adapter/extensions/loaders/migration_loader.py` (the loader)

---

## ARISTOTLE-DEBT-002 — Actors Are Placeholders (No Real Model Calls)

**Status:** Active — Near-Term (Phase A completion)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
SOCRATES, EXAMINER, and MENTOR conform to the Actor Protocol and verify
platform reachability (corpus, model provider, struggle_pattern table),
but they don't do real teaching/probing/mentoring. SOCRATES doesn't
generate explanations. EXAMINER doesn't generate/score questions. MENTOR
doesn't write AI-diagnostic struggle_pattern sentences.

**Why deferred:**
The dogfood goal was to prove the platform contract end-to-end (manifest
validates, migration applies, actor registers, scheduler runs, health
surfaces). Real model calls require a configured model provider, which
is an operational concern (API keys) not a contract concern. The actors
are structured to accept a model provider via `ctx.container.model_provider`
— the tutoring loop just needs to call it.

**Remediation trigger:**
Near-Term (Phase A completion) — once a model provider is configured on
the container, the actors gain real model calls. This is tracked in
`PLANNED_FEATURES.md` Near-Term section.

**Related work:**
- `aristotle/actors/socrates.py` (verifies corpus, doesn't generate)
- `aristotle/actors/examiner.py` (verifies model availability, doesn't generate/score)
- `aristotle/actors/mentor.py` (reads/initializes struggle_pattern, doesn't write AI diagnostics)
- ADR-001 §2 (the five modes + their roles)

---

## ARISTOTLE-DEBT-003 — Workflow Script Handlers Not Registered

**Status:** Active — Near-Term (Phase A completion)
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
The `tutoring_session_v1.yaml` workflow declares two `script` nodes:
`evaluate` (run: `aristotle_evaluate`) and `next_concept` (run:
`aristotle_next_concept`). These script handlers are NOT registered with
the workflow engine. The engine runs them in fixture/no-op mode.

**Why deferred:**
The workflow YAML is engine-compatible (the L5 loader parses it without
errors), which was the dogfood goal. Registering real script handlers
requires understanding the engine's script-handler registration mechanism
(`ScriptNode` + `script_fixture_mode`), which is a platform-side concern.
The handlers themselves (update mastery, consult prerequisite DAG, update
struggle_pattern) depend on real model calls (ARISTOTLE-DEBT-002).

**Remediation trigger:**
Near-Term (Phase A completion) — after real model calls land, the script
handlers can be registered. Tracked in `PLANNED_FEATURES.md` Near-Term.

**Related work:**
- `aristotle/workflows/tutoring_session_v1.yaml` (the workflow declaring the script nodes)
- `AIP_Brain/src/aip/orchestration/workflow/loader.py` (the loader — script_fixture_mode default)
- `AIP_Brain/src/aip/orchestration/workflow/node.py` (ScriptNode)

---

## ARISTOTLE-DEBT-004 — Single-Tenant student_id (Pre-Alpha)

**Status:** Active — by design, pre-alpha
**Phase:** Phase A dogfood
**Filed:** 2026-06-18

**What was deferred:**
The `aristotle_struggle_pattern` table has a `student_id` column (PK), but
MENTOR hardcodes `student_id = "definer"` (pre-alpha single-tenant). There's
no multi-student session routing.

**Why deferred:**
ADR-014 §1 (platform settled decisions): "One install per learner (pre-
alpha). Multi-tenant is the deferred enterprise version." The student_id
column is in place so the tenant dimension can be added later without a
rewrite. The stable PK is the forward-compatible hedge.

**Remediation trigger:**
When ARISTOTLE moves from one-install-per-learner to multi-student (post-
alpha). At that point, MENTOR reads `student_id` from the session context
(`ctx.container` session metadata) instead of hardcoding `"definer"`.

**Related work:**
- ADR-014 §1 (platform — tenancy decision)
- `aristotle/actors/mentor.py` (hardcoded student_id)
- `aristotle/migrations/M001_aristotle.sql` (the student_id column)

---
