"""Aristotle content ingestor — ADR-001 §4.

Populates the `aristotle_concept` table with concept-aware chunks. For
Phase A dogfood, the ingestor takes a YAML file with pre-defined concepts
(authored manually by the teacher/DEFINER). A future AI-chunking ingestor
will analyze a textbook automatically — that's a Phase A+ enhancement.

The ingestor is called by a session coordinator or CLI command, not by
the actor scheduler. It's a standalone function that takes the container
+ a YAML path and inserts concepts into the corpus.

Layer: imports from aip.foundation.protocols.actors only (ActorContext)
for type hints. The container is accessed via ctx.container (duck-typed).
No aip.adapter or aip.orchestration imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aip.foundation.protocols.actors import ActorContext


async def ingest_concepts_from_yaml(
    ctx: ActorContext,
    yaml_path: str | Path,
) -> dict:
    """Ingest concepts from a YAML file into the aristotle_concept table.

    The YAML format (one concept per list entry):
        - id: "concept_001"
          textbook_chapter: "Chapter 1: Introduction"
          topic: "Newton's First Law"
          subtopic: "Inertia"
          bloom_target: 3
          content_primary: |
            Newton's First Law states that an object at rest stays at rest...
          content_alt: |
            نیورٹن کا پہلا قانون کہتا ہے...
          content_alt_lang: "ur"
          prerequisite_concept_id: null

    Args:
        ctx: ActorContext with container (for corpus_registry access).
        yaml_path: path to the YAML file.

    Returns:
        dict with {ingested: int, skipped: int, errors: list[str]}.
    """
    logger = ctx.logger
    container: Any = ctx.container
    path = Path(yaml_path)

    if not path.exists():
        return {"ingested": 0, "skipped": 0, "errors": [f"file not found: {path}"]}

    # Load the YAML
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        return {"ingested": 0, "skipped": 0, "errors": [f"YAML parse error: {exc}"]}

    concepts = data if isinstance(data, list) else data.get("concepts", [])
    if not concepts:
        return {"ingested": 0, "skipped": 0, "errors": ["no concepts found in YAML"]}

    # Get the corpus stores
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return {
            "ingested": 0,
            "skipped": 0,
            "errors": ["corpus_registry not available"],
        }

    try:
        stores = await registry.get_stores("aristotle:textbook")
    except Exception as exc:
        return {"ingested": 0, "skipped": 0, "errors": [f"corpus access failed: {exc}"]}

    conn = stores.connection_manager.write_conn

    ingested = 0
    skipped = 0
    errors: list[str] = []

    for i, concept in enumerate(concepts):
        if not isinstance(concept, dict):
            errors.append(f"entry {i}: not a dict, skipping")
            skipped += 1
            continue

        concept_id = concept.get("id")
        if not concept_id:
            errors.append(f"entry {i}: missing 'id', skipping")
            skipped += 1
            continue

        topic = concept.get("topic")
        if not topic:
            errors.append(f"entry {i} (id={concept_id}): missing 'topic', skipping")
            skipped += 1
            continue

        content_primary = concept.get("content_primary", "")
        if not content_primary:
            errors.append(
                f"entry {i} (id={concept_id}): missing 'content_primary', skipping"
            )
            skipped += 1
            continue

        try:
            await conn.execute(
                """
                INSERT OR REPLACE INTO aristotle_concept
                (id, textbook_chapter, topic, subtopic, bloom_target,
                 content_primary, content_alt, content_alt_lang,
                 prerequisite_concept_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    concept_id,
                    concept.get("textbook_chapter", ""),
                    topic,
                    concept.get("subtopic"),
                    concept.get("bloom_target", 3),
                    content_primary,
                    concept.get("content_alt"),
                    concept.get("content_alt_lang"),
                    concept.get("prerequisite_concept_id"),
                ),
            )
            ingested += 1
        except Exception as exc:
            errors.append(f"entry {i} (id={concept_id}): insert failed: {exc}")
            skipped += 1

    await conn.commit()

    logger.info(
        "aristotle_ingest_complete ingested=%d skipped=%d errors=%d",
        ingested,
        skipped,
        len(errors),
    )

    return {"ingested": ingested, "skipped": skipped, "errors": errors}


async def list_concepts(ctx: ActorContext) -> list[dict]:
    """List all concepts in the aristotle_concept table.

    Returns a list of dicts with: id, topic, subtopic, bloom_target,
    prerequisite_concept_id. Useful for the session coordinator to
    determine the next concept to teach.
    """
    container: Any = ctx.container
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return []

    try:
        stores = await registry.get_stores("aristotle:textbook")
        conn = stores.connection_manager.write_conn
        cur = await conn.execute(
            "SELECT id, topic, subtopic, bloom_target, prerequisite_concept_id "
            "FROM aristotle_concept ORDER BY id"
        )
        rows = await cur.fetchall()
        await cur.close()
        return [
            {
                "id": row[0],
                "topic": row[1],
                "subtopic": row[2],
                "bloom_target": row[3],
                "prerequisite_concept_id": row[4],
            }
            for row in rows
        ]
    except Exception:
        return []
