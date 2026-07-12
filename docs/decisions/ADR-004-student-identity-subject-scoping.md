# ADR-004: Student Identity + Subject/Plan Scoping

**Status:** Proposed
**Date:** 2026-07-12
**Author:** Claude (DEFINER oversight role — architectural diagnosis)
**Supersedes:** none — extends ADR-001 (architecture), ADR-002 (intake/placement/plan), ADR-003 (ingestion/RAG)

---

## Context

Three separate production bugs, fixed in Tasks 15–17, turned out to share one root cause: nothing in Aristotle's data model identifies *which student* or *which subject* a given row belongs to. Each fix patched the symptom at the query layer that happened to be involved; none of them touched the underlying gap, because the underlying gap is architectural, not a call-site oversight repeated three times.

**The three incidents:**

1. **Intake pacing (Task 15).** Not itself a scoping bug, but the same pattern of "the server trusts the caller to self-regulate, with no structural constraint" — relevant here because the fix (per-session `deep_intake` flag) is exactly the shape of fix this ADR proposes at the data layer: give the thing that needs distinguishing an actual field, rather than inferring it.
2. **Cross-material content contamination (Task 17, Bug A).** `retrieve_relevant_chunks()` filtered only by `domain="aristotle:textbook"` — a single shared vector-store domain across every material ever ingested by every student. A pharmacy student's plan pulled physics chunks from an old, unrelated ingest sitting in the same domain. `material_id` was already present in chunk metadata; nothing filtered on it.
3. **Wrong starting concept (Task 17, Bug B).** `GET /aristotle/concepts` returns every concept ever created, table-wide, with no plan or student scoping. `ask.py`'s tutoring-start fallback took `concepts[0]` — which is deterministically the oldest row in the table (`newton_first_law`, a dogfood fixture from `concepts_sample.yaml`), regardless of which plan was actually active.

**A fourth instance, found while diagnosing the above, not yet causing a reported failure:** `GET /dashboard` (`api.py:265`) — the screen literally labeled "Teacher Dashboard" — runs `SELECT c.id, c.topic ... FROM aristotle_concept c LEFT JOIN aristotle_mastery m ON c.id = m.concept_id AND m.student_id = ?` with **no subject or plan filter at all**, against a hardcoded `student_id = "definer"` (`api.py:314`). Whoever looks at this today sees every concept from every subject and every student who has ever used this Aristotle instance, mixed into one list. This has not yet produced a visible incident only because there has been one real student so far.

**What the schema actually contains today (verified against migrations M001–M008):**

| Table | Has student_id? | Has plan_id? | Has material_id? |
|---|---|---|---|
| `aristotle_concept` | No | No | No |
| `aristotle_learning_plan` | **No** | (is the plan) | No |
| `aristotle_uploaded_material` | Yes | No | (is the material) |
| `aristotle_plan_job` | No | Yes | Yes |
| `aristotle_mastery` | Yes (defaults `'definer'`) | No | No |
| `aristotle_struggle_pattern` | Yes (defaults `'definer'`) | No | No |
| `aristotle_placement_event` | No | Yes | No |
| `aristotle_intake_session` | No | Yes | No |

`aristotle_concept` — the table every tutoring, placement, and dashboard query ultimately reads from — has zero linkage to anything. Its only connection to a subject is indirect: appearing inside some `aristotle_learning_plan.concept_ids_json` JSON array. There is no query that can answer "which concepts belong to subject X" without loading every plan and parsing JSON. `aristotle_learning_plan` cannot answer "which plans belong to student X" at all — the column doesn't exist. And `api.py:314`'s `student_id = "definer"` is not a default with an override path; as of this ADR, it is the only value that has ever existed anywhere in the running system, because `AUTH DISABLED` mode (the only mode this has ever run in) treats every request as DEFINER.

This matters beyond bug-fixing: Moses runs this for himself (multiple subjects — NBCM, and whatever else he self-studies through Aristotle) and for students at Freedom Generation School who will each study multiple subjects over time. "Which student, which subject" is not an edge case to guard against — it is the normal, expected shape of every real session this system will ever run.

