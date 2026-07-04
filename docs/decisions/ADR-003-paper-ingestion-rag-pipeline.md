# ADR-003: Multi-Phase Paper Ingestion + RAG + Plan Generation Pipeline

**Status:** Proposed
**Date:** 2026-07-05
**Author:** Super Z (coding agent)
**Supersedes:** The stub ingestion in ADR-002 §9 (single-row text storage + truncation)

## Context

The current paper upload design (ADR-002 §9, implemented in `aristotle/api.py::upload_route` + `aristotle/actors/intake.py::_fetch_material_texts`) is a stub:

1. Upload extracts full text via pypdf, stores it as a **single row** in `aristotle_uploaded_material`.
2. Each intake turn, `_fetch_material_texts` reads the full text and `_build_intake_user_prompt` truncates it to `material_preview_chars` (default 20,000).
3. The LLM sees only the first ~5,000 tokens of the paper. For NBCM (56,718 chars), 36,718 chars are invisible.
4. Plan generation is a single LLM call that must understand the paper's full structure, identify prerequisites, map the learner's gaps, and design a phased path — all from a partial read.

This is insincere. A study system built around a paper cannot truncate the paper. The learner cannot trust a plan derived from content the LLM never saw.

### What AIP Brain already has (but ARISTOTLE doesn't use)

| Capability | Location | Used by ARISTOTLE? |
|---|---|---|
| Document parser (PDF/markdown/text → CorpusTurns) | `aip.orchestration.ingestion.parsers.document_parser` | ❌ |
| Chunker (recursive char split) | `aip.orchestration.ingestion.chunker` | ❌ |
| Embedding provider (OpenAI-compatible, async) | `aip.adapter.embedding` | ❌ |
| Vector store (sqlite_vss/pgvector, top-K retrieval) | `aip.adapter.vector` | ❌ |
| Corpus registry (multi-corpus, migration-gated) | `aip.adapter.corpus_registry` | ❌ (only for mastery tables) |
| Background task supervision | `aip.adapter.extensions.supervision.supervised_task` | ❌ |
| FTS5 lexical search (auto-synced via triggers) | `CorpusTurnStore` | ❌ |

The infrastructure exists. The IntakeActor has its own parallel storage that bypasses all of it.

## Decision

Build a multi-phase pipeline that treats paper ingestion as a first-class background job, retrieves chunks via RAG at query time, and generates learning plans through a multi-step retrieval-driven process.

### Phase 1: Ingestion (background job, not chat)

When a paper is uploaded, kick off a **background ingestion job**. The upload route returns immediately with a `job_id`. The GUI polls for progress.

**Pipeline stages:**

```
upload PDF
  → PARSE: extract full text (pypdf, no truncation)
  → CHUNK: split by structural sections (heading detection + paragraph boundaries)
  → EMBED: embed each chunk via the embedding provider
  → INDEX: upsert vectors into the vector store with domain="aristotle:textbook"
  → ANALYZE: LLM structural analysis (TOC map, concept index, prerequisite graph, citations)
  → COMPLETE: store structural metadata, mark job done
```

**Storage:**
- Chunks → `aristotle:textbook` corpus via `CorpusTurnStore.write_turn()` (FTS5 auto-syncs)
- Vectors → global `container.vector_store` with `domain="aristotle:textbook"` (per-corpus vector stores are not yet wired in `CorpusStoreFactory`)
- Structural metadata → new `aristotle_material_structure` table (M008 migration)
- Job state → new `aristotle_ingest_job` table (M008 migration)

**Why background:**
- Embedding 56k chars (~14 chunks) takes 5-30 seconds on OpenRouter
- Structural analysis is 2-4 LLM calls (30-60 seconds)
- The learner should see a progress indicator, not a frozen chat

### Phase 2: RAG retrieval in IntakeActor

Replace the "Uploaded materials" block in `_build_intake_user_prompt` with retrieved chunks:

