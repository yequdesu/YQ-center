from yequ.models.ycr import YcrCapabilityIndex
from yequ.ycr import capability_gateway
from yequ.ycr.embedding import RerankItem, YcrEmbedding
from yequ.ycr.rag_cache import (
    cached_query_embedding,
    cached_rerank,
    cached_retrieval_candidates,
)


async def test_query_embedding_cache_reuses_persistent_record(db_session) -> None:
    calls = 0

    async def compute(query: str) -> YcrEmbedding:
        nonlocal calls
        calls += 1
        return YcrEmbedding(
            dense=[1.0, 0.0],
            sparse={"screen": 1.0},
            provider="test",
            model="test-embedding",
        )

    first = await cached_query_embedding(db_session, " Screen   Capture ", compute=compute)
    second = await cached_query_embedding(db_session, "screen capture", compute=compute)

    assert calls == 1
    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.embedding.sparse == {"screen": 1.0}


async def test_rerank_cache_reuses_persistent_record(db_session) -> None:
    calls = 0
    documents = ["capability screen capture", "capability file transfer"]
    hashes = ["doc_screen", "doc_transfer"]

    async def compute(query: str, docs: list[str], top_n: int) -> list[RerankItem]:
        nonlocal calls
        calls += 1
        assert docs == documents
        assert top_n == 1
        return [RerankItem(index=0, score=0.98)]

    first = await cached_rerank(
        db_session,
        "screenshot",
        documents,
        document_hashes=hashes,
        top_n=1,
        compute=compute,
    )
    second = await cached_rerank(
        db_session,
        "screenshot",
        documents,
        document_hashes=hashes,
        top_n=1,
        compute=compute,
    )

    assert calls == 1
    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.items[0].index == 0
    assert second.items[0].score == 0.98


async def test_retrieval_candidate_cache_reuses_persistent_record(db_session) -> None:
    calls = 0

    async def compute() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [
            {
                "canonical_name": "screen.capture",
                "rrf_score": 0.12,
                "ranks": {"dense_rank": 1, "sparse_rank": 1},
                "dense_score": 0.9,
                "sparse_score": 1.0,
            }
        ]

    first = await cached_retrieval_candidates(
        db_session,
        normalized_query_hash="query_hash",
        query_embedding_hash="embedding_hash",
        registry_version="registry_v1",
        filters_hash="filters_hash",
        top_k=50,
        compute=compute,
    )
    second = await cached_retrieval_candidates(
        db_session,
        normalized_query_hash="query_hash",
        query_embedding_hash="embedding_hash",
        registry_version="registry_v1",
        filters_hash="filters_hash",
        top_k=50,
        compute=compute,
    )

    assert calls == 1
    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.registry_version == "registry_v1"
    assert second.rows[0]["canonical_name"] == "screen.capture"


async def test_capability_ready_index_load_does_not_rebuild_document(
    db_session,
    monkeypatch,
) -> None:
    candidate = {
        "capability_id": "cap_screen",
        "canonical_name": "screen.capture",
        "capability_type": "function",
    }
    index_id = capability_gateway._index_id(candidate)
    db_session.add(
        YcrCapabilityIndex(
            index_id=index_id,
            capability_id="cap_screen",
            canonical_name="screen.capture",
            capability_type="function",
            index_version=capability_gateway.CAPABILITY_INDEX_VERSION,
            document_hash="doc_hash",
            document_json={"identity": "screen.capture"},
            index_text="identity: screen capture",
            embedding_json=[1.0, 0.0],
            embedding_provider="test",
            embedding_model="test-embedding",
            sparse_json={"screen": 1.0},
        )
    )
    await db_session.flush()

    def fail_document_builder(_candidate):
        raise AssertionError("search hot path must not rebuild capability index document")

    monkeypatch.setattr(capability_gateway, "_capability_index_document", fail_document_builder)

    indexes = await capability_gateway._load_ready_capability_indexes(db_session, [candidate])

    assert [item.canonical_name for item in indexes] == ["screen.capture"]
