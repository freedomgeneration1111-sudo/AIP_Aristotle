-- M006_curiosity_intent_class.sql — Add intent_class to misconception log.
--
-- ADR-002 Amendment A1: Open Learner Model — Curiosity Path.
-- Adds an intent_class column to track whether a log entry is a
-- misconception (ANSWER path) or a curiosity event (QUESTION/TANGENT/CHAT).
-- Defaults to 'ANSWER' so all existing rows are backward-compatible.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner). Comments must NOT contain semicolons. This file
-- keeps all semicolons out of comments and uses them only as SQL
-- statement terminators.

ALTER TABLE aristotle_misconception_log
    ADD COLUMN intent_class TEXT NOT NULL DEFAULT 'ANSWER';
