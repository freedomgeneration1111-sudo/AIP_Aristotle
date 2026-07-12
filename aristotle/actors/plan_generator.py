"""Plan generator — multi-step retrieval-driven learning plan pipeline (ADR-003 Phase 3).

Replaces the single-call plan generation in IntakeActor.generate_plan()
with a multi-step pipeline that retrieves relevant chunks at each step:

  Step 1: Retrieve the paper's structural map (TOC + concept index)
  Step 2: Retrieve chunks relevant to "foundational concepts"
  Step 3: LLM: identify knowledge gaps given learner's background
  Step 4: For each gap, retrieve relevant chunks
  Step 5: LLM: design phased plan bridging gaps to paper
  Step 6: LLM: identify paper sections + external prereqs per phase
  Step 7: Store the plan with chunk references

Each step retrieves ONLY the chunks it needs — no truncation, no resending
the whole paper. The LLM sees the full paper across the pipeline, just
not all at once.

Runs as a background job (supervised_task). The GUI polls
GET /aristotle/plan/{job_id}/status for progress.

Layer: imports from aip.foundation.protocols.actors (ActorContext) +
aristotle's own modules. No aip.orchestration imports (extension boundary).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan generation system prompts (one per LLM step)
# ---------------------------------------------------------------------------

_GAP_ANALYSIS_PROMPT = """You are an expert tutor analyzing a learner's background against a paper's requirements. Given the learner's self-reported background, the paper's structural map (table of contents + key concepts), and excerpts from the paper's foundational sections, identify the specific knowledge gaps that must be bridged for the learner to understand the paper.

Return valid JSON:
{
  "gaps": [
    {
      "concept": "the missing concept (e.g., 'differential geometry', 'tensor calculus')",
      "severity": "critical|important|nice-to-have",
      "reason": "why this gap matters for this specific paper",
      "paper_sections": ["section headings from the TOC that require this concept"],
      "estimated_study_time_hours": 5
    }
  ]
}

Rules:
- Be specific. "Math" is not a gap; "multivariable calculus" is.
- Severity: "critical" = cannot understand the paper without it; "important" = will struggle but can follow; "nice-to-have" = enhances understanding.
- Ground each gap in the paper's actual sections (cite the TOC entries).
- Estimate study time realistically for a self-learner at 30 min/day."""


_PHASED_PLAN_PROMPT = """You are an expert curriculum designer. Given the learner's background, the paper's structural map, the identified knowledge gaps, and excerpts from the paper relevant to each gap, design a phased learning plan that bridges from the learner's current knowledge to full comprehension of the paper.

Return valid JSON:
{
  "phases": [
    {
      "phase_number": 1,
      "phase_name": "short name (e.g., 'Mathematical Foundations')",
      "goal": "what the learner will be able to do after this phase",
      "concepts": ["concept1", "concept2"],
      "paper_sections": ["sections from the TOC covered in this phase"],
      "prerequisites": ["concepts from previous phases (empty for phase 1)"],
      "estimated_sessions": 10,
      "chunk_ids": ["chunk IDs from the retrieved excerpts relevant to this phase"]
    }
  ]
}

