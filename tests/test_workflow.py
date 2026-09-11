from __future__ import annotations

from types import SimpleNamespace

from app.agent.nodes import Services, load_context, plan, verify
from app.agent.prompts import PROMPT_VERSION
from app.agent.workflow import build_workflow
from app.models import Diagnosis, EvidenceItem, TicketCategory
from app.stores.qdrant import content_hash
from tests.conftest import FakeGraph, FakeMemory, FakeModel, FakeMongo, FakeQdrant


def run_with(verdicts: list[str], *, category: str = "PAYMENT", invalid: bool = False):
    mongo = FakeMongo(category=category)
    models = SimpleNamespace(
        fast=FakeModel(category=category),
        main=FakeModel(category=category, verdicts=verdicts, invalid_diagnosis=invalid),
    )
    services = Services(mongo, FakeQdrant(), FakeGraph(), FakeMemory(), models)
    result = build_workflow(services).invoke(
        {
            "run_id": "RUN-TEST",
            "thread_id": "THREAD-TEST",
            "ticket_id": mongo.ticket["ticket_id"],
            "retry_count": 0,
            "trace": [],
        }
    )
    return result, mongo


def test_ticket_classification_structured_output() -> None:
    mongo = FakeMongo()
    services = Services(
        mongo,
        FakeQdrant(),
        FakeGraph(),
        FakeMemory(),
        SimpleNamespace(fast=FakeModel(), main=FakeModel()),
    )
    state = {"ticket_id": "TKT-1001", "run_id": "R", "thread_id": "T", "trace": []}
    loaded = {**state, **load_context(state, services)}
    planned = plan(loaded, services)
    assert planned["route"].category == TicketCategory.PAYMENT
    assert planned["route"].inspect_code


def test_langgraph_pass_path_and_ananya_evidence() -> None:
    result, mongo = run_with(["PASS"])
    assert result["status"] == "READY_FOR_REVIEW"
    assert mongo.runs["RUN-TEST"]["attempt_count"] == 1
    assert mongo.runs["RUN-TEST"]["prompt_version"] == PROMPT_VERSION
    source_types = {item.source_type for item in result["evidence"]}
    assert {"document", "incident", "code", "structured", "memory"} <= source_types
    assert "synchronization" in result["diagnosis"].probable_cause
    assert "payment is lost" not in result["draft_response"].lower()


def test_langgraph_retry_stops_when_evidence_does_not_change() -> None:
    result, mongo = run_with(["RETRY", "PASS"])
    assert result["retry_count"] == 1
    assert mongo.runs["RUN-TEST"]["attempt_count"] == 2
    assert [item.node for item in result["trace"]].count("rewrite_query") == 1
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert "no new evidence" in result["verification"].reason


def test_retry_limit_finishes_with_insufficient_evidence() -> None:
    result, _mongo = run_with(["RETRY", "RETRY"])
    assert result["retry_count"] == 1
    assert result["status"] == "INSUFFICIENT_EVIDENCE"


def test_mandatory_security_escalation() -> None:
    result, _mongo = run_with(["PASS"], category="SECURITY")
    assert result["status"] == "ESCALATED"
    assert result["requires_human"] is True


def test_citation_verification_triggers_retry() -> None:
    result, _mongo = run_with(["PASS"], invalid=True)
    assert result["retry_count"] == 1
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["verification"].invalid_citations == ["[does-not-exist]"]


def test_verifier_rejects_nonexistent_citation_without_model_call() -> None:
    text = "Known fact"
    evidence = EvidenceItem(
        citation_id="[known]",
        chunk_id="known",
        source_id="known",
        source_path="known",
        source_type="document",
        text=text,
        content_hash=content_hash(text),
    )
    state = {
        "ticket": {"category": "PAYMENT"},
        "diagnosis": Diagnosis(
            issue_summary="x",
            probable_cause="x",
            supporting_facts=[],
            recommended_next_action="review",
            risk_level="LOW",
            response_draft="draft",
            citation_ids=["[missing]"],
            confidence=0.5,
        ),
        "evidence": [evidence],
        "trace": [],
    }
    result = verify(state, SimpleNamespace(models=SimpleNamespace(main=None)))
    assert result["verification"].verdict == "RETRY"


def test_security_escalation_takes_priority_over_invalid_citations():
    result, _ = run_with(["PASS"], category="SECURITY", invalid=True)
    assert result["status"] == "ESCALATED"
    assert result["retry_count"] == 0
    assert "specialist review" in result["draft_response"]


def test_missing_required_policy_skips_diagnosis(fake_services, monkeypatch):
    from app.agent.nodes import diagnose, retrieve

    state = {"ticket_id": "TKT-1001", "run_id": "R", "thread_id": "T", "trace": []}
    state.update(load_context(state, fake_services))
    state.update(plan(state, fake_services))
    monkeypatch.setattr(fake_services.qdrant, "get_source_items", lambda source: [])
    state.update(retrieve(state, fake_services))
    fake_services.models.main = None
    state.update(diagnose(state, fake_services))
    result = verify(state, fake_services)
    assert state["missing_sources"] == ["payment_policy"]
    assert result["verification"].verdict == "ABSTAIN"


def test_verifier_cannot_pass_with_unresolved_contradictions(fake_services, monkeypatch):
    from app.agent.nodes import diagnose, retrieve
    from app.models import Verification

    state = {"ticket_id": "TKT-1001", "run_id": "R", "thread_id": "T", "trace": []}
    state.update(load_context(state, fake_services))
    state.update(plan(state, fake_services))
    state.update(retrieve(state, fake_services))
    state.update(diagnose(state, fake_services))
    monkeypatch.setattr(
        "app.agent.nodes._structured",
        lambda *args: Verification(
            verdict="PASS",
            reason="Okay",
            contradictions=["Policy says 5 MB, code says 4 MB"],
        ),
    )
    assert verify(state, fake_services)["verification"].verdict == "ABSTAIN"


def test_new_evidence_can_resolve_single_retry(fake_services, monkeypatch):
    original_search = fake_services.qdrant.search

    def search(query, **kwargs):
        entries = original_search(query, **kwargs)
        if query == "payment callback reconciliation policy incident code":
            entries.append(
                entries[0].model_copy(
                    update={
                        "chunk_id": "new-log",
                        "source_id": "new-log",
                        "text": "New direct log",
                        "content_hash": content_hash("New direct log"),
                    }
                )
            )
        return entries

    monkeypatch.setattr(fake_services.qdrant, "search", search)
    fake_services.models.main.verdicts = ["RETRY", "PASS"]
    result = fake_services.workflow.invoke(
        {
            "ticket_id": "TKT-1001",
            "run_id": "R",
            "thread_id": "T",
            "trace": [],
            "retry_count": 0,
        }
    )
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["retry_count"] == 1
