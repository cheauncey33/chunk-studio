from __future__ import annotations

from types import SimpleNamespace

from app import llm


def test_chat_tools_sends_openai_native_tool_schema(monkeypatch) -> None:
    monkeypatch.setattr(llm, "resolve_config", lambda **kwargs: {
        "api_key": "key",
        "base_url": "https://example.test/v1",
        "model": "model-a",
    })
    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1"}],
            }}]},
        )

    monkeypatch.setattr(llm, "_post_chat_completions", fake_post)
    result = llm.chat_tools(
        [{"role": "user", "content": "查数据"}],
        [{"type": "function", "function": {
            "name": "query_business_data",
            "description": "read only",
            "parameters": {"type": "object"},
        }}],
    )

    assert result["tool_calls"][0]["id"] == "call_1"
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["tools"][0]["function"]["name"] == "query_business_data"
