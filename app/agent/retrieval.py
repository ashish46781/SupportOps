from __future__ import annotations

from collections import Counter

from app.models import EvidenceItem
from app.stores.qdrant import content_hash

SERVICES = {
    "PAYMENT": "PaymentService",
    "REFUND": "RefundService",
    "LOGIN": "AuthenticationService",
    "IMAGE_UPLOAD": "UploadService",
    "COUPON": "CouponService",
    "SECURITY": "PaymentService",
}
REQUIRED_SOURCES = {
    "PAYMENT": ("payment_policy",),
    "REFUND": ("refund_policy", "payment_policy"),
    "LOGIN": ("account_security_policy",),
    "IMAGE_UPLOAD": ("troubleshooting_guide",),
    "COUPON": ("support_faq",),
    "SECURITY": ("account_security_policy", "payment_policy", "escalation_policy"),
}
MAX_CONTEXT_CHARS = 32_000


def categories(state: dict) -> set[str]:
    return {state["ticket"]["category"], state["route"].category}


def required_sources(state: dict) -> list[str]:
    return sorted(
        {source for category in categories(state) for source in REQUIRED_SOURCES[category]}
    )


def search_services(state: dict) -> list[str]:
    # Only known service names can become search filters.
    planned = set(state["route"].entities) & set(SERVICES.values())
    return sorted(planned | {SERVICES[category] for category in categories(state)})


def fuse_results(results: list[list[EvidenceItem]]) -> list[EvidenceItem]:
    scores: Counter[str] = Counter()
    items: dict[str, EvidenceItem] = {}
    for result in results:
        for rank, item in enumerate(result, start=1):
            scores[item.chunk_id] += 1 / (60 + rank)
            items[item.chunk_id] = item
    return [items[key] for key, _ in scores.most_common()]


def deduplicate(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen_ids: set[str] = set()
    seen_content: set[tuple[str, str]] = set()
    result = []
    for item in items:
        # Preserve independent sources even when they quote the same text.
        content_key = (item.source_id, item.content_hash)
        if item.chunk_id not in seen_ids and content_key not in seen_content:
            result.append(item)
            seen_ids.add(item.chunk_id)
            seen_content.add(content_key)
    return result


def expand_parents(items: list[EvidenceItem]) -> list[EvidenceItem]:
    expanded = []
    for item in items:
        if item.parent_id and item.parent_text:
            expanded.append(
                item.model_copy(
                    update={
                        "chunk_id": item.parent_id,
                        "text": item.parent_text,
                        "content_hash": content_hash(item.parent_text),
                    }
                )
            )
        else:
            expanded.append(item)
    return deduplicate(expanded)


def evidence_package(
    required: list[EvidenceItem], optional: list[EvidenceItem]
) -> tuple[list[EvidenceItem], bool]:
    kept = expand_parents(required)
    used = sum(len(item.text) for item in kept)
    if used > MAX_CONTEXT_CHARS:
        # Truncating required policy text could remove an exception.
        return [], True
    for item in expand_parents(optional):
        if any(existing.chunk_id == item.chunk_id for existing in kept):
            continue
        if used + len(item.text) <= MAX_CONTEXT_CHARS:
            kept.append(item)
            used += len(item.text)
    return kept, False


def graph_chunk_ids(graph: dict) -> list[str]:
    ids = []
    for node in graph.get("nodes", []):
        value = node.get("chunk_id")
        if value and value != "seed" and value not in ids:
            ids.append(value)
    for edge in graph.get("edges", []):
        value = edge.get("chunk_id")
        if value and value != "seed" and value not in ids:
            ids.append(value)
    return ids[:12]
