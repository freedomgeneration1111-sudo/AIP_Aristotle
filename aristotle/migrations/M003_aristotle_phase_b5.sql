-- M003_aristotle_phase_b5.sql — Phase B.5 schema foundation (ADR-002 Rev 2).
--
-- Adds the tables + columns the Phase B.5 pedagogical upgrades need:
--   - aristotle_predict_event: logs the learner's prediction before
--     TEACH (the generation effect, ADR-002 §3 PREDICT step).
--   - aristotle_misconception_log: structured per-instance misconception
--     tracking (ADR-002 §7). Replaces MENTOR's single-diagnostic-sentence
--     pattern with a queryable history.
--   - aristotle_mastery extended columns: hint_assisted_correct,
--     slip_count, cold_start_passed, transfer_correct, transfer_attempted.
--     These feed the extended mastery model (ADR-002 §8) and the
--     cold-start check (ADR-002 §3, B.5 item 9).
--
-- This migration is additive only. No existing column or table is
-- modified or dropped. M001 + M002 tables are untouched. The new
-- aristotle_mastery columns default to 0 so existing rows and existing
-- INSERT statements continue to work.
--
-- SQLite does not support multiple ADD COLUMN in one ALTER statement,
-- so each new column gets its own ALTER TABLE. SQLite also has no
-- IF NOT EXISTS for ADD COLUMN. Re-running M003 on a DB that already
-- has the columns will raise "duplicate column name" on each ALTER.
-- The migration runner treats that as a skip-with-warning. For Phase
-- B.5 dogfood, M003 is applied once per fresh DB. The
-- extension_applied_migrations fingerprint table prevents re-application.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner). Comments must NOT contain semicolons or the split
-- breaks mid-comment. This file keeps all semicolons out of comments
-- and uses them only as SQL statement terminators.

-- aristotle_predict_event (ADR-002 §10.4)
-- No correctness column. The generation effect works regardless of
-- whether the prediction was right or wrong. We record the prediction
-- for analysis, not for scoring. The ADR's finding column (set by
-- PLACER in Phase D) is intentionally omitted here. Phase B.5 only
-- records the prediction itself.
CREATE TABLE IF NOT EXISTS aristotle_predict_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    prediction_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- aristotle_misconception_log (ADR-002 §7, §10.5)
-- Phase B.5 simplified schema: id, session_id, concept_id,
-- misconception_text, corrective_text, created_at. The ADR's full
-- schema adds student_id, quiz_type, student_answer, diagnosis_text
-- (renamed corrective_text here), hint_count_used, resolved, plus an
-- index. Those land in a later B.5 commit when MENTOR's misconception
-- tracking is wired (B.5 item 7). This commit lays the table foundation
-- so the migration test can verify the schema exists.
CREATE TABLE IF NOT EXISTS aristotle_misconception_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    misconception_text TEXT NOT NULL,
    corrective_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- aristotle_mastery extended columns (ADR-002 §10.6)
-- Each ALTER adds one column. Defaults to 0 so existing rows and
-- existing INSERT statements (which do not name these columns)
-- continue to work. The migration runner applies each statement in
-- order. If a column already exists (re-run scenario), the statement
-- fails and the runner skips it with a warning. The
-- extension_applied_migrations fingerprint table normally prevents
-- re-runs entirely.
ALTER TABLE aristotle_mastery ADD COLUMN hint_assisted_correct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN slip_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN cold_start_passed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN transfer_correct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aristotle_mastery ADD COLUMN transfer_attempted INTEGER NOT NULL DEFAULT 0;
