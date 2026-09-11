from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.main import build_services


@pytest.mark.integration
def test_local_dependencies_are_reachable() -> None:
    if os.getenv("SUPPORTGRAPH_INTEGRATION") != "1":
        pytest.skip("Set SUPPORTGRAPH_INTEGRATION=1 to run local service checks")
    services = build_services(get_settings())
    try:
        assert services.mongo.health()
        assert services.qdrant.health()
        assert services.graph.health()
        assert services.memory.health()
    finally:
        services.mongo.close()
        services.graph.close()
