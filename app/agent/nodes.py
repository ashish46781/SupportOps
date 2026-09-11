from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agent import prompts
from app.agent.retrieval import (
    deduplicate,
    evidence_package,
    fuse_results,
    graph_chunk_ids,
    required_sources,
    search_services,
)
from app.agent.state import InvestigationState
from app.ingestion.documents import allowed_source
from app.ingestion.service import describe_screenshot
from app.models import (
    Diagnosis,
    EvidenceItem,
    InvestigationStatus,
    RoutePlan,
    Verification,
    WorkflowTrace,
)
from app.stores.qdrant import content_hash


@dataclass(slots=True)
class Services:
    mongo: Any
    qdrant: Any
    graph: Any
    memory: Any
    models: Any


class RankedEvidence(BaseModel):
    chunk_ids: list[str] = Field(max_length=8)


class RewrittenQuery(BaseModel):
    query: str = Field(min_length=3, max_length=600)


def load_context(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    ticket = services.mongo.get_ticket(state["ticket_id"])
    if not ticket:
        raise LookupError(f"Ticket {state['ticket_id']} was not found")
    customer = services.mongo.get_customer(ticket["customer_id"])
    if not customer:
        raise LookupError(f"Customer {ticket['customer_id']} was not found")
    order = services.mongo.get_order(ticket["order_id"]) if ticket.get("order_id") else None
    if order and order.get("customer_id") != ticket["customer_id"]:
        raise ValueError("Ticket and order ownership do not match")
    payment = (
        services.mongo.get_payment(order_id=ticket["order_id"], customer_id=ticket["customer_id"])
        if ticket.get("order_id")
        else None
    )
    previous = services.mongo.get_previous_tickets(
        ticket["customer_id"], exclude=ticket["ticket_id"]
    )
    query = " ".join(
        filter(None, [ticket["subject"], ticket["description"], state.get("user_question")])
    )
    memories = services.memory.search(query, ticket["customer_id"], ticket["category"])
    detail = {
        "tools": [
            "get_customer",
            "get_order",
            "get_payment",
            "get_previous_tickets",
            "search_customer_memory",
        ]
    }
    return {
        "ticket": ticket,
        "customer": customer,
        "order": order,
        "payment": payment,
        "previous_tickets": previous,
        "memories": memories,
        "retry_count": state.get("retry_count", 0),
        "errors": [],
        "trace": _trace(state, "load_context", started, detail),
    }


def plan(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    payload = _context_payload(state)
    route = _structured(
        services.models.fast,
        RoutePlan,
        prompts.PLAN,
        payload,
    )
    if state["ticket"].get("screenshot_path"):
        route.inspect_screenshot = True
    route.structured_lookup = True
    return {
        "route": route,
        "rewritten_query": route.query,
        "trace": _trace(state, "plan", started, route.model_dump(exclude={"query"})),
    }


def retrieve(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    route = state["route"]
    source_types = ["document", "incident"]
    if route.inspect_code:
        source_types.append("code")
    if route.inspect_screenshot:
        source_types.append("screenshot")
    services_to_search = search_services(state)
    queries = [state["rewritten_query"]]
    if not state.get("retry_count"):
        queries.extend(route.additional_queries)
    queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:3]
    vector = (
        fuse_results(
            [
                services.qdrant.search(
                    query,
                    limit=12,
                    source_types=source_types,
                    services=services_to_search,
                    include_shared=True,
                )
                for query in queries
            ]
        )
        if route.vector_search
        else []
    )
    structured = _structured_items(state)
    policies = []
    missing_sources = []
    for source_id in required_sources(state):
        found = services.qdrant.get_source_items(source_id)
        if not found:
            missing_sources.append(source_id)
        policies.extend(found)
    memory_items = _memory_items(state)
    screenshot_item = _inspect_screenshot(state, services) if route.inspect_screenshot else None
    graph_result: dict[str, Any] = {"nodes": [], "edges": []}
    graph_evidence = []
    if route.graph_search:
        graph_result = services.graph.get_ticket_neighborhood(state["ticket_id"], max_depth=2)
        for service in services_to_search:
            graph_result = _merge_graphs(
                graph_result, services.graph.get_service_impact(service, max_depth=2)
            )
        for error_code in route.error_codes or [state["ticket"].get("error_code")]:
            if error_code:
                graph_result = _merge_graphs(
                    graph_result, services.graph.get_error_context(error_code, max_depth=2)
                )
        graph_evidence = services.qdrant.get_chunks(graph_chunk_ids(graph_result))
    # Cite the source text reached through the graph, not the relationship itself.
    required = deduplicate([*structured, *policies])
    required_ids = {item.chunk_id for item in required}
    candidates = [
        item
        for item in deduplicate(
            [
                *vector,
                *graph_evidence,
                *memory_items,
                *([screenshot_item] if screenshot_item else []),
            ]
        )
        if item.chunk_id not in required_ids
    ]
    optional = _rerank(candidates, state["rewritten_query"], services.models.fast)
    ranked, overflow = evidence_package(required, optional)
    previous = {(item.chunk_id, item.content_hash) for item in state.get("evidence", [])}
    changed = bool({(item.chunk_id, item.content_hash) for item in ranked} - previous)
    detail = {
        "queries": queries,
        "evidence_ids": [item.chunk_id for item in ranked],
        "tools": [
            name
            for name, enabled in {
                "search_evidence": route.vector_search,
                "search_graph": route.graph_search,
                "inspect_screenshot": route.inspect_screenshot,
            }.items()
            if enabled
        ],
    }
    return {
        "vector_evidence": vector,
        "structured_evidence": structured,
        "missing_sources": missing_sources,
        "context_overflow": overflow,
        "retrieval_changed": changed,
        "graph_evidence": graph_result,
        "evidence": ranked,
        "trace": _trace(state, "retrieve", started, detail),
    }


def diagnose(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    if state.get("missing_sources") or state.get("context_overflow"):
        diagnosis = Diagnosis(
            issue_summary="Required evidence is unavailable.",
            probable_cause="Not established.",
            supporting_facts=[],
            recommended_next_action="Restore the required sources and investigate again.",
            risk_level="HIGH",
            response_draft="More information is needed before we can provide a supported response.",
            citation_ids=[],
            confidence=0,
        )
    else:
        diagnosis = _structured(
            services.models.main,
            Diagnosis,
            prompts.DIAGNOSE,
            _evidence_payload(state),
        )
    # Keep model-supplied citations so the verifier can flag invalid ones.
    by_id = {item.chunk_id: item for item in state["evidence"]}
    citations = list(
        dict.fromkeys(
            by_id[identifier].citation_id
            for claim in diagnosis.claims
            for identifier in claim.evidence_ids
            if identifier in by_id
        )
    )
    diagnosis.citation_ids = list(dict.fromkeys([*diagnosis.citation_ids, *citations]))
    return {
        "diagnosis": diagnosis,
        "recommendation": diagnosis.recommended_next_action,
        "draft_response": diagnosis.response_draft,
        "citations": diagnosis.citation_ids,
        "confidence": diagnosis.confidence,
        "trace": _trace(
            state,
            "diagnose",
            started,
            {"citation_ids": diagnosis.citation_ids, "confidence": diagnosis.confidence},
        ),
    }


def verify(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    by_id = {item.chunk_id: item for item in state["evidence"]}
    valid_ids = {item.citation_id for item in state["evidence"]}
    diagnosis = state["diagnosis"]
    invalid = sorted(
        (set(diagnosis.citation_ids) - valid_ids)
        | {
            identifier
            for claim in diagnosis.claims
            for identifier in claim.evidence_ids
            if identifier not in by_id
        }
    )
    ticket_category = state["ticket"]["category"]
    route_category = state.get("route").category if state.get("route") else ticket_category
    is_security = "SECURITY" in {ticket_category, route_category}
    if is_security:
        verification = Verification(
            verdict="ESCALATE",
            invalid_citations=invalid,
            reason="Security-sensitive reports require escalation; no customer action was performed.",
        )
    elif state.get("context_overflow") or state.get("missing_sources"):
        verification = Verification(
            verdict="ABSTAIN",
            reason="Required evidence is unavailable or exceeds the review limit.",
            unsupported_claims=state.get("missing_sources", []),
        )
    elif invalid or not diagnosis.claims:
        verification = Verification(
            verdict="RETRY",
            invalid_citations=invalid,
            reason="Every material claim needs supporting evidence from this investigation.",
            retry_query="Find direct records and applicable policy supporting the disputed claims.",
        )
    elif state.get("retry_count", 0) and not state.get("retrieval_changed", True):
        verification = Verification(
            verdict="ABSTAIN",
            reason="The targeted retry found no new evidence to resolve the earlier gap.",
        )
    else:
        verification = _structured(
            services.models.main,
            Verification,
            prompts.VERIFY,
            {**_evidence_payload(state), "diagnosis": diagnosis.model_dump()},
        )
        if verification.verdict == "PASS" and (
            verification.invalid_citations
            or verification.unsupported_claims
            or verification.contradictions
        ):
            verification.verdict = "ABSTAIN"
            verification.reason = "The verifier reported unresolved claims or contradictions."
    if verification.verdict == "RETRY" and state.get("retry_count", 0) >= 1:
        verification.verdict = "ABSTAIN"
        verification.reason = "Evidence remains insufficient after the single retrieval retry."
    return {
        "verification": verification,
        "requires_human": is_security or verification.verdict == "ESCALATE",
        "escalation_reason": verification.reason if verification.verdict == "ESCALATE" else None,
        "trace": _trace(
            state,
            "verify",
            started,
            {"verdict": verification.verdict, "invalid_citations": verification.invalid_citations},
        ),
    }


def rewrite_query(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    verification = state["verification"]
    rewritten = _structured(
        services.models.fast,
        RewrittenQuery,
        prompts.REWRITE,
        json.dumps(
            {"old_query": state["rewritten_query"], "verification": verification.model_dump()}
        ),
    )
    return {
        "rewritten_query": rewritten.query,
        "retry_count": state.get("retry_count", 0) + 1,
        "trace": _trace(state, "rewrite_query", started, {"query": rewritten.query}),
    }


def finalize(state: InvestigationState, services: Services) -> dict[str, Any]:
    started = time.perf_counter()
    verdict = state["verification"].verdict
    if verdict == "PASS":
        status = InvestigationStatus.READY_FOR_REVIEW
    elif verdict == "ESCALATE":
        status = InvestigationStatus.ESCALATED
    elif verdict == "ABSTAIN" and state["diagnosis"].missing_facts:
        status = InvestigationStatus.NEEDS_CUSTOMER_INFO
    else:
        status = InvestigationStatus.INSUFFICIENT_EVIDENCE
    if verdict == "PASS":
        safe_draft = state["draft_response"]
    elif verdict == "ESCALATE":
        safe_draft = "This case needs specialist review before a response can be approved."
    else:
        safe_draft = "More information is needed before we can provide a supported response."
    trace = _trace(state, "finalize", started, {"status": status.value})
    result = {
        "run_id": state["run_id"],
        "thread_id": state["thread_id"],
        "ticket_id": state["ticket_id"],
        "status": status.value,
        "prompt_version": prompts.PROMPT_VERSION,
        "route": state["route"].model_dump(),
        "attempt_count": state.get("retry_count", 0) + 1,
        "diagnosis": state["diagnosis"].model_dump(),
        "recommendation": state["recommendation"],
        "draft_response": safe_draft,
        "unverified_draft": state["draft_response"] if verdict != "PASS" else None,
        "missing_sources": state.get("missing_sources", []),
        "confidence": state["confidence"],
        "citations": state["citations"],
        "evidence": [item.model_dump() for item in state["evidence"]],
        "graph_paths": state["graph_evidence"],
        "memory_ids": [str(item.get("id", "")) for item in state["memories"]],
        "requires_human": state.get("requires_human", False),
        "escalation_reason": state.get("escalation_reason"),
        "verification": state["verification"].model_dump(),
        "trace": [item.model_dump() for item in trace],
    }
    services.mongo.save_run(result)
    return {"status": status.value, "draft_response": safe_draft, "trace": trace}


def verification_route(state: InvestigationState) -> str:
    if state["verification"].verdict == "RETRY" and state.get("retry_count", 0) < 1:
        return "rewrite_query"
    return "finalize"


def _structured(model: Any, schema: type[BaseModel], system: str, payload: str | dict) -> Any:
    try:
        return model.with_structured_output(schema).invoke(
            [
                SystemMessage(content=prompts.UNTRUSTED_RULE + "\n" + system),
                HumanMessage(
                    content="BEGIN_UNTRUSTED_DATA\n"
                    + (json.dumps(payload, default=str) if isinstance(payload, dict) else payload)
                    + "\nEND_UNTRUSTED_DATA"
                ),
            ]
        )
    except Exception as exc:
        model_name = getattr(model, "model_name", getattr(model, "model", "configured model"))
        raise RuntimeError(
            f"OpenAI model '{model_name}' failed. Change the corresponding OPENAI_*_MODEL setting if unavailable."
        ) from exc


def _structured_items(state: InvestigationState) -> list[EvidenceItem]:
    items = []
    for kind, record, identifier in (
        ("Ticket", state.get("ticket"), state["ticket_id"]),
        ("Customer", state.get("customer"), state["ticket"]["customer_id"]),
        ("Order", state.get("order"), state["ticket"].get("order_id")),
        ("Payment", state.get("payment"), (state.get("payment") or {}).get("payment_id")),
    ):
        if not record or not identifier:
            continue
        text = f"Exact {kind.lower()} record: {json.dumps(record, default=str, sort_keys=True)}"
        items.append(
            EvidenceItem(
                citation_id=f"[MongoDB:{identifier}]",
                chunk_id=f"mongodb:{identifier}",
                source_id=f"MongoDB:{kind}",
                source_path=f"mongodb://{kind.lower()}s/{identifier}",
                source_type="structured",
                text=text,
                entity_ids=[str(identifier)],
                ticket_category=state["ticket"]["category"],
                content_hash=content_hash(text),
                score=1.0,
            )
        )
    return items


def _memory_items(state: InvestigationState) -> list[EvidenceItem]:
    output = []
    for index, memory in enumerate(state.get("memories", [])):
        memory_id = str(memory.get("id", f"memory-{index}"))
        if memory.get("metadata", {}).get("approved") is not True:
            continue
        scope = memory.get("scope", state["ticket"]["customer_id"])
        text = "Historical approved context; not proof of the current cause. " + str(
            memory.get("memory", memory.get("text", ""))
        )
        output.append(
            EvidenceItem(
                citation_id=f"[Mem0:{scope}:{memory_id}]",
                chunk_id=f"memory:{memory_id}",
                source_id="Mem0",
                source_path="mem0://approved-memory",
                source_type="memory",
                text=text,
                entity_ids=[state["ticket"]["customer_id"]],
                ticket_category=state["ticket"]["category"],
                service=memory.get("metadata", {}).get("service"),
                content_hash=content_hash(text),
                score=float(memory.get("score", 1.0)),
            )
        )
    return output


def _inspect_screenshot(state: InvestigationState, services: Services) -> EvidenceItem | None:
    source = state["ticket"].get("screenshot_path")
    if not source:
        return None
    path = allowed_source(Path(source), [Path("demo_data/shopflow/screenshots")], {".png"})
    description = describe_screenshot(path, services.models.fast)
    text = (
        f"Fresh screenshot inspection. Screen: {description.screen_name}. "
        f"Issue: {description.issue_type}. Visible text: {'; '.join(description.visible_text)}. "
        f"Error code: {description.error_code or 'none'}. {description.concise_description}"
    )
    return EvidenceItem(
        citation_id=f"[{path.name}]",
        chunk_id=f"screenshot-live:{path.stem}",
        source_id=path.stem,
        source_path=path.as_posix(),
        source_type="screenshot",
        text=text,
        entity_ids=[description.error_code] if description.error_code else [],
        ticket_category=state["ticket"]["category"],
        content_hash=content_hash(text),
        score=1.0,
    )


def _rerank(items: list[EvidenceItem], query: str, model: Any) -> list[EvidenceItem]:
    if len(items) <= 8:
        return items
    candidates = [
        {"chunk_id": item.chunk_id, "source_type": item.source_type, "text": item.text[:1500]}
        for item in items
    ]
    result = _structured(
        model,
        RankedEvidence,
        prompts.RERANK,
        {"query": query, "candidates": candidates},
    )
    by_id = {item.chunk_id: item for item in items}
    ranked = [by_id[key] for key in dict.fromkeys(result.chunk_ids) if key in by_id]
    return ranked[:8]


def _merge_graphs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    nodes = {item["id"]: item for item in [*left.get("nodes", []), *right.get("nodes", [])]}
    edges = {
        (item["source"], item["target"], item["type"]): item
        for item in [*left.get("edges", []), *right.get("edges", [])]
    }
    return {"nodes": list(nodes.values())[:50], "edges": list(edges.values())[:80]}


def _context_payload(state: InvestigationState) -> dict[str, Any]:
    return {
        "question": state.get("user_question"),
        "ticket": state.get("ticket"),
        "customer": state.get("customer"),
        "order": state.get("order"),
        "payment": state.get("payment"),
        "previous_tickets": state.get("previous_tickets", []),
        "approved_memories": state.get("memories", []),
    }


def _evidence_payload(state: InvestigationState) -> dict[str, Any]:
    return {
        "question": state.get("user_question"),
        "ticket_id": state["ticket_id"],
        "missing_sources": state.get("missing_sources", []),
        "context_overflow": state.get("context_overflow", False),
        "evidence": [item.model_dump(exclude={"parent_text"}) for item in state["evidence"]],
    }


def _trace(
    state: InvestigationState, node: str, started: float, detail: dict[str, Any]
) -> list[WorkflowTrace]:
    safe_detail = {
        key: value
        for key, value in detail.items()
        if not re.search(r"prompt|secret|key", key, re.I)
    }
    return [
        *state.get("trace", []),
        WorkflowTrace(
            node=node, duration_ms=round((time.perf_counter() - started) * 1000), detail=safe_detail
        ),
    ]
