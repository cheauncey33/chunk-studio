from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import SettingsUpdate
from app.routers import settings


def test_masked_secret_is_not_persisted(monkeypatch) -> None:
    stored = {
        "llm.api_key": "deepseek-real-key",
        "llm.model": "deepseek-v4-flash",
    }
    monkeypatch.setattr(settings.db, "get_all_settings", lambda: dict(stored))
    monkeypatch.setattr(settings.db, "set_setting", stored.__setitem__)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = settings.update_settings(SettingsUpdate(settings={
        "llm.api_key": "deep…-key",
        "llm.model": "deepseek-v4-flash",
    }))

    assert stored["llm.api_key"] == "deepseek-real-key"
    assert "…" in result["settings"]["llm.api_key"]
    assert result["sources"]["llm.api_key"] == "db"


def test_settings_expose_only_deepseek_llm_controls(monkeypatch) -> None:
    monkeypatch.setattr(settings.db, "get_all_settings", lambda: {})
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINERU_TOKEN", raising=False)

    result = settings.get_settings()

    assert result["settings"]["llm.base_url"] == "https://api.deepseek.com"
    assert result["settings"]["llm.model"] == "deepseek-v4-flash"
    assert result["sources"]["llm.base_url"] == "default"
    assert "llm.provider" not in settings.KNOWN_KEYS
    assert "llm.response_format" not in settings.KNOWN_KEYS


def test_mineru_token_from_env_is_shown_masked(monkeypatch) -> None:
    monkeypatch.setattr(settings.db, "get_all_settings", lambda: {})
    monkeypatch.setenv("MINERU_TOKEN", "mineru-secret-token-value-123456")

    result = settings.get_settings()

    assert result["sources"]["mineru.token"] == "env"
    assert "…" in result["settings"]["mineru.token"]
    assert result["settings"]["mineru.base_url"] == "https://mineru.net"
    assert result["sources"]["mineru.base_url"] == "default"


def test_env_sourced_mineru_token_is_not_overwritten_by_masked_save(monkeypatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(settings.db, "get_all_settings", lambda: dict(stored))
    monkeypatch.setattr(settings.db, "set_setting", stored.__setitem__)
    monkeypatch.setenv("MINERU_TOKEN", "mineru-secret-token-value-123456")

    settings.update_settings(SettingsUpdate(settings={
        "mineru.token": "mine…456",
        "mineru.base_url": "https://mineru.net",
    }))

    assert "mineru.token" not in stored
    assert stored["mineru.base_url"] == "https://mineru.net"
