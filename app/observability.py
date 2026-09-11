from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import httpx
from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_LANGFUSE_SECRET = re.compile(r"\blf_sk_[A-Za-z0-9_-]{8,}\b")
_INLINE_IMAGE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")


def _redact(value: str) -> str:
    value = _EMAIL.sub("[EMAIL_REDACTED]", value)
    value = _CARD.sub("[PAYMENT_NUMBER_REDACTED]", value)
    value = _OPENAI_KEY.sub("[OPENAI_KEY_REDACTED]", value)
    value = _LANGFUSE_SECRET.sub("[LANGFUSE_KEY_REDACTED]", value)
    return _INLINE_IMAGE.sub("[INLINE_IMAGE_REDACTED]", value)


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
    patches = {}
    for identifier, span in params.spans.items():
        replacements: dict[str, str | Sequence[str]] = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                masked = _redact(value)
                if masked != value:
                    replacements[key] = masked
            elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
                if all(isinstance(item, str) for item in value):
                    masked_items = [_redact(item) for item in value]
                    if masked_items != list(value):
                        replacements[key] = masked_items
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches)


def _enabled(settings: Any) -> bool:
    return bool(getattr(settings, "LANGFUSE_ENABLED", False))


@lru_cache(maxsize=4)
def _build_client(
    public_key: str,
    secret_key: str,
    base_url: str,
    environment: str,
    sample_rate: float,
) -> Langfuse:
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        sample_rate=sample_rate,
        timeout=5,
        flush_at=10,
        flush_interval=2.0,
        mask_otel_spans=mask_otel_spans,
    )


def get_langfuse(settings: Any) -> Langfuse | None:
    if not _enabled(settings):
        return None
    return _build_client(
        settings.LANGFUSE_PUBLIC_KEY,
        settings.LANGFUSE_SECRET_KEY.get_secret_value(),
        settings.LANGFUSE_BASE_URL,
        settings.LANGFUSE_TRACING_ENVIRONMENT,
        settings.LANGFUSE_SAMPLE_RATE,
    )


@contextmanager
def investigation_trace(
    settings: Any,
    *,
    run_id: str,
    thread_id: str,
    ticket_id: str,
    question_present: bool,
) -> Iterator[dict[str, Any]]:
    client = get_langfuse(settings)
    if client is None:
        yield {}
        return

    trace_id = client.create_trace_id(seed=run_id)
    handler = CallbackHandler(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        trace_context={"trace_id": trace_id},
    )
    with (
        client.start_as_current_observation(
            name="support-investigation",
            as_type="agent",
            trace_context={"trace_id": trace_id},
            input={
                "run_id": run_id,
                "ticket_id": ticket_id,
                "question_present": question_present,
            },
        ) as span,
        propagate_attributes(
            trace_name="SupportGraph Investigation",
            session_id=thread_id,
            tags=["supportgraph", "langgraph", "human-review"],
            metadata={"run_id": run_id, "ticket_id": ticket_id},
        ),
    ):
        yield {
            "callbacks": [handler],
            "run_name": "support-investigation",
        }
        span.update(output={"run_id": run_id, "status": "persisted"})


def trace_details(settings: Any, run_id: str) -> dict[str, str]:
    details: dict[str, str] = {}
    try:
        client = get_langfuse(settings)
        if client is None:
            return details
        trace_id = client.create_trace_id(seed=run_id)
        details["langfuse_trace_id"] = trace_id
        details["langfuse_trace_url"] = client.get_trace_url(trace_id=trace_id) or ""
    except Exception:
        logger.warning("Langfuse trace link unavailable for run %s", run_id, exc_info=True)
    return details


def langfuse_status(settings: Any) -> str:
    if not _enabled(settings):
        return "disabled"
    try:
        response = httpx.get(
            f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/api/public/health",
            timeout=1.0,
        )
        return "ready" if response.is_success else "unavailable"
    except httpx.HTTPError:
        return "unavailable"


def score_review(
    settings: Any,
    *,
    run_id: str,
    decision: str,
    approved: bool,
) -> None:
    try:
        client = get_langfuse(settings)
        if client is None:
            return
        trace_id = client.create_trace_id(seed=run_id)
        client.create_score(
            trace_id=trace_id,
            name="human_approval",
            value=1.0 if approved else 0.0,
            data_type="BOOLEAN",
            comment="Human review submitted in SupportGraph.",
        )
        client.create_score(
            trace_id=trace_id,
            name="review_decision",
            value=decision,
            data_type="CATEGORICAL",
        )
    except Exception:
        logger.warning("Langfuse review scoring failed", exc_info=True)


def shutdown_langfuse(settings: Any) -> None:
    client = get_langfuse(settings)
    if client is not None:
        client.shutdown()
