from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from app.agent.nodes import Services
from app.agent.workflow import build_workflow
from app.api import router
from app.config import Settings, get_settings
from app.ingestion.service import IngestionService
from app.model_factory import Models, build_models
from app.observability import shutdown_langfuse
from app.stores.memory import MemoryStore
from app.stores.mongo import MongoStore
from app.stores.neo4j import Neo4jStore
from app.stores.qdrant import QdrantEvidenceStore


@dataclass(slots=True)
class RuntimeServices:
    settings: Settings
    mongo: Any
    qdrant: Any
    graph: Any
    memory: Any
    models: Models
    workflow: Any = None
    ingestion: Any = None


def build_services(settings: Settings) -> RuntimeServices:
    models = build_models(settings)
    mongo = MongoStore(settings)
    qdrant = QdrantEvidenceStore(settings, models.embeddings)
    graph = Neo4jStore(settings)
    memory = MemoryStore(settings)
    runtime = RuntimeServices(settings, mongo, qdrant, graph, memory, models)
    node_services = Services(mongo=mongo, qdrant=qdrant, graph=graph, memory=memory, models=models)
    runtime.workflow = build_workflow(node_services)
    runtime.ingestion = IngestionService(settings, mongo, qdrant, graph, models.fast)
    return runtime


def create_app(services: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = services or build_services(get_settings())
        application.state.services = runtime
        logging.basicConfig(level=runtime.settings.LOG_LEVEL)
        try:
            yield
        finally:
            shutdown_langfuse(runtime.settings)
            for name in ("mongo", "graph"):
                close = getattr(getattr(runtime, name, None), "close", None)
                if close:
                    close()

    application = FastAPI(
        title="SupportGraph",
        version="0.1.0",
        description="Human-reviewed ShopFlow support investigations",
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
