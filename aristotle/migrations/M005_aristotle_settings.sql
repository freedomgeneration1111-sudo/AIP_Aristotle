-- M005_aristotle_settings.sql — Student settings for ARISTOTLE tutor preferences.
--
-- Stores per-student tutor preferences: display name, languages,
-- session length, mastery threshold, hint aggressiveness.
-- Single-tenant pre-alpha: student_id is always 'definer'.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped. M001 + M002 + M003 + M004 tables are untouched.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner). Comments must NOT contain semicolons. This file
-- keeps all semicolons out of comments and uses them only as SQL
-- statement terminators.

CREATE TABLE IF NOT EXISTS aristotle_settings (
    student_id          TEXT PRIMARY KEY,
    display_name        TEXT,
    primary_language    TEXT NOT NULL DEFAULT 'English',
    alt_language        TEXT,
    session_length      INTEGER NOT NULL DEFAULT 5,
    mastery_threshold   REAL NOT NULL DEFAULT 0.85,
    hint_aggressiveness TEXT NOT NULL DEFAULT 'balanced',
    updated_at          TEXT
);