```
Each intake turn:
  1. Build a retrieval query from the conversation context
     (current focus + learner's latest reply + extracted understanding)
  2. Embed the query
  3. Retrieve top-K chunks from the vector store (domain="aristotle:textbook")
  4. Also retrieve the paper's structural map (TOC + concept index — compact, ~2k tokens)
  5. Feed the LLM: system prompt + conversation + structural map + retrieved chunks
```

No truncation. The LLM sees the paper's structure always, and the specific chunks relevant to the current question. Over multiple turns, the LLM effectively "reads" the whole paper.

### Phase 3: Multi-step plan generation (background job)

When the learner has answered enough intake questions, plan generation becomes a **pipeline**, not a single call:

```
Step 1: Retrieve the paper's structural map (from Phase 1 metadata)
Step 2: Retrieve chunks relevant to "foundational concepts"
        → vector search: "prerequisites for understanding this paper"
Step 3: LLM call: "Given these foundational sections + learner's background,
        what are the knowledge gaps?"
Step 4: For each identified gap, retrieve chunks relevant to that gap
Step 5: LLM call: "Given these gap-specific sections, design a phased plan
        that bridges from learner's current knowledge to the paper"
Step 6: LLM call: "For each phase, identify which paper sections + external
        prerequisites are needed"
Step 7: Store the plan with chunk references (so tutoring can retrieve
        the right sections per concept)
```

Each step retrieves ONLY the chunks it needs. The LLM sees the full paper across the pipeline, just not all at once. This is a background job with progress tracking — the GUI shows "Analyzing your paper..." while it runs.

### Phase 4: Citation fetching (future, designed for now)

The structural analysis (Phase 1) extracts the citation list as metadata. A future phase will:
1. Resolve each citation (arXiv ID, DOI, URL)
2. Fetch the cited paper's full text (web calls)
3. Ingest each cited paper through the same Phase 1 pipeline
4. Build a citation graph (which papers depend on which)
5. The plan generator can then include cited papers as additional study material

This is designed for but NOT built in the initial implementation. The schema accommodates it.

## Consequences

### Positive
- No truncation. The LLM sees the full paper across the pipeline.
- Scales to textbooks (300k+ chars) and multiple papers — each chunk is retrieved on demand.
- Plan generation is grounded in the paper's actual structure, not a partial read.
- The learner sees progress indicators for long-running jobs.
- Reuses existing AIP Brain infrastructure (chunker, embedder, vector store, corpus registry) — no reinvention.

### Negative
- **Complexity.** This is a real build: 4 new modules, 1 migration, 3 new API routes, GUI changes. ~1,500-2,000 lines of code.
- **Latency.** Ingestion takes 30-60 seconds (embedding + structural analysis). The learner must wait. Mitigated by progress indicator.
- **Cost.** Each paper requires ~14 embedding calls + ~4 LLM analysis calls at ingestion time. Plan generation adds ~3-5 LLM calls. On OpenRouter free-tier, this is $0; on paid models, ~$0.05-0.15 per paper.
- **Domain filtering required.** `stores.vector_store` is None for `aristotle:textbook` (per Chunk 6/8 boundary). Must use `container.vector_store` with `domain="aristotle:textbook"` filter to avoid colliding with definer corpus chunks.

### Migration path
- The old `aristotle_uploaded_material` table stays (for backward compatibility with existing sessions).
- The new `aristotle_ingest_job` + `aristotle_material_structure` tables are additive (M008).
- The upload route changes from synchronous to async (returns `job_id` immediately).
- The IntakeActor's `_fetch_material_texts` is replaced with RAG retrieval — old sessions without ingested chunks fall back to the old truncation path.

## Implementation plan

### Files to create

