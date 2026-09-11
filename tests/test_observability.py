from __future__ import annotations

from types import SimpleNamespace

from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier

from app.observability import investigation_trace, mask_otel_spans


def test_disabled_langfuse_adds_no_callbacks() -> None:
    settings = SimpleNamespace(LANGFUSE_ENABLED=False)
    with investigation_trace(
        settings,
        run_id="RUN-TEST",
        thread_id="THREAD-TEST",
        ticket_id="TKT-TEST",
        question_present=True,
    ) as config:
        assert config == {}


def test_langfuse_export_mask_redacts_sensitive_values() -> None:
    identifier = OtelSpanIdentifier(trace_id="a" * 32, span_id="b" * 16)
    span = OtelSpanData(
        trace_id=identifier.trace_id,
        span_id=identifier.span_id,
        parent_span_id=None,
        name="generation",
        instrumentation_scope_name="langfuse-sdk",
        instrumentation_scope_version="4",
        attributes={
            "input": (
                "Contact person@example.com with key sk-exampleSecret123456 and card "
                "4111 1111 1111 1111. data:image/png;base64,SGVsbG8="
            )
        },
        resource_attributes={},
    )

    result = mask_otel_spans(params=MaskOtelSpansParams(spans={identifier: span}))
    masked = result.span_patches[identifier].set_attributes["input"]

    assert "person@example.com" not in masked
    assert "sk-exampleSecret123456" not in masked
    assert "4111 1111 1111 1111" not in masked
    assert "SGVsbG8=" not in masked
