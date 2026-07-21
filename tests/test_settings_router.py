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

    result = settings.update_settings(SettingsUpdate(settings={
        "llm.api_key": "deep…-key",
        "llm.model": "deepseek-v4-flash",
    }))

    assert stored["llm.api_key"] == "deepseek-real-key"
    assert "…" in result["llm.api_key"]


def test_settings_expose_only_deepseek_llm_controls(monkeypatch) -> None:
    monkeypatch.setattr(settings.db, "get_all_settings", lambda: {})

    result = settings.get_settings()

    assert result["llm.base_url"] == "https://api.deepseek.com"
    assert result["llm.model"] == "deepseek-v4-flash"
    assert "llm.provider" not in settings.KNOWN_KEYS
    assert "llm.response_format" not in settings.KNOWN_KEYS
