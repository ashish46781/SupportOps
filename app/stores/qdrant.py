from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from typing import Any

from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient, models

from app.config import Settings
from app.models import EvidenceItem
from app.observability import get_langfuse


def evidence_payload(item: EvidenceItem) -> dict[str, Any]:
    return item.model_dump(exclude={"score"})


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"supportgraph:{chunk_id}"))


class QdrantEvidenceStore:
    def __init__(
        self, settings: Settings, embeddings: Any, client: QdrantClient | None = None
    ) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self.client = client or QdrantClient(url=settings.QDRANT_URL, timeout=10)
        self.sparse = SparseTextEmbedding(model_name="Qdrant/bm25")
        self.collection = settings.QDRANT_EVIDENCE_COLLECTION

    def health(self) -> bool:
        self.client.get_collections()
        return True

    def ensure_collection(self, reset: bool = False) -> None:
        if reset and self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        if self.client.collection_exists(self.collection):
            vectors = self.client.get_collection(self.collection).config.params.vectors
            dense = vectors.get("dense") if isinstance(vectors, dict) else None
            if dense is None or dense.size != self.settings.OPENAI_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    "Embedding dimensions changed; configure a new evidence collection"
                )
        else:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    "dense": models.VectorParams(
                        size=self.settings.OPENAI_EMBEDDING_DIMENSIONS,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )
            for field in ("source_type", "ticket_category", "service", "chunk_id", "source_id"):
                self.client.create_payload_index(
                    self.collection, field, models.PayloadSchemaType.KEYWORD, wait=True
                )

    def upsert(self, items: Iterable[EvidenceItem]) -> int:
        batch = list(items)
        if not batch:
            return 0
        existing = {
            str(record.id): (record.payload or {})
            for record in self.client.retrieve(
                collection_name=self.collection,
                ids=[point_id(item.chunk_id) for item in batch],
                with_payload=True,
                with_vectors=False,
            )
        }
        model = self.settings.OPENAI_EMBEDDING_MODEL
        pending = []
        for item in batch:
            stored = existing.get(point_id(item.chunk_id), {})
            if (
                stored.get("content_hash") != item.content_hash
                or stored.get("embedding_model") != model
            ):
                pending.append(item)
            elif any(stored.get(key) != value for key, value in evidence_payload(item).items()):
                self.client.set_payload(
                    self.collection,
                    evidence_payload(item),
                    points=[point_id(item.chunk_id)],
                    wait=True,
                )
        if not pending:
            return 0
        texts = [item.text for item in pending]
        langfuse = get_langfuse(self.settings)
        if langfuse is None:
            dense_vectors = self.embeddings.embed_documents(texts)
            sparse_vectors = list(self.sparse.embed(texts))
        else:
            with langfuse.start_as_current_observation(
                name="evidence-embedding",
                as_type="embedding",
                input={"chunk_count": len(pending)},
                model=self.settings.OPENAI_EMBEDDING_MODEL,
            ) as observation:
                dense_vectors = self.embeddings.embed_documents(texts)
                sparse_vectors = list(self.sparse.embed(texts))
                observation.update(
                    output={
                        "dense_vectors": len(dense_vectors),
                        "sparse_vectors": len(sparse_vectors),
                    }
                )
        points = []
        for item, dense, sparse in zip(pending, dense_vectors, sparse_vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=point_id(item.chunk_id),
                    vector={
                        "dense": dense,
                        "bm25": models.SparseVector(
                            indices=sparse.indices.tolist(), values=sparse.values.tolist()
                        ),
                    },
                    payload={**evidence_payload(item), "embedding_model": model},
                )
            )
        self.client.upsert(self.collection, points=points, wait=True)
        return len(points)

    def get_source_items(self, source_id: str) -> list[EvidenceItem]:
        items = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_id", match=models.MatchValue(value=source_id)
                        )
                    ]
                ),
                offset=offset,
                limit=128,
                with_payload=True,
                with_vectors=False,
            )
            items.extend(EvidenceItem(**dict(point.payload or {})) for point in points)
            if offset is None:
                return sorted(items, key=lambda item: item.chunk_id)
            if len(items) >= 512:
                raise ValueError("Source exceeds the supported 512-chunk limit")

    def get_chunks(self, chunk_ids: list[str]) -> list[EvidenceItem]:
        if not chunk_ids:
            return []
        points = self.client.retrieve(
            self.collection,
            ids=[point_id(value) for value in chunk_ids[:12]],
            with_payload=True,
            with_vectors=False,
        )
        return [EvidenceItem(**dict(point.payload or {})) for point in points]

    def delete_obsolete(self, source_id: str, keep_ids: list[str]) -> None:
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_id", match=models.MatchValue(value=source_id)
                        )
                    ],
                    must_not=[models.HasIdCondition(has_id=[point_id(value) for value in keep_ids])]
                    if keep_ids
                    else [],
                )
            ),
            wait=True,
        )

    def search(
        self,
        query: str,
        limit: int = 8,
        source_types: list[str] | None = None,
        category: str | None = None,
        service: str | None = None,
        services: list[str] | None = None,
        include_shared: bool = False,
    ) -> list[EvidenceItem]:
        dense = self.embeddings.embed_query(query)
        sparse = next(iter(self.sparse.query_embed(query)))
        conditions = []
        if source_types:
            conditions.append(
                models.FieldCondition(key="source_type", match=models.MatchAny(any=source_types))
            )
        if category:
            conditions.append(
                models.FieldCondition(
                    key="ticket_category", match=models.MatchValue(value=category)
                )
            )
        selected_services = services or ([service] if service else [])
        if selected_services:
            alternatives = [
                models.FieldCondition(key="service", match=models.MatchAny(any=selected_services))
            ]
            if include_shared:
                alternatives.append(
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="service"))
                )
            conditions.append(models.Filter(should=alternatives))
        query_filter = models.Filter(must=conditions) if conditions else None
        result = self.client.query_points(
            collection_name=self.collection,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=limit * 2, filter=query_filter),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse.indices.tolist(), values=sparse.values.tolist()
                    ),
                    using="bm25",
                    limit=limit * 2,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit * 2,
        ).points
        seen: set[tuple[str, str]] = set()
        items: list[EvidenceItem] = []
        for point in result:
            payload = dict(point.payload or {})
            key = (payload["chunk_id"], payload["content_hash"])
            if key in seen:
                continue
            seen.add(key)
            items.append(EvidenceItem(**payload, score=float(point.score or 0)))
            if len(items) == limit:
                break
        return items


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
