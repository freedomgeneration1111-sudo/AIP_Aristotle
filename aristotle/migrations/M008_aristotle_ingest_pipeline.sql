-- M008_aristotle_ingest_pipeline.sql — Multi-phase paper ingestion pipeline (ADR-003).
--
-- Adds the tables that support background paper ingestion, RAG retrieval,
-- and multi-step plan generation. Replaces the stub single-row text storage
-- in M007 with proper chunking, embedding, and structural analysis.
--
-- This migration is additive only — no existing column or table is
-- modified or dropped. The old aristotle_uploaded_material table stays
-- for backward compatibility with existing sessions.
--
-- IMPORTANT: the migration runner splits on semicolon naively (same as
-- the core runner). Comments must NOT contain semicolons or the split
-- breaks mid-comment and SQLite parses comment text as SQL. This file
-- keeps all semicolons out of comments and uses them only as SQL
-- statement terminators.

-- aristotle_ingest_job: tracks background ingestion jobs
-- One row per paper upload. Updated at each phase of the pipeline.
-- The GUI polls GET /aristotle/ingest/{job_id}/status to render progress.
CREATE TABLE IF NOT EXISTS aristotle_ingest_job (
    job_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'PENDING',
    status TEXT NOT NULL DEFAULT 'PENDING',
    chunks_total INTEGER NOT NULL DEFAULT 0,
    chunks_done INTEGER NOT NULL DEFAULT 0,
    analysis_complete INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_job_material
    ON aristotle_ingest_job(material_id);

CREATE INDEX IF NOT EXISTS idx_ingest_job_status
    ON aristotle_ingest_job(status, updated_at);

-- aristotle_material_structure: structural metadata per chunk
-- One row per chunk of an ingested paper. Populated during the ANALYZE
-- phase by LLM structural analysis. Used by the IntakeActor to build
-- the "structural map" (TOC + concept index) shown to the LLM each turn.
CREATE TABLE IF NOT EXISTS aristotle_material_structure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    section_path TEXT,
    heading TEXT,
    chunk_index INTEGER NOT NULL,
    page_range TEXT,
    char_count INTEGER,
    concept_tags_json TEXT,
    prereq_tags_json TEXT,
    citation_ids_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_material_structure_material
    ON aristotle_material_structure(material_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_material_structure_chunk
    ON aristotle_material_structure(chunk_id);

-- aristotle_citation: extracted citations
-- Populated during the ANALYZE phase. Phase 4 (future) will fetch each
-- cited paper via arXiv/DOI and ingest it through the same pipeline.
CREATE TABLE IF NOT EXISTS aristotle_citation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id TEXT NOT NULL,
    citation_key TEXT,
    raw_text TEXT NOT NULL,
    citation_type TEXT,
    resolved_id TEXT,
    fetched_material_id TEXT,
    fetch_status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_citation_material
    ON aristotle_citation(material_id);

CREATE INDEX IF NOT EXISTS idx_citation_fetch_status
    ON aristotle_citation(fetch_status);

-- aristotle_plan_job: tracks multi-step plan generation jobs
-- One row per plan generation pipeline run. Updated at each step.
-- The GUI polls GET /aristotle/plan/{job_id}/status to render progress.
CREATE TABLE IF NOT EXISTS aristotle_plan_job (
    job_id TEXT PRIMARY KEY,
    plan_id TEXT,
    material_id TEXT,
    session_json TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'PENDING',
    status TEXT NOT NULL DEFAULT 'PENDING',
    steps_total INTEGER NOT NULL DEFAULT 6,
    steps_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_job_status
    ON aristotle_plan_job(status, updated_at);