## Decision

Add a lightweight, Aristotle-local student identity, and give plans and concepts real ownership columns instead of implicit/JSON-derived ownership. Explicitly **not** full authentication — this runs `AUTH DISABLED` for a small trusted group (Moses, Komal, named students) on a laptop, not a public service. The existing DEFINER/collaborator role system already establishes the precedent that "who is this" can be a simple, low-ceremony concept in this codebase; this extends the same posture to "which learner," one level down.

### Schema — new migration `M009_aristotle_student_scoping.sql`

```sql
CREATE TABLE IF NOT EXISTS aristotle_student (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Backfill: one row for the existing hardcoded identity, so every
-- pre-migration table that already defaults to 'definer' keeps resolving
-- to a real row instead of an orphaned string.
INSERT OR IGNORE INTO aristotle_student (id, name) VALUES ('definer', 'Definer');

ALTER TABLE aristotle_learning_plan ADD COLUMN student_id TEXT NOT NULL DEFAULT 'definer';
ALTER TABLE aristotle_learning_plan ADD COLUMN material_id TEXT;

ALTER TABLE aristotle_concept ADD COLUMN plan_id TEXT;
ALTER TABLE aristotle_concept ADD COLUMN material_id TEXT;

CREATE INDEX IF NOT EXISTS idx_learning_plan_student ON aristotle_learning_plan(student_id);
CREATE INDEX IF NOT EXISTS idx_concept_plan ON aristotle_concept(plan_id);
```

`plan_id`/`material_id` on `aristotle_concept` are plain columns, not a normalized join table — see Alternatives for why. Both are nullable: concepts are created fresh per plan by `plan_generator.py` Step 6 today (never shared or reused across plans), so there is no existing data to reconcile against a stricter constraint, and nullability keeps this an additive, non-breaking migration.

**Backfill for existing rows** (best-effort, run once as part of the migration or a follow-up script): for each `aristotle_learning_plan`, join through `aristotle_plan_job` on `plan_id` to recover `material_id` (the job table already carries both together — this is the one place that linkage currently exists). For `aristotle_concept`, backfill `plan_id`/`material_id` by finding which plan's `concept_ids_json` contains that concept's `id`. Both are best-effort because the currently-poisoned pharmacy plan is being discarded via the reset script (separate from this ADR) — there is no real data at stake in the first backfill.

### API — `aristotle/api.py`

- `POST /aristotle/students {name}` → creates and returns `{id, name}`.
- `GET /aristotle/students` → list all.
- `GET /aristotle/plans?student_id=X` → list plans for a student: `{id, subject, status, current_concept_idx, total_concepts, created_at, last_session_at}`. This is the endpoint a subject switcher actually needs; it does not exist today.
- `GET /aristotle/concepts` — add optional `?plan_id=` / `?material_id=` query params. When provided, filter. When absent, **log a warning** (`concepts_route_unscoped_call`) and keep current unfiltered behavior for backward compatibility with any caller not yet updated. The warning exists specifically so an unscoped call is visible in logs going forward, rather than silently reproducing the exact failure mode this ADR is closing.
- `GET /dashboard` — accept `?student_id=&plan_id=`, default `student_id` to `'definer'` only if not provided (preserves current behavior for the single-student case), and scope the concept/mastery join by `plan_id` when given instead of scanning every concept in the table.
- `plan_generator.py`'s Step 6 storage (already writes `session.draft_plan` / calls `IntakeActor.generate_plan`) — populate the new `plan_id`/`material_id` columns on each `aristotle_concept` row it creates, and `student_id`/`material_id` on the `aristotle_learning_plan` row.

### GUI — `AIP_Brain/gui/pages/ask.py`

