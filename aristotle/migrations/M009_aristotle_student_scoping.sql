-- M009_aristotle_student_scoping.sql — Student identity + plan/concept ownership (ADR-004).
--
-- Adds a lightweight, Aristotle-local student identity and gives plans and
-- concepts real ownership columns instead of the implicit/JSON-derived
-- ownership they had before. Closes the schema-level gap that Tasks 15-17
-- patched at the query layer three separate times.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped. The new columns on aristotle_learning_plan and
-- aristotle_concept are nullable (or carry a default) so existing INSERT
-- statements that do not name them continue to work.
--
-- SQLite does not support multiple ADD COLUMN in one ALTER statement,
-- so each new column gets its own ALTER TABLE. SQLite also has no
-- IF NOT EXISTS for ADD COLUMN. Re-running M009 on a DB that already
-- has the columns would raise "duplicate column name" on each ALTER —
-- but this cannot happen in normal operation: the migration runner
-- (extensions/loaders/migration_loader.py) tracks applied migrations by
-- name in extension_applied_migrations and skips ones already recorded
-- BEFORE executing any of their SQL, so M009 is never re-run once
-- applied. If that name-tracking were ever bypassed (manual DB surgery,
-- a bug in the tracking table itself), the loader has no exception
-- handling around statement execution — the duplicate-column error would
-- propagate uncaught, and per the loader's own docstring the extension
-- host catches it and transitions the extension to DEGRADED, not a
-- silent skip. Idempotency here rests entirely on the name-tracking
-- table doing its job, not on any per-statement error recovery.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner — src/aip/adapter/extensions/loaders/migration_loader.py:151).
-- Comments must NOT contain semicolons or the split breaks mid-comment
-- and SQLite parses comment text as SQL. This file keeps all semicolons
-- out of comments and uses them only as SQL statement terminators.

-- aristotle_student (ADR-004 §Decision)
-- Lightweight, Aristotle-local student identity. Name-only, no credentials
-- (the ADR explicitly rejects full authentication for this deployment).
-- Kept schema-minimal so it can be promoted to a shared AIP_Brain-level
-- table later without a rename if a second extension needs the same
-- pattern (see ADR-004 §Consequences).
CREATE TABLE IF NOT EXISTS aristotle_student (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Backfill: one row for the existing hardcoded identity, so every
-- pre-migration table that already defaults to 'definer' (aristotle_mastery,
-- aristotle_struggle_pattern, aristotle_uploaded_material) keeps resolving
-- to a real row instead of an orphaned string.
INSERT OR IGNORE INTO aristotle_student (id, name) VALUES ('definer', 'Definer');

-- aristotle_learning_plan extended columns (ADR-004 §Decision)
-- student_id: which student this plan belongs to. Defaults to 'definer'
--   so existing plans (created before this migration) stay queryable
--   and keep their current effective owner.
-- material_id: the primary uploaded material this plan was built from.
--   Nullable because the deterministic-fallback plan path doesn't always
--   have one (it can build from a LIKE query against sample data). Used
--   by /dashboard's plan-scoped view to filter concepts by material.
ALTER TABLE aristotle_learning_plan ADD COLUMN student_id TEXT NOT NULL DEFAULT 'definer';
ALTER TABLE aristotle_learning_plan ADD COLUMN material_id TEXT;

-- aristotle_concept extended columns (ADR-004 §Decision)
-- plan_id: which plan this concept was created for. Nullable because
--   pre-M009 concepts (e.g. dogfood fixtures from concepts_sample.yaml)
--   were not created in the context of any plan.
-- material_id: the uploaded material this concept was derived from.
--   Nullable for the same reason as plan_id plus the fallback LIKE-query
--   path that reuses existing concepts without a material context.
-- Both are plain columns rather than a normalized join table because the
-- current system creates concepts 1:1 per plan (plan_generator.py Step 6
-- always creates new rows, never reuses an existing concept_id across
-- plans). See ADR-004 §Alternatives for the rationale.
ALTER TABLE aristotle_concept ADD COLUMN plan_id TEXT;
ALTER TABLE aristotle_concept ADD COLUMN material_id TEXT;

-- Indexes for the two new filter columns. These are the columns future
-- callers will WHERE on to answer "which plans belong to student X" and
-- "which concepts belong to plan Y" — the queries that previously
-- required JSON-parsing loops or just returned everything table-wide.
CREATE INDEX IF NOT EXISTS idx_learning_plan_student
    ON aristotle_learning_plan(student_id);

CREATE INDEX IF NOT EXISTS idx_concept_plan
    ON aristotle_concept(plan_id);

-- Backfill (best-effort, idempotent):
-- 1) aristotle_learning_plan.material_id: join through aristotle_plan_job
--    (the one table that already carried plan_id + material_id together
--    pre-M009). Only updates rows where material_id is still NULL, so
--    re-runs and rows already populated by plan_generator are untouched.
-- 2) aristotle_concept.plan_id + material_id: for each concept, find the
--    plan whose concept_ids_json array contains that concept's id, and
--    copy plan_id + that plan's material_id onto the concept row. Uses
--    SQLite's json_each to walk the array. Same NULL-guard as above.
--
-- Both backfills are best-effort: the poisoned pharmacy plan from before
-- ADR-004 is being discarded via a separate reset script (out of scope
-- for this migration), so there is no real data at stake in the first
-- backfill on the dev machine. The statements are written to be safe
-- no-ops when the source tables are empty or when the JSON parse finds
-- no matches.

UPDATE aristotle_learning_plan
SET material_id = (
    SELECT pj.material_id
    FROM aristotle_plan_job pj
    WHERE pj.plan_id = aristotle_learning_plan.id
    AND pj.material_id IS NOT NULL
    LIMIT 1
)
WHERE material_id IS NULL
AND EXISTS (
    SELECT 1 FROM aristotle_plan_job pj
    WHERE pj.plan_id = aristotle_learning_plan.id
    AND pj.material_id IS NOT NULL
);

UPDATE aristotle_concept
SET plan_id = (
    SELECT lp.id
    FROM aristotle_learning_plan lp, json_each(lp.concept_ids_json)
    WHERE json_each.value = aristotle_concept.id
    LIMIT 1
),
material_id = (
    SELECT lp.material_id
    FROM aristotle_learning_plan lp, json_each(lp.concept_ids_json)
    WHERE json_each.value = aristotle_concept.id
    LIMIT 1
)
WHERE plan_id IS NULL
AND EXISTS (
    SELECT 1
    FROM aristotle_learning_plan lp, json_each(lp.concept_ids_json)
    WHERE json_each.value = aristotle_concept.id
);
