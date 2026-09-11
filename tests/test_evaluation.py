import pytest

from scripts.evaluate import load_cases, score


def test_fixture_validation_does_not_generate_predictions():
    cases = load_cases()
    assert len(cases) == 20
    assert len({case["case_id"] for case in cases}) == 20


def test_empty_predictions_do_not_score_as_valid_citations_or_claims():
    cases = load_cases()
    results = [
        {"case_id": case["case_id"], "ticket_id": case["ticket_id"], "error": "Timeout"}
        for case in cases
    ]
    metrics = score(cases, results)["metrics"]
    assert metrics["completed_rate"] == 0
    assert metrics["citations_resolve_rate"] == 0
    assert metrics["claim_references_resolve_rate"] == 0
    assert metrics["required_source_recall"] == 0


def test_replay_rejects_mismatched_cases():
    with pytest.raises(ValueError, match="match"):
        score(load_cases(), [])
