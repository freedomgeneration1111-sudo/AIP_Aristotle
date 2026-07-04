"""Structural analysis — LLM calls to extract paper structure.

Called by paper_ingestor.py during the ANALYZE phase. Produces:
  - TOC (table of contents) map
  - Concept index (which chunks cover which concepts)
  - Prerequisite graph (which sections depend on which)
  - Citations list (parsed from references)

Uses the "beast" model slot (same as IntakeActor) — the analysis calls
are conversational enough that a strong general-purpose model works.
Future: a dedicated "aristotle_analysis" slot could use a stronger
model (e.g., claude-3.5-sonnet) for better reasoning.

Layer: imports from aip.foundation.protocols.actors (ActorContext) +
aristotle's own modules. No aip.orchestration imports.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


_STRUCTURE_ANALYSIS_PROMPT = """You are analyzing the structure of an academic paper to build a learning curriculum from it. You will be given the paper's chunks (sections + their text). For each chunk, extract:

1. concept_tags: the key concepts this chunk covers (e.g., ["null surface", "Lorentzian manifold", "constraint algebra"])
2. prereq_tags: prerequisite concepts the reader needs to understand this chunk (e.g., ["differential geometry", "tensor calculus"])
3. citations: any citations referenced in this chunk (e.g., [{"key": "[1]", "raw_text": "Jorgensen 2026", "type": "unknown"}])

You MUST return valid JSON with this schema:
{
  "chunk_analyses": [
    {
      "chunk_id": "<the chunk_id from the input>",
      "concept_tags": ["concept1", "concept2"],
      "prereq_tags": ["prereq1", "prereq2"],
      "citations": [{"key": "[1]", "raw_text": "...", "type": "arxiv|doi|url|book|unknown"}]
    }
  ],
  "citations": [
    {"key": "[1]", "raw_text": "full citation text", "type": "arxiv|doi|url|book|unknown", "resolved_id": "arxiv id or doi or url if parsed"}
  ]
}

Rules:
- concept_tags: 1-5 tags per chunk. Use specific, searchable terms (not generic words like "math").
- prereq_tags: concepts the reader must already understand to follow this chunk. Include both math prerequisites (e.g., "linear algebra") and physics prerequisites (e.g., "special relativity").
- citations: parse any citation references in the chunk text. type is one of: arxiv (e.g., "arXiv:2301.12345"), doi (e.g., "10.1234/..."), url (e.g., "https://..."), book, unknown.
- The top-level "citations" array should contain the FULL citation text from the references/bibliography section if present, with resolved_id parsed where possible.

If a chunk has no clear concepts or prerequisites, return empty arrays for that chunk. Do not fabricate."""


async def analyze_paper_structure(
    material_id: str,
    filename: str,
    chunks: list[dict],
    container: Any,
) -> dict:
    """Run structural analysis on the paper's chunks via LLM.

    Returns: {
        concept_map: {chunk_id: [concept_tags]},
        prereq_map: {chunk_id: [prereq_tags]},
        citation_map: {chunk_id: [citation_ids]},
        citations: [{key, raw_text, type, resolved_id}],
        toc: [{heading, chunk_index}],
    }
    """
    model_provider = getattr(container, "model_provider", None)
    if model_provider is None:
        logger.warning("structural_analysis_no_model — skipping")
        return _empty_structure(chunks)

    # Build the input prompt — list all chunks with their IDs + text
    # Truncate each chunk to 1000 chars to keep the prompt manageable
    # (we already have the full text in the vector store for RAG retrieval)
    parts = [f"Paper: {filename}", f"Chunks: {len(chunks)}", ""]
    for chunk in chunks:
        text_preview = chunk["text"][:1000]
        if len(chunk["text"]) > 1000:
            text_preview += "..."
        parts.append(f"--- Chunk {chunk['chunk_index']} (id: {chunk['chunk_id']}) ---")
        parts.append(f"Heading: {chunk['heading'] or '(none)'}")
        parts.append(f"Section: {chunk['section_path'] or '(none)'}")
        parts.append(f"Text: {text_preview}")
        parts.append("")

    user_prompt = "\n".join(parts)

    # Single LLM call for the whole paper (works for papers up to ~30 chunks)
    # For very large textbooks, this would need to be batched by section
    try:
        result = await model_provider.call(
            slot_name="beast",
            messages=[
                {"role": "system", "content": _STRUCTURE_ANALYSIS_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = result.get("content", "") if isinstance(result, dict) else ""
    except Exception as exc:
        logger.warning("structural_analysis_model_call_failed error=%s:%s", type(exc).__name__, exc)
        return _empty_structure(chunks)

    # Parse the JSON response
    parsed = _parse_json_response(raw)
    if parsed is None:
        logger.warning("structural_analysis_non_json_response len=%d", len(raw))
        return _empty_structure(chunks)

    # Build the maps from the parsed response
    concept_map: dict[str, list[str]] = {}
    prereq_map: dict[str, list[str]] = {}
    citation_map: dict[str, list[str]] = {}

    for ca in parsed.get("chunk_analyses", []):
        chunk_id = ca.get("chunk_id", "")
        if chunk_id:
            concept_map[chunk_id] = ca.get("concept_tags", [])
            prereq_map[chunk_id] = ca.get("prereq_tags", [])
            citation_map[chunk_id] = [c.get("key", "") for c in ca.get("citations", []) if c.get("key")]

    # Build TOC from the chunks (not the LLM — this is deterministic)
    toc = [
        {"heading": c["section_path"] or c["heading"], "chunk_index": c["chunk_index"]}
        for c in chunks
    ]

    # Citations from the top-level array
    citations = parsed.get("citations", [])

    logger.info(
        "structural_analysis_complete material_id=%s chunks=%d concepts=%d citations=%d",
        material_id, len(chunks), sum(len(v) for v in concept_map.values()), len(citations),
    )

    return {
        "concept_map": concept_map,
        "prereq_map": prereq_map,
        "citation_map": citation_map,
        "citations": citations,
        "toc": toc,
    }


def _empty_structure(chunks: list[dict]) -> dict:
    """Return an empty structure (used when LLM analysis fails)."""
    return {
        "concept_map": {},
        "prereq_map": {},
        "citation_map": {},
        "citations": [],
        "toc": [
            {"heading": c["section_path"] or c["heading"], "chunk_index": c["chunk_index"]}
            for c in chunks
        ],
    }


def _parse_json_response(raw: str) -> dict | None:
    """Extract the first JSON object from a model response.

    Handles: pure JSON, JSON wrapped in ```json fences, JSON with
    preamble text before it. Returns None if no valid JSON found.
    """
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
