from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import EvidenceItem
from app.stores.neo4j import validate_schema
from app.stores.qdrant import QdrantEvidenceStore, content_hash, evidence_payload, point_id


def test_qdrant_payload_contains_required_provenance() -> None:
    text = "Captured payment requires reconciliation."
    item = EvidenceItem(
        citation_id="[payment_policy.pdf, p.1]",
        chunk_id="payment:p1:c1",
        source_id="payment_policy",
        source_path="documents/payment_policy.pdf",
        source_type="document",
        page=1,
        section="Captured payment",
        text=text,
        entity_ids=["PaymentService"],
        ticket_category="PAYMENT",
        service="PaymentService",
        content_hash=content_hash(text),
    )
    payload = evidence_payload(item)
    assert (
        set(
            [
                "chunk_id",
                "source_id",
                "source_path",
                "source_type",
                "page",
                "section",
                "entity_ids",
                "ticket_category",
                "service",
                "content_hash",
            ]
        )
        <= payload.keys()
    )
    assert point_id(item.chunk_id) == point_id(item.chunk_id)


def test_neo4j_schema_allowlist_rejects_unknown_values() -> None:
    validate_schema("Ticket", "CREATED")
    with pytest.raises(ValueError, match="label"):
        validate_schema("Anything")
    with pytest.raises(ValueError, match="relationship"):
        validate_schema("Ticket", "RUN_ARBITRARY_CYPHER")


class Collection:
    def __init__(self) -> None:
        self.rows = {}

    def replace_one(self, query, record, upsert=False) -> None:
        assert upsert
        self.rows[next(iter(query.values()))] = record


def test_mongo_upserts_are_idempotent() -> None:
    from app.stores.mongo import MongoStore

    store = object.__new__(MongoStore)
    collection = Collection()
    store.db = {"customers": collection}
    rows = [{"customer_id": "CUST-001", "name": "A"}]
    assert store.upsert_many("customers", "customer_id", rows) == 1
    assert store.upsert_many("customers", "customer_id", rows) == 1
    assert len(collection.rows) == 1


class VectorValues(list):
    def tolist(self) -> list:
        return list(self)


class RecordingEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2] for _ in texts]


class RecordingSparse:
    def embed(self, texts: list[str]):
        return [
            SimpleNamespace(indices=VectorValues([1]), values=VectorValues([1.0])) for _ in texts
        ]


class IncrementalClient:
    def __init__(self, existing: EvidenceItem) -> None:
        self.existing = existing
        self.upserted = []

    def retrieve(self, **_kwargs):
        return [
            SimpleNamespace(
                id=point_id(self.existing.chunk_id),
                payload={**evidence_payload(self.existing), "embedding_model": "test-embedding"},
            )
        ]

    def upsert(self, _collection: str, points: list, wait: bool) -> None:
        assert wait
        self.upserted.extend(points)


def test_qdrant_does_not_regenerate_unchanged_embeddings() -> None:
    unchanged = EvidenceItem(
        citation_id="[payment_policy.pdf, p.1]",
        chunk_id="payment:p1:c1",
        source_id="payment_policy",
        source_path="documents/payment_policy.pdf",
        source_type="document",
        page=1,
        text="Captured payment requires reconciliation.",
        content_hash=content_hash("Captured payment requires reconciliation."),
    )
    changed = unchanged.model_copy(
        update={
            "chunk_id": "payment:p1:c2",
            "text": "A new evidence chunk.",
            "content_hash": content_hash("A new evidence chunk."),
        }
    )
    embeddings = RecordingEmbeddings()
    client = IncrementalClient(unchanged)
    store = object.__new__(QdrantEvidenceStore)
    store.collection = "supportgraph_evidence"
    store.client = client
    store.embeddings = embeddings
    store.sparse = RecordingSparse()
    store.settings = SimpleNamespace(
        LANGFUSE_ENABLED=False, OPENAI_EMBEDDING_MODEL="test-embedding"
    )

    assert store.upsert([unchanged, changed]) == 1
    assert embeddings.calls == [[changed.text]]
    assert len(client.upserted) == 1


def test_memory_requires_explicit_approval_and_deduplicates_review():
    from unittest.mock import Mock

    from app.stores.memory import MemoryStore

    store = object.__new__(MemoryStore)
    store.memory = Mock()
    rows = [
        {"id": "unknown", "memory": "Missing review metadata"},
        {"id": "rejected", "metadata": {"approved": False}},
        {"id": "approved", "metadata": {"approved": True, "review_id": "REV-1"}},
    ]
    store.memory.get_all.return_value = {"results": rows}
    store.memory.search.return_value = {"results": rows}
    assert [row["id"] for row in store.list_customer("C")] == ["approved"]
    assert [row["id"] for row in store.search("query", "C", "PAYMENT")] == ["approved"]
    store.add_approved("Saved recommendation", "C", "PAYMENT", "T", review_id="REV-1")
    store.memory.add.assert_not_called()


def test_review_write_uses_atomic_unreviewed_status_condition():
    from unittest.mock import Mock

    from pymongo import ReturnDocument

    from app.stores.mongo import MongoStore

    store = object.__new__(MongoStore)
    store.db = Mock()
    store.db.investigation_runs.find_one_and_update.return_value = None
    assert (
        store.record_review("R", {"review_id": "V"}, "READY_FOR_REVIEW", "APPROVED", "draft", True)
        is None
    )
    args, kwargs = store.db.investigation_runs.find_one_and_update.call_args
    assert args[0] == {"run_id": "R", "status": "READY_FOR_REVIEW", "review": {"$exists": False}}
    assert args[1]["$set"]["memory_status"] == "PENDING"
    assert kwargs["return_document"] == ReturnDocument.AFTER
