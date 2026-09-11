from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agent import prompts
from app.models import (
    HealthResponse,
    InvestigateRequest,
    InvestigationStatus,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    TicketCategory,
    TicketStatus,
)
from app.observability import (
    investigation_trace,
    langfuse_status,
    score_review,
    trace_details,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ApprovedMemory(BaseModel):
    reusable_fact: str | None = Field(default=None, min_length=10, max_length=800)
    service: str | None = None


def _services(request: Request) -> Any:
    return request.app.state.services


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    services = _services(request)
    statuses = {}
    for name in ("mongo", "qdrant", "graph", "memory"):
        try:
            statuses[name] = "ready" if getattr(services, name).health() else "unavailable"
        except Exception:
            statuses[name] = "unavailable"
    return HealthResponse(
        mongodb=statuses["mongo"],
        qdrant=statuses["qdrant"],
        neo4j=statuses["graph"],
        mem0=statuses["memory"],
        langfuse=langfuse_status(services.settings),
    )


@router.get("/tickets")
def list_tickets(
    request: Request,
    ticket_status: TicketStatus | None = Query(default=None, alias="status"),
    category: TicketCategory | None = None,
    customer_id: str | None = None,
) -> list[dict[str, Any]]:
    return _services(request).mongo.list_tickets(
        status=ticket_status.value if ticket_status else None,
        category=category.value if category else None,
        customer_id=customer_id,
    )


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, request: Request) -> dict[str, Any]:
    mongo = _services(request).mongo
    ticket = mongo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    customer = mongo.get_customer(ticket["customer_id"])
    order = mongo.get_order(ticket["order_id"]) if ticket.get("order_id") else None
    if order and order.get("customer_id") != ticket["customer_id"]:
        raise HTTPException(status_code=409, detail="Ticket and order ownership do not match")
    payment = (
        mongo.get_payment(order_id=ticket["order_id"], customer_id=ticket["customer_id"])
        if ticket.get("order_id")
        else None
    )
    return {"ticket": ticket, "customer": customer, "order": order, "payment": payment}


@router.post("/tickets/{ticket_id}/investigate", status_code=status.HTTP_201_CREATED)
def investigate(ticket_id: str, body: InvestigateRequest, request: Request) -> dict[str, Any]:
    services = _services(request)
    if not services.mongo.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
    thread_id = f"THREAD-{ticket_id}"
    services.mongo.save_run(
        {"run_id": run_id, "thread_id": thread_id, "ticket_id": ticket_id, "status": "RUNNING"}
    )
    try:
        with investigation_trace(
            services.settings,
            run_id=run_id,
            thread_id=thread_id,
            ticket_id=ticket_id,
            question_present=body.question is not None,
        ) as tracing:
            services.workflow.invoke(
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "ticket_id": ticket_id,
                    "user_question": body.question,
                    "retry_count": 0,
                    "trace": [],
                },
                config={"configurable": {"thread_id": thread_id}, **tracing},
            )
    except Exception as exc:
        logger.exception("Investigation failed: %s", run_id)
        services.mongo.save_run(
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "ticket_id": ticket_id,
                "status": "FAILED",
                "error": "Investigation failed; check service availability and configuration.",
            }
        )
        raise HTTPException(
            status_code=502, detail={"message": "Investigation failed", "run_id": run_id}
        ) from exc
    run = services.mongo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=500, detail="Investigation did not persist a result")
    run.update(trace_details(services.settings, run_id))
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    run = _services(request).mongo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Investigation run not found")
    return run


@router.post("/runs/{run_id}/review", status_code=status.HTTP_201_CREATED)
def review_run(run_id: str, body: ReviewRequest, request: Request) -> dict[str, Any]:
    services = _services(request)
    run = services.mongo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Investigation run not found")
    ticket = services.mongo.get_ticket(run["ticket_id"])
    if not ticket:
        raise HTTPException(status_code=409, detail="The run's ticket is missing")
    if run.get("review"):
        return _existing_review(run, body)
    approved = body.decision in {ReviewDecision.APPROVE, ReviewDecision.EDIT_AND_APPROVE}
    if approved and (
        run.get("status") != InvestigationStatus.READY_FOR_REVIEW
        or run.get("verification", {}).get("verdict") != "PASS"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only verified drafts can be approved. Investigate again after resolving the evidence gap.",
        )
    if run.get("status") not in {
        "READY_FOR_REVIEW",
        "ESCALATED",
        "INSUFFICIENT_EVIDENCE",
        "NEEDS_CUSTOMER_INFO",
    }:
        raise HTTPException(status_code=409, detail="This run is not ready for review")
    record = ReviewRecord(
        review_id=f"REV-{uuid.uuid4().hex[:12].upper()}",
        run_id=run_id,
        decision=body.decision,
        original_response=run.get("draft_response"),
        edited_response=body.edited_response,
        reviewer_note=body.reviewer_note,
    )
    response = (
        body.edited_response
        if body.decision == ReviewDecision.EDIT_AND_APPROVE
        else run.get("draft_response")
    )
    if approved:
        status_value = "APPROVED"
    elif body.decision == ReviewDecision.ESCALATE:
        status_value = "ESCALATED"
    else:
        status_value = "REJECTED"
    saved = services.mongo.record_review(
        run_id,
        record.model_dump(mode="json"),
        run["status"],
        status_value,
        response if approved else None,
        approved,
    )
    if saved is None:
        current = services.mongo.get_run(run_id)
        if current and current.get("review"):
            return _existing_review(current, body)
        raise HTTPException(status_code=409, detail="The run changed; refresh before reviewing")
    if approved:
        _write_review_memory(services, saved, ticket)
    score_review(services.settings, run_id=run_id, decision=body.decision.value, approved=approved)
    return _review_result(services.mongo.get_run(run_id))