| File | Purpose |
|---|---|
| `aristotle/migrations/M008_aristotle_ingest_pipeline.sql` | `aristotle_ingest_job` + `aristotle_material_structure` tables |
| `aristotle/ingestion/__init__.py` | Package init |
| `aristotle/ingestion/paper_ingestor.py` | Background ingestion job (parse → chunk → embed → index → analyze) |
| `aristotle/ingestion/structural_analysis.py` | LLM calls for TOC, concept index, prereq graph, citations |
| `aristotle/ingestion/citation_fetcher.py` | (Phase 4 stub) arXiv/DOI lookup + fetch |
| `aristotle/actors/plan_generator.py` | Multi-step retrieval-driven plan pipeline |
| `tests/test_aristotle_ingest_pipeline.py` | Tests for ingestion + RAG retrieval |
| `tests/test_aristotle_plan_generator.py` | Tests for multi-step plan generation |

### Files to modify

| File | Change |
|---|---|
| `aristotle/api.py::upload_route` | Kick off background ingestion job, return `job_id` |
| `aristotle/api.py` | Add `GET /aristotle/ingest/{job_id}/status`, `GET /aristotle/material/{material_id}/structure`, `POST /aristotle/plan/generate` |
| `aristotle/actors/intake.py::_build_intake_user_prompt` | Replace "Uploaded materials" block with RAG retrieval |
| `aristotle/actors/intake.py::_fetch_material_texts` | Replace with `_retrieve_relevant_chunks` (vector search) |
| `aristotle/config.py` | Add `rag_top_k`, `rag_chunk_chars`, `ingest_analysis_model_slot` settings |
| `gui/pages/ask.py` (AIP_Brain) | Show ingestion progress indicator + plan generation progress |

### Build order

1. **M008 migration** — the schema foundation
2. **`paper_ingestor.py`** — the background job (parse → chunk → embed → index)
3. **`structural_analysis.py`** — LLM analysis calls
4. **Upload route modification** — kick off the job, return `job_id`
5. **New API routes** — job status, material structure
6. **IntakeActor RAG retrieval** — replace truncation with top-K chunks
7. **`plan_generator.py`** — multi-step plan pipeline
8. **`POST /aristotle/plan/generate`** — trigger the pipeline
9. **GUI changes** — progress indicators
10. **Tests** — for each component as it's built

## Detailed design

### M008 schema

```sql
-- aristotle_ingest_job: tracks background ingestion jobs
CREATE TABLE IF NOT EXISTS aristotle_ingest_job (
    job_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL,          -- FK to aristotle_uploaded_material.id
    filename TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|PARSING|CHUNKING|EMBEDDING|INDEXING|ANALYZING|COMPLETE|FAILED
    status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING|RUNNING|COMPLETE|FAILED
    chunks_total INTEGER DEFAULT 0,
    chunks_done INTEGER DEFAULT 0,
    analysis_complete INTEGER DEFAULT 0,    -- 0 or 1
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- aristotle_material_structure: structural metadata per chunk
CREATE TABLE IF NOT EXISTS aristotle_material_structure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,              -- CorpusTurn.turn_id
    turn_id TEXT NOT NULL,               -- same as chunk_id (corpus turn id)
    section_path TEXT,                   -- e.g., "Chapter 3 > 3.2 Null Surfaces"
    heading TEXT,                        -- the heading text
    chunk_index INTEGER NOT NULL,        -- 0-based order within the paper
    page_range TEXT,                     -- e.g., "5-7" (from PDF metadata)
    char_count INTEGER,
    concept_tags_json TEXT,              -- JSON array of concepts this chunk covers
    prereq_tags_json TEXT,               -- JSON array of prerequisite concepts
    citation_ids_json TEXT,              -- JSON array of citation IDs referenced
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (material_id) REFERENCES aristotle_uploaded_material(id)
);

CREATE INDEX IF NOT EXISTS idx_material_structure_material
    ON aristotle_material_structure(material_id, chunk_index);

-- aristotle_citation: extracted citations (Phase 4 fetches these)
CREATE TABLE IF NOT EXISTS aristotle_citation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id TEXT NOT NULL,           -- the paper that cites this
    citation_key TEXT,                   -- e.g., "[1]", "Jorgensen2026"
    raw_text TEXT NOT NULL,              -- full citation text
    citation_type TEXT,                  -- arxiv|doi|url|book|unknown
    resolved_id TEXT,                    -- arXiv ID / DOI / URL (if parsed)
    fetched_material_id TEXT,            -- FK to aristotle_uploaded_material if fetched
    fetch_status TEXT DEFAULT 'PENDING', -- PENDING|FETCHED|FAILED|SKIPPED
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (material_id) REFERENCES aristotle_uploaded_material(id)
);

CREATE INDEX IF NOT EXISTS idx_citation_material
    ON aristotle_citation(material_id);
```

