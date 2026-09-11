from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from qdrant_client import QdrantClient, models

from app.agent.nodes import _rerank
from app.agent.retrieval import evidence_package, expand_parents, required_sources
from app.models import EvidenceItem, RoutePlan
from app.stores.qdrant import QdrantEvidenceStore, content_hash
from tests.test_stores import RecordingEmbeddings, RecordingSparse


def item(key, service=None, **extra):
    return EvidenceItem(
        chunk_id=key,
        source_id=key,
        citation_id=f"[{key}]",
        source_path=key,
        source_type="document",
        text=key,
        content_hash=content_hash(key),
        service=service,
        **extra,
    )


@pytest.fixture
def vector_store():
    store = object.__new__(QdrantEvidenceStore)
    store.collection = "test"
    store.settings = SimpleNamespace(LANGFUSE_ENABLED=False, OPENAI_EMBEDDING_MODEL="test")
    store.client = QdrantClient(":memory:")
    store.client.create_collection(
        "test",
        vectors_config={"dense": models.VectorParams(size=2, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams()},
    )
    store.embeddings = RecordingEmbeddings()
    store.embeddings.embed_query = lambda query: [0.1, 0.2]
    store.sparse = RecordingSparse()
    store.sparse.query_embed = lambda query: iter(store.sparse.embed([query]))
    yield store
    store.client.close()


def test_shared_policy_and_selected_services_survive_hybrid_filter(vector_store):
    vector_store.upsert(
        [
            item("payment", "PaymentService"),
            item("shared"),
            item("login", "AuthenticationService"),
        ]
    )
    found = vector_store.search("policy", services=["PaymentService"], include_shared=True)
    assert {entry.source_id for entry in found} == {"payment", "shared"}
    assert vector_store.get_source_items("login")[0].service == "AuthenticationService"


def test_obsolete_chunks_are_deleted_only_within_their_source(vector_store):
    first = item("first")
    stale = item("stale").model_copy(update={"source_id": "first"})
    other = item("other")
    vector_store.upsert([first, stale, other])
    vector_store.delete_obsolete("first", ["first"])
    assert [entry.chunk_id for entry in vector_store.get_source_items("first")] == ["first"]
    assert vector_store.get_source_items("other")


def test_metadata_refresh_reuses_vectors_but_model_change_reembeds(vector_store):
    original = item("policy")
    vector_store.upsert([original])
    changed = original.model_copy(update={"parent_id": "parent", "parent_text": "full context"})
    assert vector_store.upsert([changed]) == 0
    assert vector_store.get_source_items("policy")[0].parent_text == "full context"
    vector_store.settings.OPENAI_EMBEDDING_MODEL = "new-model"
    assert vector_store.upsert([changed]) == 1
    assert len(vector_store.embeddings.calls) == 2


def test_required_policy_is_protected_and_parent_context_is_deduplicated():
    children = [
        item(key, parent_id="parent", parent_text="Policy and its exception.").model_copy(
            update={"source_id": "policy"}
        )
        for key in ("child1", "child2")
    ]
    assert len(expand_parents(children)) == 1
    selected, overflow = evidence_package(children, [item("optional")])
    assert not overflow
    assert selected[0].chunk_id == "parent"
    assert "exception" in selected[0].text
    selected, overflow = evidence_package(
        [item("huge").model_copy(update={"text": "x" * 32001})], []
    )
    assert overflow and selected == []


def test_reranker_preserves_model_order_and_drops_unknown_ids():
    model = Mock()
    model.with_structured_output.return_value.invoke.return_value = SimpleNamespace(
        chunk_ids=["item8", "unknown", "item2", "item8"]
    )
    result = _rerank([item(f"item{i}") for i in range(10)], "query", model)
    assert [entry.chunk_id for entry in result] == ["item8", "item2"]


def test_router_cannot_remove_original_category_policies():
    state = {
        "ticket": {"category": "SECURITY"},
        "route": RoutePlan(category="PAYMENT", query="payment"),
    }
    assert "account_security_policy" in required_sources(state)
    assert "escalation_policy" in required_sources(state)
