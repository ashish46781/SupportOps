from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_investigation_returns_saved_result_when_trace_link_is_unavailable(
    fake_services, monkeypatch
) -> None:
    from contextlib import nullcontext

    from app import observability

    client = Mock()
    client.create_trace_id.return_value = "trace-test"
    client.get_trace_url.side_effect = httpx.ConnectError("Langfuse unavailable")
    monkeypatch.setattr(observability, "get_langfuse", lambda settings: client)
    monkeypatch.setattr("app.api.investigation_trace", lambda *args, **kwargs: nullcontext({}))
    with TestClient(create_app(fake_services)) as api:
        response = api.post("/tickets/TKT-1001/investigate", json={})
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["run_id"] in fake_services.mongo.runs
    assert result["langfuse_trace_id"] == "trace-test"
    assert "langfuse_trace_url" not in result


def test_health_and_core_routes(fake_services) -> None:
    with TestClient(create_app(fake_services)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["mongodb"] == "ready"
        assert client.get("/tickets").json()[0]["ticket_id"] == "TKT-1001"
        detail = client.get("/tickets/TKT-1001")
        assert detail.status_code == 200
        run = client.post("/tickets/TKT-1001/investigate", json={})
        assert run.status_code == 201
        assert run.json()["status"] == "READY_FOR_REVIEW"


def test_human_approval_writes_distilled_memory(fake_services) -> None:
    fake_services.mongo.runs["RUN-APPROVE"] = {
        "run_id": "RUN-APPROVE",
        "ticket_id": "TKT-1001",
        "draft_response": "We will request review of reconciliation.",
        "diagnosis": {"probable_cause": "callback mismatch"},
        "status": "READY_FOR_REVIEW",
        "verification": {"verdict": "PASS"},
    }
    with TestClient(create_app(fake_services)) as client:
        response = client.post("/runs/RUN-APPROVE/review", json={"decision": "APPROVE"})
    assert response.status_code == 201
    assert response.json()["memory_written"] is True
    assert len(fake_services.memory.added) == 1
    assert fake_services.memory.added[0]["review_id"].startswith("REV-")


def test_rejection_does_not_write_approved_memory(fake_services) -> None:
    fake_services.mongo.runs["RUN-REJECT"] = {
        "run_id": "RUN-REJECT",
        "ticket_id": "TKT-1001",
        "draft_response": "Draft",
        "diagnosis": {},
        "status": "INSUFFICIENT_EVIDENCE",
    }
    with TestClient(create_app(fake_services)) as client:
        response = client.post(
            "/runs/RUN-REJECT/review",
            json={"decision": "REJECT", "reviewer_note": "Evidence is too weak."},
        )
    assert response.status_code == 201
    assert response.json()["memory_written"] is False
    assert fake_services.memory.added == []


def seed_review(services, status="READY_FOR_REVIEW", verdict="PASS"):
    services.mongo.runs["RUN-REVIEW"] = {
        "run_id": "RUN-REVIEW",
        "ticket_id": "TKT-1001",
        "draft_response": "Request reconciliation.",
        "diagnosis": {},
        "status": status,
        "verification": {"verdict": verdict},
    }


def test_repeated_approval_is_idempotent_and_conflicting_review_is_rejected(fake_services):
    seed_review(fake_services)
    with TestClient(create_app(fake_services)) as api:
        first = api.post("/runs/RUN-REVIEW/review", json={"decision": "APPROVE"})
        second = api.post("/runs/RUN-REVIEW/review", json={"decision": "APPROVE"})
        different = api.post(
            "/runs/RUN-REVIEW/review",
            json={
                "decision": "REJECT",
                "reviewer_note": "Changed my mind",
            },
        )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(fake_services.mongo.reviews) == len(fake_services.memory.added) == 1
    assert different.status_code == 409


def test_memory_failure_preserves_approval_and_can_be_retried(fake_services, monkeypatch):
    seed_review(fake_services)
    original_add = fake_services.memory.add_approved
    monkeypatch.setattr(
        fake_services.memory, "add_approved", Mock(side_effect=RuntimeError("offline"))
    )
    with TestClient(create_app(fake_services)) as api:
        first = api.post("/runs/RUN-REVIEW/review", json={"decision": "APPROVE"})
        assert first.status_code == 201
        assert first.json()["memory_status"] == "FAILED"
        assert fake_services.mongo.runs["RUN-REVIEW"]["status"] == "APPROVED"
        monkeypatch.setattr(fake_services.memory, "add_approved", original_add)
        retry = api.post("/runs/RUN-REVIEW/memory/retry")
        again = api.post("/runs/RUN-REVIEW/memory/retry")
    assert retry.json()["memory_status"] == again.json()["memory_status"] == "WRITTEN"
    assert len(fake_services.memory.added) == 1


def test_approval_can_skip_unhelpful_memory(fake_services, monkeypatch):
    from app.api import ApprovedMemory

    seed_review(fake_services)
    monkeypatch.setattr("app.api._distill_approved_memory", lambda *args: ApprovedMemory())
    with TestClient(create_app(fake_services)) as api:
        response = api.post("/runs/RUN-REVIEW/review", json={"decision": "APPROVE"})
    assert response.json()["memory_status"] == "SKIPPED"
    assert fake_services.memory.added == []


@pytest.mark.parametrize(
    "status,verdict",
    [
        ("ESCALATED", "ESCALATE"),
        ("INSUFFICIENT_EVIDENCE", "ABSTAIN"),
        ("RUNNING", "PASS"),
        ("READY_FOR_REVIEW", "ABSTAIN"),
    ],
)
def test_only_ready_verified_runs_can_be_approved(fake_services, status, verdict):
    seed_review(fake_services, status, verdict)
    with TestClient(create_app(fake_services)) as api:
        response = api.post("/runs/RUN-REVIEW/review", json={"decision": "APPROVE"})
    assert response.status_code == 409
    assert fake_services.mongo.reviews == []


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "EDIT_AND_APPROVE", "edited_response": "   "},
        {"decision": "REJECT", "reviewer_note": "   "},
        {"decision": "APPROVE", "edited_response": "A hidden edit"},
    ],
)
def test_invalid_review_text_is_rejected(fake_services, body):
    with TestClient(create_app(fake_services)) as api:
        assert api.post("/runs/unknown/review", json=body).status_code == 422


def test_failed_investigation_is_saved_with_safe_error(fake_services):
    fake_services.workflow = Mock()
    fake_services.workflow.invoke.side_effect = RuntimeError("private upstream details")
    with TestClient(create_app(fake_services)) as api:
        response = api.post("/tickets/TKT-1001/investigate", json={})
        run = api.get("/runs/" + response.json()["detail"]["run_id"]).json()
    assert response.status_code == 502
    assert run["status"] == "FAILED"
    assert "private upstream details" not in str(run)


def test_unlinked_ticket_does_not_show_latest_unrelated_payment(fake_services):
    fake_services.mongo.ticket["order_id"] = None
    with TestClient(create_app(fake_services)) as api:
        detail = api.get("/tickets/TKT-1001").json()
    assert detail["payment"] is None
