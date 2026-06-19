-- M004_aristotle_phase_d.sql — Phase D schema foundation (ADR-002 §10).
--
-- Adds the three tables the Phase D onboarding system needs:
--   - aristotle_intake_session: records the intake conversation state +
--     responses so the INTAKE actor can resume after interruption.
--   - aristotle_learning_plan: versioned, append-only learning plan.
--     The INTAKE actor writes one row at the end of a successful intake.
--     The session coordinator reads concept_ids_json to build the concept
--     queue for interleaved sessions (Phase B.5 item 5).
--   - aristotle_placement_event: records placement calibration results.
--     The PLACER actor (Phase D, not in this commit) writes one row per
--     concept assessed during placement.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped. M001 + M002 + M003 tables are untouched.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner — src/aip/adapter/extensions/loaders/migration_loader.py:151).
-- Comments must NOT contain semicolons or the split breaks mid-comment
-- and SQLite parses comment text as SQL. This file keeps all semicolons
-- out of comments and uses them only as SQL statement terminators.

-- aristotle_intake_session (ADR-002 §10.3, simplified for Phase D pre-alpha)
-- Records the intake conversation state so the INTAKE actor can resume
-- after interruption. responses_json holds the learner's free-form answers
-- to each stage (subject, prior_knowledge, goals, schedule).
-- status: 'in_progress' during the conversation, 'complete' when the
-- learning plan is generated.
CREATE TABLE IF NOT EXISTS aristotle_intake_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT,
    subject TEXT,
    prior_knowledge TEXT,
    goals TEXT,
    schedule_minutes_per_day INTEGER,
    responses_json TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK(status IN ('in_progress', 'complete')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- aristotle_learning_plan (ADR-002 §10.1, simplified for Phase D pre-alpha)
-- Versioned, append-only learning plan. The INTAKE actor writes one row
-- at the end of a successful intake. concept_ids_json is an ordered JSON
-- array of concept_ids — the session coordinator reads it to build the
-- concept queue for interleaved sessions.
-- current_concept_idx tracks progress through the plan.
-- status: 'active' (default), 'paused', 'complete'.
CREATE TABLE IF NOT EXISTS aristotle_learning_plan (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    goals TEXT,
    schedule_minutes_per_day INTEGER NOT NULL,
    concept_ids_json TEXT NOT NULL,
    current_concept_idx INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'complete')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_session_at TEXT,
    consecutive_missed_sessions INTEGER NOT NULL DEFAULT 0
);

-- aristotle_placement_event (ADR-002 §10.2, simplified for Phase D pre-alpha)
-- Records placement calibration results. The PLACER actor (Phase D, not
-- in this commit) writes one row per concept assessed during placement.
-- score: 0.0-1.0 from EXAMINER.evaluate() during placement.
-- mastery_achieved: 0 or 1.
CREATE TABLE IF NOT EXISTS aristotle_placement_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    score REAL NOT NULL,
    mastery_achieved INTEGER NOT NULL DEFAULT 0,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
