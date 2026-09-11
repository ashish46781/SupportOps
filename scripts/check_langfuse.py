from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.observability import get_langfuse, langfuse_status  # noqa: E402


def main() -> None:
    settings = get_settings()
    if not settings.LANGFUSE_ENABLED:
        raise RuntimeError("Langfuse is disabled; set LANGFUSE_ENABLED=true in .env")
    if langfuse_status(settings) != "ready":
        raise RuntimeError(f"Langfuse is not ready at {settings.LANGFUSE_BASE_URL}")

    client = get_langfuse(settings)
    if client is None or not client.auth_check():
        raise RuntimeError("Langfuse credentials were rejected by the local server")

    trace_id = client.create_trace_id(seed=f"local-smoke:{uuid.uuid4()}")
    with client.start_as_current_observation(
        name="supportgraph-local-smoke",
        as_type="span",
        trace_context={"trace_id": trace_id},
        input={"component": "local-langfuse"},
    ) as span:
        span.update(output={"status": "ready"})
    client.flush()
    print(f"Langfuse is ready and authenticated: {client.get_trace_url(trace_id=trace_id)}")


if __name__ == "__main__":
    main()