def _review_result(run: dict) -> dict:
    return {
        "review": run["review"],
        "memory_written": run.get("memory_status") == "WRITTEN",
        "memory_status": run.get("memory_status", "NOT_REQUIRED"),
    }


def _existing_review(run: dict, body: ReviewRequest) -> dict:
    saved = run["review"]
    if any(saved.get(key) != value for key, value in body.model_dump(mode="json").items()):
        raise HTTPException(
            status_code=409, detail="A different review is already recorded for this run"
        )
    return _review_result(run)


def _write_review_memory(services: Any, run: dict, ticket: dict) -> None:
    if not services.mongo.claim_memory_write(run["run_id"]):
        return
    try:
        review = ReviewRecord.model_validate(run["review"])
        memory = _distill_approved_memory(
            services, run, ticket, run.get("approved_response"), review
        )
        if memory.reusable_fact is None:
            services.mongo.finish_memory_write(run["run_id"], "SKIPPED")
            return
        services.memory.add_approved(
            memory.reusable_fact,
            customer_id=ticket["customer_id"],
            category=ticket["category"],
            ticket_id=ticket["ticket_id"],
            service=memory.service,
            review_id=review.review_id,
        )
    except Exception:
        logger.warning(
            "Memory write failed for run %s; review remains saved", run["run_id"], exc_info=True
        )
        services.mongo.finish_memory_write(run["run_id"], "FAILED")
    else:
        services.mongo.finish_memory_write(run["run_id"], "WRITTEN")


@router.post("/runs/{run_id}/memory/retry")
def retry_review_memory(run_id: str, request: Request) -> dict:
    services = _services(request)
    run = services.mongo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Investigation run not found")
    if run.get("status") != "APPROVED" or not run.get("review"):
        raise HTTPException(status_code=409, detail="No approved review is available")
    ticket = services.mongo.get_ticket(run["ticket_id"])
    if not ticket:
        raise HTTPException(status_code=409, detail="The run's ticket is missing")
    _write_review_memory(services, run, ticket)
    return _review_result(services.mongo.get_run(run_id))


@router.get("/customers/{customer_id}/memories")
def customer_memories(customer_id: str, request: Request) -> list[dict[str, Any]]:
    if not _services(request).mongo.get_customer(customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    return _services(request).memory.list_customer(customer_id)


@router.get("/graph/tickets/{ticket_id}")
def ticket_graph(ticket_id: str, request: Request) -> dict[str, Any]:
    if not _services(request).mongo.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _services(request).graph.get_ticket_neighborhood(ticket_id, max_depth=2)


@router.post("/ingestion/run")
def run_ingestion(request: Request, reset: bool = False) -> dict[str, int]:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Ingestion is available only from localhost")
    try:
        return _services(request).ingestion.run(reset=reset)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409, detail="Demo assets are missing; run the asset builder"
        ) from exc


def _distill_approved_memory(
    services: Any,
    run: dict[str, Any],
    ticket: dict[str, Any],
    response: str | None,
    review: ReviewRecord,
) -> ApprovedMemory:
    structured = services.models.fast.with_structured_output(ApprovedMemory)
    payload = {
        "ticket_id": ticket["ticket_id"],
        "category": ticket["category"],
        "diagnosis": run.get("diagnosis"),
        "approved_response": response,
        "reviewer_note": review.reviewer_note,
    }
    return structured.invoke(
        [
            SystemMessage(content=prompts.UNTRUSTED_RULE + "\n" + prompts.MEMORY),
            HumanMessage(
                content="BEGIN_UNTRUSTED_DATA\n"
                + json.dumps(payload, default=str)
                + "\nEND_UNTRUSTED_DATA"
            ),
        ]
    )
