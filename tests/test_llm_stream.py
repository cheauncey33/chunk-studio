from __future__ import annotations

from types import SimpleNamespace

from app import llm


def test_chat_tools_stream_aggregates_tokens_and_tool_call_deltas(monkeypatch) -> None:
    monkeypatch.setattr(llm, "resolve_config", lambda **kwargs: {
        "api_key": "key",
        "base_url": "https://example.test/v1",
        "model": "model-a",
    })
    captured = {}
    lines = [
        'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"search","arguments":"{\\"q\\":"}}]},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\"}"}}]},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]

    class FakeStream:
        def __enter__(self):
            return SimpleNamespace(
                status_code=200,
                text="",
                iter_lines=lambda: lines,
            )

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(llm.httpx, "stream", lambda **kwargs: (captured.update(kwargs) or FakeStream()))
    events = []
    result = llm.chat_tools_stream(
        [{"role": "user", "content": "查"}],
        [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}],
        event_sink=events.append,
    )

    assert result["content"] == "你好"
    assert result["tool_calls"][0]["id"] == "call_1"
    assert result["tool_calls"][0]["function"]["name"] == "search"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"q":"x"}'
    assert any(event["type"] == "token" for event in events)
    assert any(event["type"] == "tool_call_delta" for event in events)
    assert captured["json"]["stream"] is True
