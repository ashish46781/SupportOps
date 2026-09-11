from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_require_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_accept_injected_values() -> None:
    settings = Settings(OPENAI_API_KEY="test-key", _env_file=None)
    assert settings.OPENAI_MAIN_MODEL == "gpt-5.6-terra"
    assert settings.OPENAI_EMBEDDING_DIMENSIONS == 1536
