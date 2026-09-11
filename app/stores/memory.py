from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mem0 import Memory

from app.config import Settings


class MemoryStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": settings.QDRANT_MEMORY_COLLECTION,
                    "url": settings.QDRANT_URL,
                    "embedding_model_dims": settings.OPENAI_EMBEDDING_DIMENSIONS,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "api_key": settings.OPENAI_API_KEY.get_secret_value(),
                    "model": settings.OPENAI_EMBEDDING_MODEL,
                    "embedding_dims": settings.OPENAI_EMBEDDING_DIMENSIONS,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "api_key": settings.OPENAI_API_KEY.get_secret_value(),
                    "model": settings.OPENAI_FAST_MODEL,
                    "temperature": 0,
                },
            },
        }
        self.memory = Memory.from_config(config)

    def health(self) -> bool:
        self.memory.get_all(user_id="organization", limit=1)
        return True

    def search(
        self, query: str, customer_id: str, category: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        combined: list[dict[str, Any]] = []
        for scope in (customer_id, f"category:{category}", "organization"):
            result = self.memory.search(query=query, user_id=scope, limit=limit)
            values = result.get("results", result) if isinstance(result, dict) else result
            for item in values or []:
                normalized = dict(item)
                normalized["scope"] = scope
                if normalized.get("metadata", {}).get("approved") is True:
                    combined.append(normalized)
        seen: set[str] = set()
        output = []
        for item in combined:
            memory_id = str(item.get("id", item.get("memory", "")))
            if memory_id not in seen:
                seen.add(memory_id)
                output.append(item)
        return output[:limit]

    def list_customer(self, customer_id: str) -> list[dict[str, Any]]:
        result = self.memory.get_all(user_id=customer_id)
        values = result.get("results", result) if isinstance(result, dict) else result
        return [
            dict(item) for item in values or [] if item.get("metadata", {}).get("approved") is True
        ]

    def add_approved(
        self,
        text: str,
        customer_id: str,
        category: str,
        ticket_id: str,
        service: str | None = None,
        review_id: str | None = None,
    ) -> Any:
        if review_id and any(
            item.get("metadata", {}).get("review_id") == review_id
            for item in self.list_customer(customer_id)
        ):
            return None
        metadata = {
            "kind": "approved_recommendation",
            "category": category,
            "ticket_id": ticket_id,
            "resolution_status": "APPROVED",
            "approved": True,
            "service": service,
            "review_id": review_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        return self.memory.add(text, user_id=customer_id, metadata=metadata, infer=False)

    def seed_approved(
        self,
        text: str,
        scope: str,
        category: str,
        ticket_id: str,
        service: str | None = None,
    ) -> bool:
        result = self.memory.get_all(user_id=scope)
        values = result.get("results", result) if isinstance(result, dict) else result
        if any(item.get("memory", item.get("text")) == text for item in values or []):
            return False
        metadata = {
            "category": category,
            "ticket_id": ticket_id,
            "resolution_status": "APPROVED",
            "approved": True,
            "service": service,
            "created_at": datetime.now(UTC).isoformat(),
            "seeded": True,
        }
        self.memory.add(text, user_id=scope, metadata=metadata, infer=False)
        return True
