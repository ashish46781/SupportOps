from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.nodes import Services
from app.agent.workflow import build_workflow
from app.models import (
    Diagnosis,
    EvidenceClaim,
    EvidenceItem,
    RoutePlan,
    ScreenshotDescription,
    Verification,
)
from app.stores.qdrant import content_hash

ROOT = Path(__file__).resolve().parents[1]


class FakeMongo:
    def __init__(self, ticket_id: str = "TKT-1001", category: str = "PAYMENT") -> None:
        records = json.loads(
            (ROOT / "demo_data" / "shopflow" / "raw" / "records.json").read_text(encoding="utf-8")
        )
        ticket = next((item for item in records["tickets"] if item["ticket_id"] == ticket_id), None)
        if ticket is None:
            ticket = dict(records["tickets"][0], ticket_id=ticket_id, category=category)
        ticket = dict(ticket, category=category)
        self.ticket = ticket
        self.customer = next(
            item for item in records["customers"] if item["customer_id"] == ticket["customer_id"]
        )
        self.order = next(
            (item for item in records["orders"] if item["order_id"] == ticket.get("order_id")), None
        )
        self.payment = next(
            (item for item in records["payments"] if item["order_id"] == ticket.get("order_id")),
            None,
        )
        self.previous = [
            item
            for item in records["tickets"]
            if item["customer_id"] == ticket["customer_id"] and item["ticket_id"] != ticket_id
        ]
        self.runs: dict[str, dict] = {}
        self.reviews: list[dict] = []

    def get_ticket(self, ticket_id: str) -> dict | None:
        return self.ticket if ticket_id == self.ticket["ticket_id"] else None

    def get_customer(self, customer_id: str) -> dict | None:
        return self.customer if customer_id == self.customer["customer_id"] else None

    def get_order(self, order_id: str) -> dict | None:
        return self.order if self.order and order_id == self.order["order_id"] else None

    def get_payment(self, **_kwargs: Any) -> dict | None:
        return self.payment

    def get_previous_tickets(self, _customer_id: str, exclude: str | None = None) -> list[dict]:
        return [item for item in self.previous if item["ticket_id"] != exclude]

    def list_tickets(
        self, status: str | None = None, category: str | None = None, customer_id: str | None = None
    ) -> list[dict]:
        if status and self.ticket["status"] != status:
            return []
        if category and self.ticket["category"] != category:
            return []
        if customer_id and self.ticket["customer_id"] != customer_id:
            return []
        return [self.ticket]

    def save_run(self, run: dict) -> None:
        self.runs[run["run_id"]] = run

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def record_review(self, run_id, review, expected_status, status, response, approved):
        run = self.runs[run_id]
        if run.get("review") or run.get("status") != expected_status:
            return None
        run.update(
            review=review, status=status, memory_status="PENDING" if approved else "NOT_REQUIRED"
        )
        if response is not None:
            run["approved_response"] = response
        self.reviews.append(review)
        return run

    def claim_memory_write(self, run_id):
        run = self.runs[run_id]
        if run.get("memory_status") not in {"PENDING", "FAILED"}:
            return False
        run["memory_status"] = "WRITING"
        return True

    def finish_memory_write(self, run_id, status):
        self.runs[run_id]["memory_status"] = status

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


class FakeQdrant:
    def search(self, _query: str, **_kwargs: Any) -> list[EvidenceItem]:
        raw = [
            (
                "payment_policy:p1",
                "payment_policy",
                "[payment_policy.pdf, p.1]",
                "Captured payment plus failed order requires reconciliation.",
                "document",
            ),
            (
                "INC-001:p1",
                "INC-001",
                "[INC-001, p.1]",
                "Callback failures left captured payments paired with failed orders.",
                "incident",
            ),
            (
                "code:handle",
                "shopflow_api",
                "[payments.py:handle_payment_callback]",
                "handle_payment_callback may fail before reconciliation is scheduled.",
                "code",
            ),
        ]
        return [
            EvidenceItem(
                citation_id=citation,
                chunk_id=chunk,
                source_id=source,
                source_path=source,
                source_type=source_type,
                text=text,
                service="PaymentService",
                content_hash=content_hash(text),
                score=1,
            )
            for chunk, source, citation, text, source_type in raw
        ]

    def get_source_items(self, source_id):
        return [item for item in self.search("policy") if item.source_id == source_id]

    def get_chunks(self, chunk_ids):
        return [item for item in self.search("graph") if item.chunk_id in chunk_ids]

    def health(self) -> bool:
        return True


class FakeGraph:
    def _value(self) -> dict:
        return {
            "nodes": [{"id": "PaymentService", "labels": ["Service"], "name": "PaymentService"}],
            "edges": [],
        }

    def get_ticket_neighborhood(self, *_args: Any, **_kwargs: Any) -> dict:
        return self._value()

    def get_service_impact(self, *_args: Any, **_kwargs: Any) -> dict:
        return self._value()

    def get_error_context(self, *_args: Any, **_kwargs: Any) -> dict:
        return self._value()

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


