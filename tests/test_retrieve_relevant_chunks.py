"""Tests for Task 17: material_id scoping in retrieve_relevant_chunks.

"aristotle:textbook" is a single shared vector-store domain across every
material ever ingested by every student — there is no per-material
partition at the vector-store or aristotle_concept-table level. Without a
material_id filter, retrieval for one learner's paper can surface chunks
from a completely different paper someone else (or a past dogfood/test
session) ingested earlier.

Reproduced in production: a pharmacy student's plan pulled physics chunks
(tangent spaces, field operators/spin, Quantum Darwinism) into freshly
generated "pharmacognosy_NNN" concepts, because gap analysis and
concept-detail generation retrieved from the whole shared corpus, not
just this student's uploaded textbook.

Run: pytest tests/test_retrieve_relevant_chunks.py -v
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aristotle.ingestion.paper_ingestor import retrieve_relevant_chunks


@dataclass
class _FakeChunk:
    """Mirrors aip.foundation.schemas.retrieval.Chunk."""

    id: str
    content: str | None = None
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


class _FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    """Returns a fixed, mixed-material set of chunks regardless of query —
    mirrors the real store's behavior of not filtering by material_id.
    """

    def __init__(self, chunks: list[_FakeChunk]):
        self._chunks = chunks
        self.last_top_k: int | None = None

    async def retrieve(self, query_vector, domain=None, top_k=10):
        self.last_top_k = top_k
        return self._chunks[:top_k]


def _make_container(vector_store: Any) -> Any:
    return type(
        "C",
        (),
        {
            "embedding_provider": _FakeEmbeddingProvider(),
            "vector_store": vector_store,
        },
    )()


# A mixed bag: 3 chunks from the pharmacy material, 2 from an old physics
# material that happens to live in the same shared "aristotle:textbook"
# domain (e.g. a past NBCM/dogfood ingest on the same machine).
_MIXED_CHUNKS = [
    _FakeChunk(id="p1", content="Pharmacognosy studies crude drugs.", score=0.95,
               metadata={"material_id": "pharm-material"}),
    _FakeChunk(id="phys1", content="Tangent spaces are collections of directional derivatives.", score=0.93,
               metadata={"material_id": "old-physics-material"}),
    _FakeChunk(id="p2", content="Organoleptic evaluation uses sensory inspection.", score=0.90,
               metadata={"material_id": "pharm-material"}),
    _FakeChunk(id="phys2", content="Quantum Darwinism explains classical objectivity.", score=0.88,
               metadata={"material_id": "old-physics-material"}),
    _FakeChunk(id="p3", content="Extraction techniques include maceration and percolation.", score=0.85,
               metadata={"material_id": "pharm-material"}),
]


class TestMaterialIdScoping:
    @pytest.mark.asyncio
    async def test_no_material_ids_returns_unfiltered_top_k(self):
        """Backwards compatible: material_ids=None (default) behaves exactly
        as before — no filtering, no over-fetch."""
        store = _FakeVectorStore(_MIXED_CHUNKS)
        container = _make_container(store)

        results = await retrieve_relevant_chunks(container, "some query", top_k=3)

        assert len(results) == 3
        assert store.last_top_k == 3  # no over-fetch when not filtering
        # Unfiltered — includes whatever's first, physics or not.
        assert results[0]["chunk_id"] == "p1"
        assert results[1]["chunk_id"] == "phys1"

    @pytest.mark.asyncio
    async def test_material_ids_filters_out_other_materials(self):
        """The actual fix: passing material_ids excludes chunks tagged
        with a different material_id, even though the underlying vector
        store has no native filter and returns everything mixed together.
        """
        store = _FakeVectorStore(_MIXED_CHUNKS)
        container = _make_container(store)

        results = await retrieve_relevant_chunks(
            container, "prerequisites foundations introduction", top_k=3,
            material_ids=["pharm-material"],
        )

        assert len(results) == 3
        chunk_ids = {r["chunk_id"] for r in results}
        assert chunk_ids == {"p1", "p2", "p3"}
        # No physics chunk should ever appear, regardless of its score.
        assert "phys1" not in chunk_ids
        assert "phys2" not in chunk_ids

    @pytest.mark.asyncio
    async def test_material_ids_over_fetches_to_survive_filtering(self):
        """When filtering, the store is asked for more than top_k so that
        enough same-material chunks survive the client-side filter —
        without this, a query where physics chunks score higher than
        pharmacy chunks could starve the result set down to zero.
        """
        store = _FakeVectorStore(_MIXED_CHUNKS)
        container = _make_container(store)

        await retrieve_relevant_chunks(
            container, "some query", top_k=3, material_ids=["pharm-material"],
        )

        # top_k=3 with filtering active should over-fetch (min(3*5, 50) = 15,
        # capped by the fake store's list length of 5 chunks returned).
        assert store.last_top_k == 15

    @pytest.mark.asyncio
    async def test_material_ids_still_caps_at_top_k(self):
        """Even with plenty of matching chunks available, the result is
        still truncated to top_k after filtering."""
        many_pharm_chunks = [
            _FakeChunk(id=f"p{i}", content=f"chunk {i}", score=1.0 - i * 0.01,
                       metadata={"material_id": "pharm-material"})
            for i in range(20)
        ]
        store = _FakeVectorStore(many_pharm_chunks)
        container = _make_container(store)

        results = await retrieve_relevant_chunks(
            container, "some query", top_k=3, material_ids=["pharm-material"],
        )

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_empty_material_ids_list_behaves_like_none(self):
        """An empty list (e.g. session.material_ids not yet populated)
        should not trigger filtering/over-fetch machinery — falsy check,
        same as material_ids=None."""
        store = _FakeVectorStore(_MIXED_CHUNKS)
        container = _make_container(store)

        results = await retrieve_relevant_chunks(
            container, "some query", top_k=3, material_ids=[],
        )

        assert len(results) == 3
        assert store.last_top_k == 3

    @pytest.mark.asyncio
    async def test_chunks_missing_material_id_metadata_are_excluded(self):
        """Defense in depth: a chunk with no material_id in its metadata
        (e.g. ingested before this field existed) is treated as
        non-matching when filtering is active, not silently included."""
        chunks = _MIXED_CHUNKS + [
            _FakeChunk(id="legacy1", content="no metadata tag", score=0.99, metadata={}),
        ]
        store = _FakeVectorStore(chunks)
        container = _make_container(store)

        results = await retrieve_relevant_chunks(
            container, "some query", top_k=10, material_ids=["pharm-material"],
        )

        chunk_ids = {r["chunk_id"] for r in results}
        assert "legacy1" not in chunk_ids
