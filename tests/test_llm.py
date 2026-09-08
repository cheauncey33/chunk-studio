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
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("PI_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("PI_MODEL", raising=False)
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


def test_chat_text_omits_json_response_format(monkeypatch) -> None:
    settings = {
        "llm.api_key": "deepseek-key",
        "llm.base_url": "https://deepseek.example/v1",
        "llm.model": "deepseek-v4-flash",
    }
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("PI_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("PI_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": settings.get(key, default))
    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "普通文本回答"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)

    result = llm.chat_text([{"role": "user", "content": "hi"}])

    assert result == "普通文本回答"
    assert "response_format" not in captured["json"]


def test_chat_json_requires_deepseek_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)

    with pytest.raises(RuntimeError, match="LLM API key"):
        llm.chat_json([{"role": "user", "content": "return JSON"}])


def test_chat_json_retries_transient_connect_error(monkeypatch) -> None:
    settings = {
        "llm.api_key": "deepseek-key",
        "llm.base_url": "https://deepseek.example/v1",
        "llm.model": "deepseek-v4-flash",
    }
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("PI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("PI_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("PI_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": settings.get(key, default))
    monkeypatch.setattr(llm, "_HTTP_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(llm, "_HTTP_RETRY_BASE_DELAY_S", 0)
    monkeypatch.setattr(llm.time, "sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise llm.httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING")
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)

    result = llm.chat_json([{"role": "user", "content": "return JSON"}])

    assert result == {"ok": True}
    assert calls["n"] == 3


def test_chat_json_uses_llm_env_names(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("PI_THINKING_LEVEL", raising=False)
    monkeypatch.setenv("PI_THINKING_LEVEL", "low")
    monkeypatch.delenv("LLM_THINKING_PAYLOAD", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json([{"role": "user", "content": "return JSON"}])
    assert result == {"ok": True}
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert captured["json"]["model"] == "glm-5.3-flash"


def test_public_config_is_deepseek_only_and_never_contains_secret(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-leak")
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)

    result = llm.public_config()

    assert result["provider"] == "deepseek"
    assert result["response_format"] == "json_object"
    assert result["thinking"] == "disabled"


def test_chat_json_uses_dashscope_enable_thinking(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "qwen3.8-flash")
    monkeypatch.delenv("LLM_THINKING_PAYLOAD", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json([{"role": "user", "content": "return JSON"}])
    assert result == {"ok": True}
    body = captured["json"]
    assert captured["url"].startswith("https://dashscope.aliyuncs.com/")
    assert body["model"] == "qwen3.8-flash"
    assert body["enable_thinking"] is False
    assert "thinking" not in body
    assert "api_key" not in result


def test_chat_json_zhipu_official_glm53_uses_thinking_object_low(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("DEEPSEEK_MODEL", "glm-5.3-flash")
    monkeypatch.setenv("PI_THINKING_LEVEL", "low")
    monkeypatch.delenv("LLM_THINKING_PAYLOAD", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json([{"role": "user", "content": "return JSON"}])
    assert result == {"ok": True}
    body = captured["json"]
    assert captured["url"].startswith("https://open.bigmodel.cn/")
    assert body["model"] == "glm-5.3-flash"
    assert body["thinking"] == {"type": "enabled", "reasoning_effort": "low"}
    assert "enable_thinking" not in body


def test_chat_json_dashscope_glm53_forces_thinking_and_maps_medium(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "ZHIPU/GLM-5.3-Flash")
    monkeypatch.setenv("PI_THINKING_LEVEL", "medium")
    monkeypatch.delenv("LLM_THINKING_PAYLOAD", raising=False)
    monkeypatch.setattr(llm.db, "get_setting", lambda key, default="": default)
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "{\"ok\": true}"}}]},
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json([{"role": "user", "content": "return JSON"}])
    assert result == {"ok": True}
    body = captured["json"]
    assert body["model"] == "ZHIPU/GLM-5.3-Flash"
    assert body["enable_thinking"] is True
    assert body["reasoning_effort"] == "high"
    assert "thinking" not in body