class FakeMemory:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def search(self, *_args: Any, **_kwargs: Any) -> list[dict]:
        return [
            {
                "id": "MEM-001",
                "memory": "Previous callback reconciliation restored Ananya's order.",
                "metadata": {"approved": True, "service": "PaymentService"},
                "score": 1,
            }
        ]

    def list_customer(self, _customer_id: str) -> list[dict]:
        return self.search()

    def add_approved(self, text: str, **metadata: Any) -> None:
        self.added.append({"text": text, **metadata})

    def health(self) -> bool:
        return True


class FakeStructured:
    def __init__(self, parent: FakeModel, schema: type) -> None:
        self.parent = parent
        self.schema = schema

    def invoke(self, _messages: Any) -> Any:
        name = self.schema.__name__
        if name == "RoutePlan":
            return RoutePlan(
                category=self.parent.category,
                structured_lookup=True,
                vector_search=True,
                graph_search=True,
                inspect_screenshot=True,
                inspect_code=True,
                entities=["PaymentService"],
                error_codes=["PAYMENT_SYNC_502"],
                query="captured payment failed order callback reconciliation",
            )
        if name == "Diagnosis":
            citations = (
                ["[does-not-exist]"]
                if self.parent.invalid_diagnosis
                else [
                    "[MongoDB:ORD-1001]",
                    "[MongoDB:PAY-1001]",
                    "[payment_policy.pdf, p.1]",
                    "[INC-001, p.1]",
                    "[payments.py:handle_payment_callback]",
                    "[Mem0:CUST-001:MEM-001]",
                ]
            )
            return Diagnosis(
                issue_summary="A captured payment is paired with a failed order.",
                probable_cause="A supported payment/order callback synchronization failure.",
                supporting_facts=["Exact statuses conflict", "Prior incident matches"],
                recommended_next_action="Request reconciliation; escalate to engineering if it fails.",
                risk_level="MEDIUM",
                response_draft="We found a status mismatch and will have the supported reconciliation reviewed.",
                citation_ids=citations,
                confidence=0.88,
                claims=[
                    EvidenceClaim(
                        text="The order and payment statuses disagree.",
                        kind="recorded_fact",
                        evidence_ids=["mongodb:ORD-1001", "mongodb:PAY-1001"],
                    ),
                    EvidenceClaim(
                        text="Callback synchronization is a possible cause.",
                        kind="hypothesis",
                        evidence_ids=["INC-001:p1", "code:handle"],
                        limitation="Current transaction logs are missing.",
                    ),
                ],
            )
        if name == "Verification":
            verdict = self.parent.verdicts.pop(0) if self.parent.verdicts else "PASS"
            return Verification(
                verdict=verdict,
                reason="Evidence is sufficient."
                if verdict == "PASS"
                else "Retrieve more direct evidence.",
                retry_query="callback policy direct evidence" if verdict == "RETRY" else None,
            )
        if name == "RewrittenQuery":
            return self.schema(query="payment callback reconciliation policy incident code")
        if name == "ScreenshotDescription":
            return ScreenshotDescription(
                visible_text=["Payment received", "Order status: FAILED", "PAYMENT_SYNC_502"],
                error_code="PAYMENT_SYNC_502",
                screen_name="ShopFlow Checkout",
                issue_type="payment synchronization",
                concise_description="The visible order and payment states disagree.",
            )
        if name == "RankedEvidence":
            return self.schema(
                chunk_ids=[
                    "payment_policy:p1",
                    "INC-001:p1",
                    "code:handle",
                    "mongodb:ORD-1001",
                    "mongodb:PAY-1001",
                    "memory:MEM-001",
                    "screenshot-live:payment_failed",
                    "graph:PaymentService",
                ]
            )
        if name == "ApprovedMemory":
            return self.schema(
                reusable_fact="Approved reconciliation is the supported response to this callback mismatch.",
                service="PaymentService",
            )
        raise AssertionError(f"Unexpected schema: {name}")


class FakeModel:
    def __init__(
        self,
        category: str = "PAYMENT",
        verdicts: list[str] | None = None,
        invalid_diagnosis: bool = False,
    ) -> None:
        self.category = category
        self.verdicts = list(verdicts or ["PASS"])
        self.invalid_diagnosis = invalid_diagnosis
        self.model_name = "fake-model"

    def with_structured_output(self, schema: type) -> FakeStructured:
        return FakeStructured(self, schema)


@pytest.fixture
def fake_services() -> Any:
    mongo = FakeMongo()
    models = SimpleNamespace(fast=FakeModel(), main=FakeModel())
    runtime = SimpleNamespace(
        settings=SimpleNamespace(LOG_LEVEL="INFO"),
        mongo=mongo,
        qdrant=FakeQdrant(),
        graph=FakeGraph(),
        memory=FakeMemory(),
        models=models,
        ingestion=SimpleNamespace(run=lambda reset=False: {"indexed": 1}),
    )
    node_services = Services(
        mongo=runtime.mongo,
        qdrant=runtime.qdrant,
        graph=runtime.graph,
        memory=runtime.memory,
        models=models,
    )
    runtime.workflow = build_workflow(node_services)
    return runtime
