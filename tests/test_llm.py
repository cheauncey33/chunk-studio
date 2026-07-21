from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import llm


def test_chat_json_uses_deepseek_settings(monkeypatch) -> None:
    settings = {
        "llm.api_key": "deepseek-key",
        "llm.base_url": "https://deepseek.example/v1",
        "llm.model": "deepseek-v4-flash",
    }
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": settings.get(key, default))
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)

    result = llm.chat_json([{"role": "user", "content": "return JSON"}])

    assert result == {"ok": True}
    assert captured["url"] == "https://deepseek.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer deepseek-key"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["thinking"] == {"type": "disabled"}


def test_chat_json_requires_deepseek_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)

    with pytest.raises(RuntimeError, match="DeepSeek API key"):
        llm.chat_json([{"role": "user", "content": "return JSON"}])


def test_public_config_is_deepseek_only_and_never_contains_secret(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)

    result = llm.public_config()

    assert result["provider"] == "deepseek"
    assert result["response_format"] == "json_object"
    assert result["thinking"] == "disabled"
    assert "api_key" not in result
