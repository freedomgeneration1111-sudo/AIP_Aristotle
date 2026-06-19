-- M002_aristotle_mastery.sql — SM-2 spaced repetition state per concept per student.
--
-- ADR-001 §2: VIGIL was supposed to provide SM-2, but the platform's Vigil actor
-- is a quality evaluation actor (faithfulness, consistency, source grounding),
-- NOT a spaced repetition scheduler. This is a platform gap (logged as
-- ARISTOTLE-DEBT-006). ARISTOTLE implements SM-2 directly — the algorithm is
-- ~20 lines of Python and doesn't belong in the platform anyway.
--
-- This table stores the SM-2 state per (student, concept) pair. The session
-- coordinator reads/writes it after each EVALUATE transition.

CREATE TABLE IF NOT EXISTS aristotle_mastery (
    student_id TEXT NOT NULL DEFAULT 'definer',
    concept_id TEXT NOT NULL,
    -- SM-2 algorithm state
    easiness_factor REAL NOT NULL DEFAULT 2.5,   -- SM-2 EF (>= 1.3)
    interval_days INTEGER NOT NULL DEFAULT 0,    -- days until next review
    repetitions INTEGER NOT NULL DEFAULT 0,      -- consecutive correct reviews
    next_review_at TEXT,                          -- ISO timestamp, NULL = not scheduled
    -- Mastery tracking (separate from SM-2)
    last_score REAL,                              -- 0.0-1.0 from EXAMINER.evaluate()
    mastered INTEGER NOT NULL DEFAULT 0,          -- 0 = not mastered, 1 = mastered
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (student_id, concept_id)
);
