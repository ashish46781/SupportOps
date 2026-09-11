from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "demo_data" / "shopflow" / "evaluation_cases.jsonl"


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not cases:
        raise ValueError("The evaluation needs at least one case")
    seen = set()
    for case in cases:
        key = case["ticket_id"] + ":" + case["question"]
        identifier = case.setdefault("case_id", hashlib.sha256(key.encode()).hexdigest()[:12])
        if identifier in seen:
            raise ValueError(f"Duplicate case ID: {identifier}")
        seen.add(identifier)
        for field in (
            "ticket_id",
            "question",
            "expected_category",
            "expected_source_ids",
            "expected_escalation",
            "forbidden_claims",
        ):
            if field not in case:
                raise ValueError(f"{identifier} is missing {field}")
    return cases


def live_prediction(case: dict, api_url: str) -> dict:
    started = time.perf_counter()
    result = {"case_id": case["case_id"], "ticket_id": case["ticket_id"]}
    try:
        response = httpx.post(
            f"{api_url}/tickets/{case['ticket_id']}/investigate",
            json={"question": case["question"]},
            timeout=300,
        )
        response.raise_for_status()
        result["run"] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Keep the other cases measurable when a service or a single request fails.
        result["error"] = type(exc).__name__
    result["latency_seconds"] = round(time.perf_counter() - started, 3)
    return result


def score(cases: list[dict], results: list[dict]) -> dict:
    by_id = {}
    for result in results:
        if result["case_id"] in by_id:
            raise ValueError("Duplicate result case ID")
        by_id[result["case_id"]] = result
    if set(by_id) != {case["case_id"] for case in cases}:
        raise ValueError("Results must match the evaluation case IDs exactly")
    rows = []
    source_hits = source_total = 0
    for case in cases:
        result = by_id[case["case_id"]]
        if result.get("ticket_id") != case["ticket_id"]:
            raise ValueError("Result ticket does not match its case")
        run = result.get("run", {})
        evidence = run.get("evidence", [])
        sources = {item["source_id"] for item in evidence}
        expected = set(case["expected_source_ids"])
        source_hits += len(expected & sources)
        source_total += len(expected)
        valid_citations = {item["citation_id"] for item in evidence}
        citations = run.get("citations", [])
        valid_ids = {item["chunk_id"] for item in evidence}
        claims = run.get("diagnosis", {}).get("claims", [])
        text = json.dumps(run.get("diagnosis", {})) + run.get("draft_response", "")
        rows.append(
            {
                "case_id": case["case_id"],
                "completed": bool(run)
                and not result.get("error")
                and run.get("status") != "FAILED",
                "category_correct": run.get("route", {}).get("category")
                == case["expected_category"],
                "all_required_sources_found": bool(run) and expected <= sources,
                "citations_resolve": bool(citations) and set(citations) <= valid_citations,
                "claim_references_resolve": bool(claims)
                and all(
                    bool(claim.get("evidence_ids")) and set(claim["evidence_ids"]) <= valid_ids
                    for claim in claims
                ),
                "escalation_correct": bool(run)
                and (run.get("status") == "ESCALATED") == case["expected_escalation"],
                "verifier_passed": run.get("verification", {}).get("verdict") == "PASS",
                "forbidden_phrase_flags": [
                    phrase
                    for phrase in case["forbidden_claims"]
                    if phrase.casefold() in text.casefold()
                ],
            }
        )
    metrics = {
        field + "_rate": mean(row[field] for row in rows)
        for field in (
            "completed",
            "category_correct",
            "all_required_sources_found",
            "citations_resolve",
            "claim_references_resolve",
            "escalation_correct",
            "verifier_passed",
        )
    }
    metrics["required_source_recall"] = source_hits / source_total if source_total else None
    metrics["forbidden_phrase_flag_count"] = sum(len(row["forbidden_phrase_flags"]) for row in rows)
    latencies = [item["latency_seconds"] for item in results if "latency_seconds" in item]
    metrics["mean_latency_seconds"] = mean(latencies) if latencies else None
    return {"metrics": metrics, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate SupportOps cases or measure actual saved runs"
    )
    parser.add_argument("--mode", choices=("offline", "live", "replay"), default="offline")
    parser.add_argument("--api-url", default=os.getenv("API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--results", type=Path, help="JSON results from an earlier live evaluation")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    report = {"mode": args.mode, "case_count": len(cases)}
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "offline":
        report["note"] = "Fixture schema validation only. No predictions or model-quality metrics."
    else:
        if args.mode == "replay":
            if args.results is None:
                parser.error("--results is required for replay")
            results = json.loads(args.results.read_text(encoding="utf-8"))
        else:
            results = []
            results_path = args.output_dir / f"runs_{timestamp}.json"
            for case in cases:
                print(f"Investigating {case['case_id']} ...", flush=True)
                results.append(live_prediction(case, args.api_url.rstrip("/")))
                results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            report["results_file"] = str(results_path)
        report.update(score(cases, results))
        report["note"] = (
            "Reference resolution is not factual support. Verifier agreement is not answer accuracy. "
            "Forbidden phrases are heuristic flags, including possible negations. A person must assess "
            "claim grounding, policy correctness and safe actions against the saved evidence."
        )
    path = args.output_dir / f"evaluation_{args.mode}_{timestamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