Rules:
- Order phases by prerequisite dependency (foundations first).
- Each phase should take 5-15 sessions (at 30 min/day, that's 1-3 weeks).
- Ground every phase in the paper's actual sections (cite the TOC).
- The final phase should cover the paper's advanced/original content.
- Include chunk_ids from the excerpts so the tutoring system can retrieve the right sections per concept."""


_CONCEPT_DETAIL_PROMPT = """You are an expert tutor. Given a phase from a learning plan, the paper's structural map, and retrieved excerpts, produce a list of specific learning concepts for that phase. Each concept will become a row in aristotle_concept and a node in the tutoring loop.

Return valid JSON:
{
  "concepts": [
    {
      "topic": "short topic name",
      "subtopic": "more specific",
      "bloom_target": 1-6,
      "content_primary": "1-2 sentence description of what this concept covers",
      "prerequisite_concept_id": null or index of prerequisite in this list,
      "paper_chunk_ids": ["chunk IDs from the paper relevant to this concept"],
      "estimated_sessions": 2
    }
  ]
}

Rules:
- 3-8 concepts per phase (enough for a meaningful unit, not so many it's overwhelming).
- Bloom target: 1=remember, 2=understand, 3=apply, 4=analyze, 5=evaluate, 6=create.
- Order concepts by prerequisite dependency within the phase.
- Ground each concept in the paper's actual content (cite chunk_ids)."""


# ---------------------------------------------------------------------------
# Job tracking helpers
# ---------------------------------------------------------------------------


async def _update_plan_job(
    conn: Any,
    job_id: str,
    *,
    phase: str | None = None,
    status: str | None = None,
    steps_done: int | None = None,
    error: str | None = None,
    plan_id: str | None = None,
    completed_at: str | None = None,
) -> None:
    """Update the aristotle_plan_job row."""
    sets = []
    params: list[Any] = []
    if phase is not None:
        sets.append("phase = ?")
        params.append(phase)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if steps_done is not None:
        sets.append("steps_done = ?")
        params.append(steps_done)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if plan_id is not None:
        sets.append("plan_id = ?")
        params.append(plan_id)
    if completed_at is not None:
        sets.append("completed_at = ?")
        params.append(completed_at)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(job_id)
    await conn.execute(
        f"UPDATE aristotle_plan_job SET {', '.join(sets)} WHERE job_id = ?",
        tuple(params),
    )
    await conn.commit()


async def _create_plan_job(
    conn: Any, job_id: str, session_json: str, material_id: str | None,
) -> None:
    """Create the initial aristotle_plan_job row."""
    await conn.execute(
        "INSERT INTO aristotle_plan_job (job_id, session_json, material_id, "
        "phase, status, steps_total) VALUES (?, ?, ?, 'PENDING', 'PENDING', 6)",
        (job_id, session_json, material_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Main multi-step pipeline
# ---------------------------------------------------------------------------


async def generate_plan_pipeline(
    session: Any,  # IntakeSession
    container: Any,
    job_id: str,
) -> None:
    """Background job: multi-step retrieval-driven plan generation.

    Steps:
      1. Retrieve paper's structural map (TOC + concept index)
      2. Retrieve chunks relevant to "foundational concepts"
      3. LLM: identify knowledge gaps given learner's background
      4. For each gap, retrieve relevant chunks
      5. LLM: design phased plan bridging gaps to paper
      6. LLM: produce detailed concepts per phase + ingest into aristotle_concept

    Updates aristotle_plan_job progress at each step. On completion,
    the plan is stored in aristotle_learning_plan + concepts in
    aristotle_concept.

    Args:
        session: IntakeSession (with draft_plan, extracted, material_ids)
        container: The AipContainer (duck-typed)
        job_id: The plan job ID for progress tracking
    """
    from aristotle.ingestion.paper_ingestor import (
        retrieve_relevant_chunks, get_structural_map,
    )
    from aristotle.actors.intake import IntakeActor, IntakeSession, intake_session_to_dict

    logger.info("plan_generation_started job_id=%s material_ids=%s", job_id, session.material_ids)

    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        await _fail_plan_job(container, job_id, "corpus_registry not available")
        return

    model_provider = getattr(container, "model_provider", None)
    if model_provider is None:
        await _fail_plan_job(container, job_id, "model_provider not available")
        return

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn

        # --- Step 1: Retrieve structural map ---
        await _update_plan_job(conn, job_id, phase="STRUCTURE_RETRIEVAL", status="RUNNING", steps_done=0)

        # Task 16: structural analysis (structural_analysis.py) is a single
        # LLM call for the whole paper and is documented as only reliable up
        # to ~30 chunks; larger textbooks routinely exceed the 120s ingest
        # timeout and get skipped (see paper_ingestor.py's
        # "ingest_analysis_timeout ... skipping (chunks are indexed, RAG
        # will work without structure)"). That's an accepted degraded mode
        # at ingestion time — get_structural_map() returns {"toc": [], ...}
        # rather than raising. Every downstream prompt-builder in this file
        # already loops over structural_maps defensively (safe on an empty
        # list), so there is no reason to hard-fail the whole plan job here.
        # Doing so previously meant any material over ~30 chunks could NEVER
        # get a plan — "confirming the plan again" just re-ran the same
        # doomed check forever, since nothing retries the skipped analysis.
        structural_maps: list[dict] = []
        for mid in session.material_ids:
            smap = await get_structural_map(container, mid)
            if smap.get("toc"):
                structural_maps.append(smap)

        if not structural_maps:
            logger.warning(
                "plan_step_1_no_structural_map job_id=%s — proceeding without "
                "TOC/concept-index context; plan will rely on retrieved "
                "chunk excerpts only (lower structure, not lower content)",
                job_id,
            )

        logger.info("plan_step_1_complete job_id=%s structural_maps=%d", job_id, len(structural_maps))

        # --- Step 2: Retrieve foundational chunks ---
        await _update_plan_job(conn, job_id, phase="FOUNDATIONAL_RETRIEVAL", steps_done=1)

        foundational_query = "prerequisites foundations introduction basics definitions notation"
        foundational_chunks = await retrieve_relevant_chunks(
            container, foundational_query, top_k=8,
        )
        logger.info(
            "plan_step_2_complete job_id=%s foundational_chunks=%d",
            job_id, len(foundational_chunks),
        )

        # Task 16: THIS is the real "was this material actually ingested"
        # check — chunk retrieval hitting the vector store, not the
        # structural-analysis side-channel. If neither structure nor any
        # retrievable content exists, there is genuinely nothing to build a
        # plan from, and failing here (with a message the GUI already
        # surfaces as "please try confirming your plan again") is correct —
        # unlike the old Step 1 check, retrying this one can actually
        # succeed once ingestion embedding finishes.
        if not structural_maps and not foundational_chunks:
            await _fail_plan_job(
                container, job_id,
                "No content retrieved for this material — it may not be "
                "ingested yet. Please try confirming your plan again.",
            )
            return

        # --- Step 3: LLM gap analysis ---
        await _update_plan_job(conn, job_id, phase="GAP_ANALYSIS", steps_done=2)

        gaps = await _analyze_knowledge_gaps(
            session, structural_maps, foundational_chunks, model_provider,
        )
        logger.info("plan_step_3_complete job_id=%s gaps=%d", job_id, len(gaps))

        # --- Step 4: Retrieve gap-specific chunks ---
        await _update_plan_job(conn, job_id, phase="GAP_RETRIEVAL", steps_done=3)

        gap_chunks: dict[str, list[dict]] = {}
        for gap in gaps:
            gap_concept = gap.get("concept", "")
            if gap_concept:
                chunks = await retrieve_relevant_chunks(
                    container, gap_concept, top_k=3,
                )
                gap_chunks[gap_concept] = chunks

        logger.info(
            "plan_step_4_complete job_id=%s gaps_with_chunks=%d",
            job_id, len(gap_chunks),
        )

        # --- Step 5: LLM phased plan design ---
        await _update_plan_job(conn, job_id, phase="PLAN_DESIGN", steps_done=4)

        phases = await _design_phased_plan(
            session, structural_maps, gaps, gap_chunks, model_provider,
        )
        logger.info("plan_step_5_complete job_id=%s phases=%d", job_id, len(phases))

        # --- Step 6: LLM concept detail per phase + ingest ---
        await _update_plan_job(conn, job_id, phase="CONCEPT_DETAIL", steps_done=5)

        all_concepts: list[dict] = []
        for phase in phases:
            phase_chunks: list[dict] = []
            for chunk_id in phase.get("chunk_ids", []):
                # Retrieve the chunk by ID (from the gap_chunks we already have)
                for gc in gap_chunks.values():
                    for c in gc:
                        if c.get("chunk_id") == chunk_id:
                            phase_chunks.append(c)
                            break

            concepts = await _generate_concept_details(
                phase, structural_maps, phase_chunks, model_provider,
            )
            all_concepts.extend(concepts)

        logger.info("plan_step_6_complete job_id=%s total_concepts=%d", job_id, len(all_concepts))

        # --- Store the plan + ingest concepts ---
        await _update_plan_job(conn, job_id, phase="STORING", steps_done=6)

        # Update the session's draft_plan with the generated concepts
        session.draft_plan = all_concepts

        # Use the existing IntakeActor.generate_plan to ingest + store
        from aip.foundation.protocols.actors import ActorContext
        import asyncio as _asyncio
        ctx = ActorContext(
            container=container,
            config=None,
            logger=logger,
            cancel_event=_asyncio.Event(),
        )
        actor = IntakeActor()
        plan_result = await actor.generate_plan(ctx, session)

        if plan_result.ok and plan_result.data:
            plan_id = plan_result.data.get("plan_id", "")
            session.plan_id = plan_id
            await _update_plan_job(
                conn, job_id,
                phase="COMPLETE",
                status="COMPLETE",
                plan_id=plan_id,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.info(
                "plan_generation_complete job_id=%s plan_id=%s concepts=%d",
                job_id, plan_id, len(all_concepts),
            )
        else:
            await _fail_plan_job(
                container, job_id,
                f"Plan storage failed: {plan_result.error}",
            )

    except Exception as exc:
        logger.error(
            "plan_generation_failed job_id=%s error=%s:%s",
            job_id, type(exc).__name__, exc, exc_info=True,
        )
        await _fail_plan_job(container, job_id, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Step 3: Knowledge gap analysis
# ---------------------------------------------------------------------------


async def _analyze_knowledge_gaps(
    session: Any,
    structural_maps: list[dict],
    foundational_chunks: list[dict],
    model_provider: Any,
) -> list[dict]:
    """LLM call: identify knowledge gaps given learner's background + paper structure."""
    # Build the user prompt
    parts = ["Analyze the learner's knowledge gaps against the paper's requirements.", ""]

    # Learner background
    parts.append("Learner's background:")
    parts.append(f"  Subject: {session.extracted.get('subject', 'unknown')}")
    parts.append(f"  Prior knowledge: {session.extracted.get('prior_knowledge', 'not specified')}")
    parts.append(f"  Goals: {session.extracted.get('goals', 'not specified')}")
    parts.append("")

    # Paper structure
    for smap in structural_maps:
        toc = smap.get("toc", [])
        concepts = smap.get("concepts", [])
        if toc:
            parts.append("Paper table of contents:")
            for entry in toc[:30]:
                parts.append(f"  {entry.get('chunk_index', '?')}. {entry.get('heading', '?')}")
        if concepts:
            parts.append(f"Paper key concepts: {', '.join(concepts[:40])}")
        parts.append("")

    # Foundational chunks
    if foundational_chunks:
        parts.append("Excerpts from foundational sections:")
        for i, chunk in enumerate(foundational_chunks[:5], 1):
            content = chunk.get("content", "")[:1500]
            parts.append(f"--- Excerpt {i} ---")
            parts.append(content)
            parts.append("")

    parts.append("Identify the knowledge gaps. Return JSON per the schema in your instructions.")

    user_prompt = "\n".join(parts)

    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": _GAP_ANALYSIS_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = result.get("content", "") if isinstance(result, dict) else ""
    except Exception as exc:
        logger.warning("gap_analysis_model_call_failed error=%s:%s", type(exc).__name__, exc)
        return []

    parsed = _parse_json_response(raw)
    if parsed is None:
        logger.warning("gap_analysis_non_json_response len=%d", len(raw))
        return []

    return parsed.get("gaps", [])


# ---------------------------------------------------------------------------
# Step 5: Phased plan design
# ---------------------------------------------------------------------------


async def _design_phased_plan(
    session: Any,
    structural_maps: list[dict],
    gaps: list[dict],
    gap_chunks: dict[str, list[dict]],
    model_provider: Any,
) -> list[dict]:
    """LLM call: design a phased plan bridging gaps to the paper."""
    parts = ["Design a phased learning plan for this learner.", ""]

    # Learner background
    parts.append("Learner's background:")
    parts.append(f"  Subject: {session.extracted.get('subject', 'unknown')}")
    parts.append(f"  Prior knowledge: {session.extracted.get('prior_knowledge', 'not specified')}")
    parts.append(f"  Goals: {session.extracted.get('goals', 'not specified')}")
    parts.append(f"  Schedule: {session.extracted.get('schedule_minutes', 30)} min/day")
    parts.append("")

    # Paper structure
    for smap in structural_maps:
        toc = smap.get("toc", [])
        if toc:
            parts.append("Paper table of contents:")
            for entry in toc[:30]:
                parts.append(f"  {entry.get('chunk_index', '?')}. {entry.get('heading', '?')}")
        parts.append("")

    # Knowledge gaps
    if gaps:
        parts.append("Identified knowledge gaps:")
        for i, gap in enumerate(gaps, 1):
            parts.append(
                f"  {i}. {gap.get('concept', '?')} "
                f"(severity: {gap.get('severity', '?')}, "
                f"~{gap.get('estimated_study_time_hours', '?')}h) — "
                f"{gap.get('reason', '')}"
            )
            paper_secs = gap.get("paper_sections", [])
            if paper_secs:
                parts.append(f"     Required by: {', '.join(paper_secs)}")
        parts.append("")

    # Gap-specific chunks
    if gap_chunks:
        parts.append("Excerpts relevant to each gap:")
        for gap_concept, chunks in gap_chunks.items():
            parts.append(f"--- Gap: {gap_concept} ---")
            for chunk in chunks[:2]:
                content = chunk.get("content", "")[:1000]
                chunk_id = chunk.get("chunk_id", "")
                parts.append(f"  [chunk_id={chunk_id}]")
                parts.append(f"  {content}")
            parts.append("")

    parts.append("Design the phased plan. Return JSON per the schema in your instructions.")

    user_prompt = "\n".join(parts)

    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": _PHASED_PLAN_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = result.get("content", "") if isinstance(result, dict) else ""
    except Exception as exc:
        logger.warning("plan_design_model_call_failed error=%s:%s", type(exc).__name__, exc)
        return []

    parsed = _parse_json_response(raw)
    if parsed is None:
        logger.warning("plan_design_non_json_response len=%d", len(raw))
        return []

    return parsed.get("phases", [])


# ---------------------------------------------------------------------------
# Step 6: Concept detail per phase
# ---------------------------------------------------------------------------


async def _generate_concept_details(
    phase: dict,
    structural_maps: list[dict],
    phase_chunks: list[dict],
    model_provider: Any,
) -> list[dict]:
    """LLM call: produce detailed concepts for a phase."""
    parts = [f"Generate detailed learning concepts for this phase.", ""]

    parts.append(f"Phase {phase.get('phase_number', '?')}: {phase.get('phase_name', '?')}")
    parts.append(f"Goal: {phase.get('goal', '')}")
    parts.append(f"Concepts: {', '.join(phase.get('concepts', []))}")
    paper_secs = phase.get("paper_sections", [])
    if paper_secs:
        parts.append(f"Paper sections: {', '.join(paper_secs)}")
    parts.append("")

    # Paper structure (for context)
    for smap in structural_maps:
        toc = smap.get("toc", [])
        if toc:
            parts.append("Paper TOC (for reference):")
            for entry in toc[:20]:
                parts.append(f"  {entry.get('chunk_index', '?')}. {entry.get('heading', '?')}")
        parts.append("")

    # Phase-specific chunks
    if phase_chunks:
        parts.append("Excerpts relevant to this phase:")
        for i, chunk in enumerate(phase_chunks[:4], 1):
            content = chunk.get("content", "")[:1200]
            chunk_id = chunk.get("chunk_id", "")
            parts.append(f"--- Excerpt {i} [chunk_id={chunk_id}] ---")
            parts.append(content)
        parts.append("")

    parts.append("Generate the concepts. Return JSON per the schema in your instructions.")

    user_prompt = "\n".join(parts)

    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": _CONCEPT_DETAIL_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = result.get("content", "") if isinstance(result, dict) else ""
    except Exception as exc:
        logger.warning("concept_detail_model_call_failed error=%s:%s", type(exc).__name__, exc)
        return []

    parsed = _parse_json_response(raw)
    if parsed is None:
        logger.warning("concept_detail_non_json_response len=%d", len(raw))
        return []

    return parsed.get("concepts", [])


# ---------------------------------------------------------------------------
# Job creation + status helpers
# ---------------------------------------------------------------------------


async def create_plan_job(
    container: Any,
    session: Any,
    material_id: str | None = None,
) -> str:
    """Create a new plan job row + return the job_id. Does NOT start the job."""
    from aristotle.actors.intake import intake_session_to_dict

    job_id = str(uuid.uuid4())
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        raise RuntimeError("corpus_registry not available")
    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn
    session_json = json.dumps(intake_session_to_dict(session))
    await _create_plan_job(conn, job_id, session_json, material_id)
    return job_id


async def get_plan_job_status(container: Any, job_id: str) -> dict | None:
    """Get the status of a plan generation job."""
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return None
    stores = await registry.get_stores("aristotle:textbook")
    conn = stores.connection_manager.write_conn
    cur = await conn.execute(
        "SELECT job_id, plan_id, material_id, phase, status, "
        "steps_total, steps_done, error, started_at, updated_at, completed_at "
        "FROM aristotle_plan_job WHERE job_id = ?",
        (job_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return {
        "job_id": row[0],
        "plan_id": row[1],
        "material_id": row[2],
        "phase": row[3],
        "status": row[4],
        "steps_total": row[5],
        "steps_done": row[6],
        "error": row[7],
        "started_at": row[8],
        "updated_at": row[9],
        "completed_at": row[10],
    }


async def _fail_plan_job(container: Any, job_id: str, error: str) -> None:
    """Mark a plan job as failed."""
    try:
        registry = getattr(container, "corpus_registry", None)
        if registry is None:
            return
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        await _update_plan_job(
            conn, job_id,
            phase="FAILED",
            status="FAILED",
            error=error,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.error("fail_plan_job_update_failed job_id=%s error=%s", job_id, exc)


# ---------------------------------------------------------------------------
# JSON parsing helper (same as intake.py)
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict | None:
    """Extract the first JSON object from a model response."""
    if not raw:
        return None
    raw = raw.strip()

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    import re

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    brace_match = re.search(r"\{.*\}", raw, re.S)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None