### paper_ingestor.py — background job

```python
async def ingest_paper(
    material_id: str,
    filename: str,
    extracted_text: str,
    container: Any,
    job_id: str,
) -> None:
    """Background job: parse → chunk → embed → index → analyze.

    Updates aristotle_ingest_job progress at each phase.
    On completion, the paper's chunks are in the vector store
    (domain="aristotle:textbook") + structural metadata is in
    aristotle_material_structure.
    """
    # 1. Update job: PARSING
    # 2. Chunk the text (structural chunking by heading + paragraph)
    # 3. Update job: CHUNKING (chunks_total = N)
    # 4. Write chunks to CorpusTurnStore (FTS5 auto-syncs)
    # 5. Update job: EMBEDDING
    # 6. Embed each chunk + upsert to vector_store (domain="aristotle:textbook")
    # 7. Update job: INDEXING (chunks_done increments)
    # 8. Update job: ANALYZING
    # 9. Run structural analysis (LLM calls)
    # 10. Update job: COMPLETE
```

### IntakeActor RAG retrieval

```python
async def _retrieve_relevant_chunks(
    session: IntakeSession,
    student_input: str,
    container: Any,
    top_k: int = 5,
) -> list[dict]:
    """Retrieve top-K chunks relevant to the current conversation context.

    Builds a query from: current focus + learner's latest reply +
    extracted understanding. Embeds the query, retrieves chunks from
    the vector store (domain="aristotle:textbook").
    """
    if not session.material_ids:
        return []

    # Build retrieval query
    query_parts = [session.current_focus]
    if student_input:
        query_parts.append(student_input)
    if session.extracted.get("subject"):
        query_parts.append(session.extracted["subject"])
    query = " ".join(query_parts)

    # Embed + retrieve
    embedding_provider = getattr(container, "embedding_provider", None)
    vector_store = getattr(container, "vector_store", None)
    if not embedding_provider or not vector_store:
        return []

    query_vec = await embedding_provider.embed(query)
    chunks = await vector_store.retrieve(
        query_vec, domain="aristotle:textbook", top_k=top_k,
    )

    return [
        {
            "chunk_id": c.id,
            "content": c.content,
            "score": c.score,
            "metadata": c.metadata,
        }
        for c in chunks
    ]
```

### plan_generator.py — multi-step pipeline

```python
async def generate_plan(
    session: IntakeSession,
    container: Any,
    job_id: str,
) -> dict:
    """Multi-step retrieval-driven plan generation.

    Steps:
      1. Retrieve paper's structural map (TOC + concept index)
      2. Retrieve chunks relevant to "foundational concepts"
      3. LLM: identify knowledge gaps given learner's background
      4. For each gap, retrieve relevant chunks
      5. LLM: design phased plan bridging gaps to paper
      6. LLM: identify paper sections + external prereqs per phase
      7. Store plan with chunk references

    Returns: {"plan_id": ..., "phases": [...], "concept_count": N}
    """
```

## References

- ADR-002 §9 — the original intake + plan design (now superseded by this ADR)
- AIP_Brain `aip.orchestration.ingestion.parsers.document_parser` — PDF/markdown parsing
- AIP_Brain `aip.adapter.vector.sqlite_vss_store` — vector store with domain filtering
- AIP_Brain `aip.adapter.extensions.supervision.supervised_task` — background task helper
- AIP_Brain `aip.adapter.corpus_registry` — multi-corpus infrastructure
