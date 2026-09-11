from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.database import Database

from app.config import Settings


class MongoStore:
    def __init__(self, settings: Settings, client: MongoClient | None = None) -> None:
        self.client = client or MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=3000)
        self.db: Database = self.client[settings.MONGODB_DATABASE]

    def close(self) -> None:
        self.client.close()

    def health(self) -> bool:
        return bool(self.client.admin.command("ping").get("ok"))

    def ensure_indexes(self) -> None:
        unique = {
            "customers": "customer_id",
            "orders": "order_id",
            "payments": "payment_id",
            "tickets": "ticket_id",
            "investigation_runs": "run_id",
            "reviews": "review_id",
            "ingested_sources": "source_id",
        }
        for collection, key in unique.items():
            self.db[collection].create_index([(key, ASCENDING)], unique=True)
        self.db.orders.create_index([("customer_id", ASCENDING)])
        self.db.payments.create_index([("customer_id", ASCENDING), ("order_id", ASCENDING)])
        self.db.tickets.create_index(
            [("customer_id", ASCENDING), ("status", ASCENDING), ("category", ASCENDING)]
        )
        self.db.investigation_runs.create_index(
            [("ticket_id", ASCENDING), ("created_at", ASCENDING)]
        )
        self.db.reviews.create_index([("run_id", ASCENDING)])

    def upsert_many(self, collection: str, key: str, records: Iterable[dict[str, Any]]) -> int:
        count = 0
        for record in records:
            self.db[collection].replace_one({key: record[key]}, record, upsert=True)
            count += 1
        return count

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return _clean(self.db.customers.find_one({"customer_id": customer_id}))

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return _clean(self.db.orders.find_one({"order_id": order_id}))

    def get_payment(
        self, *, order_id: str | None = None, customer_id: str | None = None
    ) -> dict[str, Any] | None:
        query = {
            key: value
            for key, value in {"order_id": order_id, "customer_id": customer_id}.items()
            if value
        }
        return _clean(self.db.payments.find_one(query, sort=[("created_at", -1)]))

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        return _clean(self.db.tickets.find_one({"ticket_id": ticket_id}))

    def get_previous_tickets(
        self, customer_id: str, exclude: str | None = None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"customer_id": customer_id}
        if exclude:
            query["ticket_id"] = {"$ne": exclude}
        return [
            _clean(item) for item in self.db.tickets.find(query).sort("created_at", -1).limit(10)
        ]

    def list_tickets(
        self,
        status: str | None = None,
        category: str | None = None,
        customer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = {
            key: value
            for key, value in {
                "status": status,
                "category": category,
                "customer_id": customer_id,
            }.items()
            if value
        }
        return [
            _clean(item) for item in self.db.tickets.find(query).sort("created_at", -1).limit(100)
        ]

    def save_run(self, run: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        updates = {key: value for key, value in run.items() if key != "created_at"}
        updates["updated_at"] = now
        self.db.investigation_runs.update_one(
            {"run_id": run["run_id"]},
            {"$set": updates, "$setOnInsert": {"created_at": run.get("created_at", now)}},
            upsert=True,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return _clean(self.db.investigation_runs.find_one({"run_id": run_id}))

    def record_review(
        self,
        run_id: str,
        review: dict,
        expected_status: str,
        status: str,
        response: str | None,
        approved: bool,
    ) -> dict | None:
        # Save the review and memory status together to prevent competing decisions.
        updates = {
            "review": review,
            "status": status,
            "memory_status": "PENDING" if approved else "NOT_REQUIRED",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if response is not None:
            updates["approved_response"] = response
        return _clean(
            self.db.investigation_runs.find_one_and_update(
                {"run_id": run_id, "status": expected_status, "review": {"$exists": False}},
                {"$set": updates},
                return_document=ReturnDocument.AFTER,
            )
        )

    def claim_memory_write(self, run_id: str) -> bool:
        result = self.db.investigation_runs.update_one(
            {"run_id": run_id, "memory_status": {"$in": ["PENDING", "FAILED"]}},
            {"$set": {"memory_status": "WRITING"}},
        )
        return result.modified_count == 1

    def finish_memory_write(self, run_id: str, status: str) -> None:
        self.db.investigation_runs.update_one(
            {"run_id": run_id, "memory_status": "WRITING"},
            {"$set": {"memory_status": status, "updated_at": datetime.now(UTC).isoformat()}},
        )

    def save_ingested_source(self, source: dict[str, Any]) -> None:
        self.db.ingested_sources.replace_one(
            {"source_id": source["source_id"]}, source, upsert=True
        )

    def get_ingested_source(self, source_id: str) -> dict[str, Any] | None:
        return _clean(self.db.ingested_sources.find_one({"source_id": source_id}))


def _clean(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    document.pop("_id", None)
    return document
