from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.observability import get_langfuse  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a SupportGraph run in local Langfuse")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--wait-seconds", type=int, default=60)
    args = parser.parse_args()

    client = get_langfuse(get_settings())
    if client is None:
        raise RuntimeError("Langfuse is disabled")
    trace_id = client.create_trace_id(seed=args.run_id)
    deadline = time.monotonic() + args.wait_seconds
    observations = []
    scores = []
    while time.monotonic() < deadline:
        observations = client.api.observations.get_many(trace_id=trace_id, limit=100).data
        scores = client.api.scores_v3.get_many_v3(trace_id=trace_id, limit=100).data
        if observations and scores:
            break
        time.sleep(2)

    if not observations:
        raise RuntimeError(f"No Langfuse observations found for {args.run_id}")
    names = sorted({item.name for item in observations})
    score_names = sorted({item.name for item in scores})
    print(
        f"Langfuse run verified: trace={trace_id}; observations={len(observations)}; "
        f"scores={score_names or ['none']}; names={names}"
    )


if __name__ == "__main__":
    main()
