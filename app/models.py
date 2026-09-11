from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TicketStatus(StrEnum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketCategory(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    LOGIN = "LOGIN"
    IMAGE_UPLOAD = "IMAGE_UPLOAD"
    COUPON = "COUPON"
    SECURITY = "SECURITY"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    EDIT_AND_APPROVE = "EDIT_AND_APPROVE"
    ESCALATE = "ESCALATE"
    REJECT = "REJECT"


class InvestigationStatus(StrEnum):
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_CUSTOMER_INFO = "NEEDS_CUSTOMER_INFO"
    ESCALATED = "ESCALATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class InvestigateRequest(BaseModel):
    question: str | None = Field(default=None, max_length=1000)


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    edited_response: str | None = Field(default=None, max_length=5000)
    reviewer_note: str | None = Field(default=None, max_length=2000)

    @field_validator("edited_response", "reviewer_note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_edit(self) -> ReviewRequest:
        if self.decision != ReviewDecision.EDIT_AND_APPROVE and self.edited_response:
            raise ValueError("Use EDIT_AND_APPROVE to change the response")
        if self.decision == ReviewDecision.EDIT_AND_APPROVE and not self.edited_response:
            raise ValueError("edited_response is required for EDIT_AND_APPROVE")
        if (
            self.decision in {ReviewDecision.ESCALATE, ReviewDecision.REJECT}
            and not self.reviewer_note
        ):
            raise ValueError("reviewer_note is required for escalation or rejection")
        return self


class RoutePlan(BaseModel):
    category: TicketCategory
    structured_lookup: bool = True
    vector_search: bool = True
    graph_search: bool = True
    inspect_screenshot: bool = False
    inspect_code: bool = False
    entities: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)
    query: str = Field(min_length=3, max_length=600)
    additional_queries: list[str] = Field(default_factory=list, max_length=2)
    evidence_needed: list[str] = Field(default_factory=list, max_length=5)


class ScreenshotDescription(BaseModel):
    visible_text: list[str]
    error_code: str | None = None
    screen_name: str
    issue_type: str
    concise_description: str


class EvidenceItem(BaseModel):
    citation_id: str
    chunk_id: str
    source_id: str
    source_path: str
    source_type: str
    text: str
    page: int | None = None
    section: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    ticket_category: str | None = None
    service: str | None = None
    content_hash: str
    score: float = 0.0
    parent_id: str | None = None
    parent_text: str | None = None
    source_hash: str | None = None


class EvidenceClaim(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["recorded_fact", "customer_report", "hypothesis", "recommendation"]
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    limitation: str = Field(default="", max_length=800)


class Diagnosis(BaseModel):
    issue_summary: str
    probable_cause: str
    supporting_facts: list[str]
    missing_facts: list[str] = Field(default_factory=list)
    recommended_next_action: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    response_draft: str
    citation_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    claims: list[EvidenceClaim] = Field(default_factory=list, max_length=12)


class Verification(BaseModel):
    verdict: Literal["PASS", "RETRY", "ABSTAIN", "ESCALATE"]
    unsupported_claims: list[str] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    reason: str
    retry_query: str | None = None
    contradictions: list[str] = Field(default_factory=list, max_length=8)


class WorkflowTrace(BaseModel):
    node: str
    duration_ms: int
    detail: dict[str, Any] = Field(default_factory=dict)


class ReviewRecord(BaseModel):
    review_id: str
    run_id: str
    decision: ReviewDecision
    original_response: str | None = None
    edited_response: str | None = None
    reviewer_note: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HealthResponse(BaseModel):
    application: str = "ready"
    mongodb: str
    qdrant: str
    neo4j: str
    mem0: str
    langfuse: str = "disabled"
