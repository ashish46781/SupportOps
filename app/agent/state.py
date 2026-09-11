from __future__ import annotations

from typing import Any, TypedDict

from app.models import Diagnosis, EvidenceItem, RoutePlan, Verification, WorkflowTrace


class InvestigationState(TypedDict, total=False):
    thread_id: str
    run_id: str
    ticket_id: str
    user_question: str | None
    ticket: dict[str, Any]
    customer: dict[str, Any]
    order: dict[str, Any] | None
    payment: dict[str, Any] | None
    previous_tickets: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    route: RoutePlan
    rewritten_query: str
    vector_evidence: list[EvidenceItem]
    graph_evidence: dict[str, Any]
    structured_evidence: list[EvidenceItem]
    evidence: list[EvidenceItem]
    diagnosis: Diagnosis
    verification: Verification
    recommendation: str
    draft_response: str
    citations: list[str]
    confidence: float
    retry_count: int
    requires_human: bool
    escalation_reason: str | None
    status: str
    errors: list[str]
    trace: list[WorkflowTrace]
    missing_sources: list[str]
    context_overflow: bool
    retrieval_changed: bool
