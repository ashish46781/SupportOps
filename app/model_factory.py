from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import Settings


@dataclass(slots=True)
class Models:
    main: Any
    fast: Any
    embeddings: Any


def build_models(settings: Settings) -> Models:
    api_key = settings.OPENAI_API_KEY.get_secret_value()
    common = {"api_key": api_key, "timeout": 45, "max_retries": 2}
    try:
        main = ChatOpenAI(
            model=settings.OPENAI_MAIN_MODEL,
            reasoning_effort=settings.OPENAI_MAIN_REASONING_EFFORT,
            **common,
        )
        fast = ChatOpenAI(
            model=settings.OPENAI_FAST_MODEL,
            reasoning_effort=settings.OPENAI_FAST_REASONING_EFFORT,
            **common,
        )
        embeddings = OpenAIEmbeddings(
            model=settings.OPENAI_EMBEDDING_MODEL,
            dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
            api_key=api_key,
            timeout=45,
            max_retries=2,
        )
    except Exception as exc:
        raise RuntimeError(
            "Configured OpenAI model construction failed. Check OPENAI_MAIN_MODEL, "
            "OPENAI_FAST_MODEL, and OPENAI_EMBEDDING_MODEL."
        ) from exc
    return Models(main=main, fast=fast, embeddings=embeddings)
