from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Read .env without overriding existing environment variables.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    OPENAI_API_KEY: SecretStr
    OPENAI_MAIN_MODEL: str = "gpt-5.6-terra"
    OPENAI_FAST_MODEL: str = "gpt-5.6-luna"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536
    OPENAI_MAIN_REASONING_EFFORT: str = "medium"
    OPENAI_FAST_REASONING_EFFORT: str = "low"

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "supportgraph"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_EVIDENCE_COLLECTION: str = "supportgraph_evidence"
    QDRANT_MEMORY_COLLECTION: str = "supportgraph_memories"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: SecretStr = SecretStr("supportgraph-password")
    API_URL: str = "http://127.0.0.1:8000"
    LOG_LEVEL: str = "INFO"
    DEMO_ROOT: Path = Field(default=Path("demo_data/shopflow"))
    DOCLING_ENABLED: bool = True

    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: SecretStr = SecretStr("")
    LANGFUSE_BASE_URL: str = "http://127.0.0.1:3000"
    LANGFUSE_TRACING_ENVIRONMENT: str = "development"
    LANGFUSE_SAMPLE_RATE: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("OPENAI_API_KEY")
    @classmethod
    def require_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY is required; set it in .env")
        return value

    @field_validator("OPENAI_MAIN_REASONING_EFFORT", "OPENAI_FAST_REASONING_EFFORT")
    @classmethod
    def valid_effort(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("reasoning effort must be low, medium, or high")
        return value

    @model_validator(mode="after")
    def require_langfuse_keys_when_enabled(self) -> Settings:
        if not self.LANGFUSE_ENABLED:
            return self
        if not self.LANGFUSE_PUBLIC_KEY.strip():
            raise ValueError("LANGFUSE_PUBLIC_KEY is required when Langfuse is enabled")
        if not self.LANGFUSE_SECRET_KEY.get_secret_value().strip():
            raise ValueError("LANGFUSE_SECRET_KEY is required when Langfuse is enabled")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