- A lightweight student picker — not a login form. A dropdown/selector populated from `GET /aristotle/students`, with a "new student" option that calls `POST /aristotle/students`. Persisted the same way `concept_from_url` already is (query param / local state), threaded into every Aristotle API call as `student_id`.
- A "My Subjects" view for the selected student: calls `GET /aristotle/plans?student_id=X`, lets them resume an existing subject or start a new one (triggers fresh intake). This is the actual UI surface Moses asked for — it doesn't exist today because the API it would call doesn't exist today.

## Alternatives Considered

**Full authentication (passwords, sessions, JWT).** Rejected — disproportionate to the actual threat model. This runs on a laptop for a known small group; `AUTH DISABLED` mode already documents that this is intentional for the current deployment stage. Real auth adds password storage and session-hijacking surface for no corresponding benefit here.

**Platform-level (AIP_Brain) identity system spanning all extensions**, rather than Aristotle-local. Rejected for now — no other extension currently needs per-user scoping, and generalizing before a second consumer exists is guesswork about requirements nobody has yet. The upgrade path stays open: `aristotle_student` is deliberately shaped (id/name, nothing Aristotle-specific in its own definition) so it could be promoted to a shared Brain-level table later without a rename if a second extension needs the same pattern. See Consequences.

**Normalized `aristotle_plan_concept(plan_id, concept_id, position)` join table** instead of columns directly on `aristotle_concept`. Rejected — the current system creates concepts 1:1 per plan (plan_generator's Step 6 always creates new rows, never reuses an existing concept_id across plans), so a many-to-many join table models a relationship that doesn't exist yet in practice. Two nullable columns are the right size for the current usage pattern; revisit if concept-sharing across plans becomes a real feature (e.g. two students on the same textbook reusing the same generated concepts).

**Leave `aristotle_concept`'s schema untouched; only add API-level filtering that resolves ownership by joining through `concept_ids_json` at query time.** Rejected — this is the same shape of fix already applied three times (Task 15's turns_in_focus visibility-only fallback, Task 17's two query-layer patches) and it has already failed to generalize three times: each new caller of `aristotle_concept` has to remember to do the JSON-parsing dance itself, or it silently reproduces the exact bug this ADR exists to close (as `/dashboard` demonstrates — it was never touched by Task 17 and still leaks). A column the row itself carries can't be forgotten by a future caller the way a join-time convention can.

## Consequences

**Gets easier:** any future query that needs "concepts for subject X" or "plans for student Y" is a `WHERE` clause instead of a JSON-parsing loop. The dashboard, the concept map, and any future per-subject feature (progress reports, spaced-repetition scheduling per subject, etc.) all become straightforward once ownership is a real column.

**Gets harder / new maintenance burden:** every future write path that creates an `aristotle_concept` or `aristotle_learning_plan` row must remember to populate the new ownership columns, or a new instance of the same class of bug reappears with different symptoms. Worth a lint/test rule (e.g. a boundary test asserting `plan_generator.py`'s concept-insertion path always sets `plan_id`), not just a code review reminder.

**Upgrade path if the "Aristotle-local, not platform-level" call turns out wrong:** `aristotle_student` was kept schema-minimal specifically so it can move to an AIP_Brain-level table later — the migration path is "copy the table, repoint foreign keys," not a redesign.

**Explicitly not addressed by this ADR:** the already-poisoned pharmacy plan/concepts — handled separately via the reset script, not migration backfill. Real login/password auth, if ever needed for a public-facing deployment — out of scope per Alternatives above; would be a superseding ADR, not an extension of this one.

## Related

- ADR-001 (Aristotle architecture) — actor roles this ADR doesn't change.
- ADR-002 (intake/placement/learning plan) — `aristotle_learning_plan` schema origin; this ADR extends it.
- ADR-003 (ingestion/RAG pipeline) — `material_id` on chunk metadata, which this ADR finally connects to the plan/concept layer.
- Task 17 worklog entry — the two query-layer patches this ADR replaces with a schema-level fix.
- Source files most affected: `aristotle/migrations/` (new M009), `aristotle/api.py`, `aristotle/actors/plan_generator.py`, `AIP_Brain/gui/pages/ask.py`.
