from __future__ import annotations

import json
from pathlib import Path

from app.ingestion.code import parse_repository
from app.ingestion.documents import extract_pdf

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo_data" / "shopflow"


def test_demo_dataset_integrity() -> None:
    data = json.loads((DEMO / "raw" / "records.json").read_text(encoding="utf-8"))
    assert len(data["customers"]) == 12
    assert len(data["orders"]) == len(data["payments"]) == 19
    assert len(data["tickets"]) == 24
    assert sum(ticket["status"] in {"RESOLVED", "CLOSED"} for ticket in data["tickets"]) == 12
    ananya = next(ticket for ticket in data["tickets"] if ticket["ticket_id"] == "TKT-1001")
    assert ananya["error_code"] == "PAYMENT_SYNC_502"
    order = next(order for order in data["orders"] if order["order_id"] == "ORD-1001")
    payment = next(payment for payment in data["payments"] if payment["order_id"] == "ORD-1001")
    assert (order["status"], payment["status"], payment["amount"]) == ("FAILED", "CAPTURED", 2499)


def test_document_chunks_keep_page_and_source_provenance(monkeypatch) -> None:
    monkeypatch.setattr("app.ingestion.documents._docling_headings", lambda _path: {})
    chunks = extract_pdf(DEMO / "documents" / "payment_policy.pdf", "PAYMENT", "PaymentService")
    assert chunks
    assert all(item.page and item.source_id == "payment_policy" for item in chunks)
    assert any("reconciliation" in item.text.lower() for item in chunks)
    assert all(
        item.parent_id and item.text in item.parent_text and item.source_hash for item in chunks
    )


def test_incident_cache_uses_document_id_and_rejects_partial_index():
    from types import SimpleNamespace

    from app.ingestion.documents import file_sha256
    from app.ingestion.service import IngestionService

    path = DEMO / "documents" / "INC-001_payment_callback_worker.pdf"
    chunks = extract_pdf(path, "PAYMENT", "PaymentService", use_docling=False)
    source = {"sha256": file_sha256(path), "pipeline_version": 2, "chunks": len(chunks)}
    seen = []

    def get_source(source_id):
        seen.append(source_id)
        return source

    service = IngestionService(
        None,
        SimpleNamespace(get_ingested_source=get_source),
        SimpleNamespace(get_source_items=lambda source_id: chunks),
        None,
        None,
    )
    assert service._cached_source(path) == chunks
    assert seen == ["INC-001"]
    source["chunks"] += 1
    assert service._cached_source(path) == []


def test_graph_does_not_link_attribute_call_to_unrelated_local_function():
    from app.ingestion.code import PythonSymbol, code_graph

    def symbol(name, calls):
        return PythonSymbol(
            kind="function",
            name=name,
            qualified_name=f"module.{name}",
            file_path="module.py",
            start_line=1,
            end_line=2,
            signature=name,
            docstring=None,
            source="",
            calls=calls,
        )

    _, edges = code_graph([symbol("save", []), symbol("run", ["client.save"])])
    assert edges["CALLS"] == []


def test_ast_extracts_symbols_calls_and_endpoints() -> None:
    symbols = parse_repository(DEMO / "repository" / "shopflow_api")
    callback = next(item for item in symbols if item.name == "handle_payment_callback")
    assert "schedule_reconciliation" in callback.calls
    assert callback.endpoint == "/callback"
    assert callback.start_line < callback.end_line
    validator = next(item for item in symbols if item.name == "validate_upload")
    assert "uploads.py" in validator.file_path


def test_failed_index_does_not_mark_source_as_complete(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import pytest

    from app.ingestion.service import IngestionService

    mongo, qdrant, graph = Mock(), Mock(), Mock()
    service = IngestionService(SimpleNamespace(DEMO_ROOT=DEMO), mongo, qdrant, graph, None)

    def documents(root):
        service._record_source(root / "documents" / "payment_policy.pdf", "pdf", 1)
        return []

    monkeypatch.setattr(service, "_documents", documents)
    monkeypatch.setattr(service, "_screenshots", lambda root: [])
    monkeypatch.setattr(service, "_code", lambda root: ([], {}, {}))
    qdrant.upsert.side_effect = RuntimeError("index unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        service.run()
    mongo.save_ingested_source.assert_not_called()
    assert service._run_lock.acquire(blocking=False)
    service._run_lock.release()
