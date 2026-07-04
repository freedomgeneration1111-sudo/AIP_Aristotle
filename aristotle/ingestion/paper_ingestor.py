"""Paper ingestor — background job that ingests an uploaded paper into the
vector store + corpus for RAG retrieval.

Pipeline stages:
  PARSE → CHUNK → EMBED → INDEX → ANALYZE → COMPLETE

Each stage updates the aristotle_ingest_job row so the GUI can render
progress. The job runs as a supervised background task (via
aip.adapter.extensions.supervision.supervised_task) and is cancelled on
shutdown.

Layer: imports from aip.foundation.protocols.actors (ActorContext),
aip.adapter.extensions.supervision (supervised_task), and aristotle's
own modules. Does NOT import from aip.orchestration.* directly —
container-duck-typed access only (extension boundary discipline).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols.actors import ActorContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job tracking helpers
# ---------------------------------------------------------------------------


async def _update_job(
    conn: Any,
    job_id: str,
    *,
    phase: str | None = None,
    status: str | None = None,
    chunks_total: int | None = None,
    chunks_done: int | None = None,
    analysis_complete: int | None = None,
    error: str | None = None,
    completed_at: str | None = None,
) -> None:
    """Update the aristotle_ingest_job row. Only updates non-None fields."""
    sets = []
    params: list[Any] = []
    if phase is not None:
        sets.append("phase = ?")
        params.append(phase)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if chunks_total is not None:
        sets.append("chunks_total = ?")
        params.append(chunks_total)
    if chunks_done is not None:
        sets.append("chunks_done = ?")
        params.append(chunks_done)
    if analysis_complete is not None:
        sets.append("analysis_complete = ?")
        params.append(analysis_complete)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if completed_at is not None:
        sets.append("completed_at = ?")
        params.append(completed_at)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(job_id)
    await conn.execute(
        f"UPDATE aristotle_ingest_job SET {', '.join(sets)} WHERE job_id = ?",
        tuple(params),
    )
    await conn.commit()


async def _create_job(
    conn: Any, job_id: str, material_id: str, filename: str,
) -> None:
    """Create the initial aristotle_ingest_job row."""
    await conn.execute(
        "INSERT INTO aristotle_ingest_job (job_id, material_id, filename, "
        "phase, status) VALUES (?, ?, ?, 'PENDING', 'PENDING')",
        (job_id, material_id, filename),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Chunking — structural, not fixed-char
# ---------------------------------------------------------------------------


def _chunk_paper_by_sections(text: str, max_chars: int = 1500) -> list[dict]:
    """Chunk a paper by structural sections.

    Strategy:
      1. Split on markdown-style headings (#, ##, ###) OR all-caps lines
         that look like section titles
      2. Within each section, split into paragraphs (double newline)
      3. Group paragraphs into chunks of ~max_chars, keeping sections
         together when possible
      4. Each chunk has metadata: section_path, heading, chunk_index

    Returns: list of {chunk_id, text, section_path, heading, chunk_index}
    """
    chunks: list[dict] = []
    chunk_index = 0

    # Split on headings: lines starting with # (markdown) OR lines that
    # are all-caps + short (likely section titles in plain text)
    import re

    # Pattern: markdown headings OR all-caps lines (3-80 chars, no lowercase)
    heading_pattern = re.compile(
        r'^(#{1,6}\s+.+|^[A-Z][A-Z0-9\s\-:,.()]{2,79}$)',
        re.MULTILINE,
    )

    # Find all heading positions
    headings = list(heading_pattern.finditer(text))

    if not headings:
        # No headings found — chunk by paragraph groups
        paragraphs = text.split('\n\n')
        current_chunk = ""
        current_heading = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > max_chars and current_chunk:
                chunks.append({
                    "chunk_id": _make_chunk_id(current_heading, chunk_index),
                    "text": current_chunk.strip(),
                    "section_path": current_heading,
                    "heading": current_heading,
                    "chunk_index": chunk_index,
                })
                chunk_index += 1
                current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para
        if current_chunk.strip():
            chunks.append({
                "chunk_id": _make_chunk_id(current_heading, chunk_index),
                "text": current_chunk.strip(),
                "section_path": current_heading,
                "heading": current_heading,
                "chunk_index": chunk_index,
            })
        return chunks

    # Iterate sections defined by headings
    for i, match in enumerate(headings):
        heading_line = match.group(0).lstrip('#').strip()
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section_text = text[start:end].strip()

        # Split section into paragraphs
        paragraphs = section_text.split('\n\n')
        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) > max_chars and current_chunk:
                chunks.append({
                    "chunk_id": _make_chunk_id(heading_line, chunk_index),
                    "text": current_chunk.strip(),
                    "section_path": heading_line,
                    "heading": heading_line,
                    "chunk_index": chunk_index,
                })
                chunk_index += 1
                current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para
        if current_chunk.strip():
            chunks.append({
                "chunk_id": _make_chunk_id(heading_line, chunk_index),
                "text": current_chunk.strip(),
                "section_path": heading_line,
                "heading": heading_line,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    return chunks


def _make_chunk_id(heading: str, index: int) -> str:
    """Generate a deterministic chunk ID from heading + index."""
    raw = f"{heading}:{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main ingestion job
# ---------------------------------------------------------------------------


async def ingest_paper(
    material_id: str,
    filename: str,
    extracted_text: str,
    container: Any,
    job_id: str,
) -> None:
    """Background job: parse → chunk → embed → index → analyze.

    Updates aristotle_ingest_job progress at each phase. On completion,
    the paper's chunks are in the vector store (domain="aristotle:textbook")
    + structural metadata is in aristotle_material_structure.

    Args:
        material_id: The aristotle_uploaded_material.id
        filename: Original filename (for progress display)
        extracted_text: Full extracted text (no truncation)
        container: The AipContainer (duck-typed)
        job_id: The job ID for progress tracking
    """
    logger.info("ingest_paper_started job_id=%s material_id=%s filename=%s", job_id, material_id, filename)

    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        await _fail_job(container, job_id, "corpus_registry not available")
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # Wait for migrations to be ready (ADR-014 §A5 gate)
        migration_ready = getattr(registry, "migration_ready", None)
        if migration_ready is not None:
            await migration_ready.wait()

        # --- Phase 1: PARSE (text already extracted by upload route) ---
        await _update_job(conn, job_id, phase="PARSING", status="RUNNING")
        if not extracted_text or len(extracted_text.strip()) < 100:
            await _update_job(
                conn, job_id, phase="FAILED", status="FAILED",
                error=f"Extracted text too short ({len(extracted_text or '')} chars) — likely a math-heavy PDF that pypdf cannot parse",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        # --- Phase 2: CHUNK ---
        await _update_job(conn, job_id, phase="CHUNKING")
        chunks = _chunk_paper_by_sections(extracted_text, max_chars=1500)
        await _update_job(conn, job_id, chunks_total=len(chunks))
        logger.info("ingest_chunked job_id=%s chunks=%d", job_id, len(chunks))

        if not chunks:
            await _update_job(
                conn, job_id, phase="FAILED", status="FAILED",
                error="Chunking produced 0 chunks",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        # --- Phase 3: EMBED + INDEX ---
        await _update_job(conn, job_id, phase="EMBEDDING")

        embedding_provider = getattr(container, "embedding_provider", None)
        vector_store = getattr(container, "vector_store", None)

        if embedding_provider is None or vector_store is None:
            await _fail_job(container, job_id, "embedding_provider or vector_store not available")
            return

        # Embed + upsert each chunk. No batch API — one call per chunk.
        # Use a semaphore to limit concurrency (avoid rate limits).
        sem = asyncio.Semaphore(3)

        async def embed_and_store(chunk: dict, idx: int) -> None:
            async with sem:
                try:
                    chunk_text = chunk["text"]
                    # Truncate to 2000 chars for embedding (model limit)
                    embed_text = chunk_text[:2000]
                    embedding = await embedding_provider.embed(embed_text)

                    # Upsert to vector store with domain filter
                    await vector_store.upsert(
                        id=chunk["chunk_id"],
                        embedding=embedding,
                        content=chunk_text,
                        metadata={
                            "material_id": material_id,
                            "filename": filename,
                            "section_path": chunk["section_path"],
                            "heading": chunk["heading"],
                            "chunk_index": chunk["chunk_index"],
                            "char_count": len(chunk_text),
                            "source": "aristotle_upload",
                        },
                        domain="aristotle:textbook",
                    )

                    # Also write to CorpusTurnStore for FTS5 lexical search
                    # (the vector store + FTS5 are complementary retrieval channels)
                    await _write_corpus_turn(stores, material_id, filename, chunk)

                    await _update_job(conn, job_id, chunks_done=idx + 1)
                except Exception as exc:
                    logger.warning(
                        "ingest_chunk_failed job_id=%s chunk_index=%d error=%s:%s",
                        job_id, idx, type(exc).__name__, exc,
                    )
                    # Don't fail the whole job — skip this chunk + continue

        # Process chunks concurrently (bounded by semaphore)
        tasks = [embed_and_store(chunk, idx) for idx, chunk in enumerate(chunks)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # --- Phase 4: ANALYZE (structural analysis via LLM) ---
        await _update_job(conn, job_id, phase="ANALYZING")
        try:
            from aristotle.ingestion.structural_analysis import analyze_paper_structure
            structure = await analyze_paper_structure(
                material_id, filename, chunks, container,
            )
            # Store structural metadata
            await _store_structure(conn, material_id, chunks, structure)
            await _update_job(conn, job_id, analysis_complete=1)
            logger.info("ingest_analysis_complete job_id=%s", job_id)
        except Exception as exc:
            logger.warning(
                "ingest_analysis_failed job_id=%s error=%s:%s — "
                "chunks are indexed but structural metadata is missing",
                job_id, type(exc).__name__, exc,
            )
            # Don't fail the job — chunks are indexed, analysis is optional
            # The IntakeActor can still retrieve chunks via RAG

        # --- Phase 5: COMPLETE ---
        await _update_job(
            conn, job_id,
            phase="COMPLETE",
            status="COMPLETE",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("ingest_paper_complete job_id=%s chunks=%d", job_id, len(chunks))

    except Exception as exc:
        logger.error(
            "ingest_paper_failed job_id=%s error=%s:%s",
            job_id, type(exc).__name__, exc, exc_info=True,
        )
        await _fail_job(container, job_id, f"{type(exc).__name__}: {exc}")


async def _write_corpus_turn(stores: Any, material_id: str, filename: str, chunk: dict) -> None:
    """Write a chunk to the CorpusTurnStore for FTS5 lexical search.

    The CorpusTurnStore auto-syncs an FTS5 index via triggers, so this
    enables lexical (keyword) search alongside vector search.
    """
    if stores.turn_store is None:
        return
    try:
        from aip.foundation.schemas.corpus_turn import CorpusTurn
        import hashlib as _hashlib

        conv_id = _hashlib.sha256(material_id.encode()).hexdigest()[:16]
        turn_id = chunk["chunk_id"]
        turn = CorpusTurn(
            turn_id=turn_id,
            conversation_id=conv_id,
            conversation_name=filename,
            turn_index=chunk["chunk_index"],
            source_model="aristotle_upload",
            source_account="aristotle",
            export_date="",
            user_text=chunk["heading"] or f"Chunk {chunk['chunk_index']}",
            assistant_text=chunk["text"],
            turn_timestamp="",
            domains=["aristotle:textbook"],
            primary_domain="aristotle:textbook",
            tags=[],
            importance=0.5,
            bridges=[],
            beast_confidence=0.0,
            tagging_version=0,
            content_hash=_hashlib.sha256(chunk["text"].encode()).hexdigest(),
            source_path=filename,
        )
        await stores.turn_store.write_turn(turn)
    except Exception as exc:
        logger.debug("corpus_turn_write_failed chunk_id=%s error=%s", chunk["chunk_id"], exc)


async def _store_structure(
    conn: Any, material_id: str, chunks: list[dict], structure: dict,
) -> None:
    """Store structural metadata in aristotle_material_structure."""
    concept_map = structure.get("concept_map", {})
    prereq_map = structure.get("prereq_map", {})
    citation_map = structure.get("citation_map", {})

    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        await conn.execute(
            "INSERT INTO aristotle_material_structure "
            "(material_id, chunk_id, turn_id, section_path, heading, "
            "chunk_index, page_range, char_count, concept_tags_json, "
            "prereq_tags_json, citation_ids_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                material_id,
                chunk_id,
                chunk_id,
                chunk["section_path"],
                chunk["heading"],
                chunk["chunk_index"],
                None,  # page_range — TODO: extract from PDF metadata
                chunk.get("char_count", len(chunk["text"])),
                json.dumps(concept_map.get(chunk_id, [])),
                json.dumps(prereq_map.get(chunk_id, [])),
                json.dumps(citation_map.get(chunk_id, [])),
            ),
        )
    await conn.commit()

    # Store citations in aristotle_citation
    citations = structure.get("citations", [])
    for cite in citations:
        await conn.execute(
            "INSERT INTO aristotle_citation "
            "(material_id, citation_key, raw_text, citation_type, resolved_id, fetch_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                material_id,
                cite.get("key", ""),
                cite.get("raw_text", ""),
                cite.get("type", "unknown"),
                cite.get("resolved_id", ""),
                "PENDING",
            ),
        )
    await conn.commit()


async def _fail_job(container: Any, job_id: str, error: str) -> None:
    """Mark a job as failed."""
    try:
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        await _update_job(
            conn, job_id,
            phase="FAILED",
            status="FAILED",
            error=error,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.error("fail_job_update_failed job_id=%s error=%s", job_id, exc)


# ---------------------------------------------------------------------------
# Job creation + status helpers (called from API routes)
# ---------------------------------------------------------------------------


async def create_ingest_job(
    container: Any, material_id: str, filename: str,
) -> str:
    """Create a new ingest job row + return the job_id. Does NOT start the job."""
    job_id = str(uuid.uuid4())
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        raise RuntimeError("corpus_registry not available")
    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn
    await _create_job(conn, job_id, material_id, filename)
    return job_id


async def get_job_status(container: Any, job_id: str) -> dict | None:
    """Get the status of an ingest job. Returns None if not found."""
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return None
    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT job_id, material_id, filename, phase, status, "
        "chunks_total, chunks_done, analysis_complete, error, "
        "started_at, updated_at, completed_at "
        "FROM aristotle_ingest_job WHERE job_id = ?",
        (job_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return {
        "job_id": row[0],
        "material_id": row[1],
        "filename": row[2],
        "phase": row[3],
        "status": row[4],
        "chunks_total": row[5],
        "chunks_done": row[6],
        "analysis_complete": bool(row[7]),
        "error": row[8],
        "started_at": row[9],
        "updated_at": row[10],
        "completed_at": row[11],
    }


async def get_material_structure(container: Any, material_id: str) -> list[dict]:
    """Get the structural metadata for an ingested paper. Returns list of chunk metadata."""
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return []
    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT chunk_id, section_path, heading, chunk_index, "
        "page_range, char_count, concept_tags_json, prereq_tags_json, "
        "citation_ids_json "
        "FROM aristotle_material_structure "
        "WHERE material_id = ? ORDER BY chunk_index",
        (material_id,),
    )
    rows = await cur.fetchall()
    await cur.close()
    return [
        {
            "chunk_id": r[0],
            "section_path": r[1],
            "heading": r[2],
            "chunk_index": r[3],
            "page_range": r[4],
            "char_count": r[5],
            "concept_tags": json.loads(r[6]) if r[6] else [],
            "prereq_tags": json.loads(r[7]) if r[7] else [],
            "citation_ids": json.loads(r[8]) if r[8] else [],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# RAG retrieval (used by IntakeActor)
# ---------------------------------------------------------------------------


async def retrieve_relevant_chunks(
    container: Any,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Retrieve top-K chunks from the vector store relevant to the query.

    Filters by domain="aristotle:textbook" to scope to ARISTOTLE paper
    chunks only (no collision with definer corpus chunks).

    Returns: list of {chunk_id, content, score, metadata}
    """
    embedding_provider = getattr(container, "embedding_provider", None)
    vector_store = getattr(container, "vector_store", None)
    if embedding_provider is None or vector_store is None:
        return []
    if not query or not query.strip():
        return []

    try:
        query_vec = await embedding_provider.embed(query[:2000])
        chunks = await vector_store.retrieve(
            query_vec, domain="aristotle:textbook", top_k=top_k,
        )
        return [
            {
                "chunk_id": c.id,
                "content": c.content,
                "score": c.score,
                "metadata": c.metadata or {},
            }
            for c in chunks
        ]
    except Exception as exc:
        logger.warning(
            "rag_retrieval_failed query=%r error=%s:%s",
            query[:80], type(exc).__name__, exc,
        )
        return []


async def get_structural_map(container: Any, material_id: str) -> dict:
    """Get a compact structural map (TOC + concept index) for the LLM.

    Returns: {toc: [{heading, chunk_index}], concepts: [...], citations: [...]}
    """
    structure = await get_material_structure(container, material_id)
    if not structure:
        return {"toc": [], "concepts": [], "citations": []}

    toc = [
        {"heading": s["section_path"] or s["heading"], "chunk_index": s["chunk_index"]}
        for s in structure
    ]

    # Aggregate concept tags across all chunks
    all_concepts: set[str] = set()
    for s in structure:
        all_concepts.update(s["concept_tags"])

    # Get citations
    registry = getattr(container, "corpus_registry", None)
    citations: list[dict] = []
    if registry is not None:
        try:
            stores = await registry.get_stores("aristotle:textbook")
            conn = stores.connection_manager.write_conn
            cur = await conn.execute(
                "SELECT citation_key, raw_text, citation_type, resolved_id "
                "FROM aristotle_citation WHERE material_id = ?",
                (material_id,),
            )
            rows = await cur.fetchall()
            await cur.close()
            citations = [
                {
                    "key": r[0],
                    "raw_text": r[1],
                    "type": r[2],
                    "resolved_id": r[3],
                }
                for r in rows
            ]
        except Exception:
            pass

    return {
        "toc": toc,
        "concepts": sorted(all_concepts),
        "citations": citations,
    }
