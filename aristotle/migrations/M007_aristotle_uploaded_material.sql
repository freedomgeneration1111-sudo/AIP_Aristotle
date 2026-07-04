-- M007_aristotle_uploaded_material.sql — Phase D material storage (ADR-002 §9 stage 3).
--
-- Adds the table that stores uploaded textbook/paper/notes content so the
-- INTAKE actor can reference it during the LLM-driven intake conversation
-- and the plan generator can derive concepts from the actual material
-- rather than a LIKE query against sample data.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner — src/aip/adapter/extensions/loaders/migration_loader.py:151).
-- Comments must NOT contain semicolons or the split breaks mid-comment
-- and SQLite parses comment text as SQL. This file keeps all semicolons
-- out of comments and uses them only as SQL statement terminators.

-- aristotle_uploaded_material (ADR-002 §9 stage 3, §10 new table)
-- One row per uploaded file. extracted_text holds the full text content
-- (PDF via pypdf, image via pytesseract OCR, html via tag stripping,
-- txt/md/csv/json/yaml via UTF-8 decode).
-- The INTAKE actor reads extracted_text for any material_id in its
-- session.material_ids list and includes it in the model context so
-- the model can ask informed questions and derive concepts from the
-- actual content.
-- concept_ids_json is populated when the plan generator ingests new
-- concepts derived from this material — links the material to the
-- concepts it informed.
CREATE TABLE IF NOT EXISTS aristotle_uploaded_material (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL DEFAULT 'definer',
    filename TEXT NOT NULL,
    source_type TEXT NOT NULL
        CHECK(source_type IN ('pdf', 'image', 'text', 'html')),
    extracted_text TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER,
    concept_ids_json TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_uploaded_material_student
    ON aristotle_uploaded_material(student_id, ingested_at);
