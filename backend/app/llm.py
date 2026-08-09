"""OpenAI-compatible JSON chat client for generative LLM tasks."""
from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from typing import Any, Callable

import httpx

from . import db


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
# Transient TLS / connection drops (e.g. UNEXPECTED_EOF_WHILE_READING).
_HTTP_RETRY_ATTEMPTS = max(1, int(os.environ.get("LLM_HTTP_RETRIES", "4")))
_HTTP_RETRY_BASE_DELAY_S = float(os.environ.get("LLM_HTTP_RETRY_BASE_DELAY_S", "1.0"))
_TRANSIENT_HTTPX_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.TimeoutException,
)


def public_config(*, model: str | None = None) -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "base_url": (
            os.environ.get("DEEPSEEK_BASE_URL")
            or db.get_setting("llm.base_url")
            or DEFAULT_BASE_URL
        ).rstrip("/"),
        "model": (
            model
            or os.environ.get("DEEPSEEK_MODEL")
            or db.get_setting("llm.model")
            or DEFAULT_MODEL
        ),
        "temperature": 0,
        "response_format": "json_object",
        "thinking": "disabled",
    }


def resolve_config(*, model: str | None = None) -> dict[str, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DeepSeek API key is not configured")
    visible = public_config(model=model)
    return {
        "api_key": api_key,
        "base_url": str(visible["base_url"]),
        "model": str(visible["model"]),
    }


def _post_chat_completions(
    *,
    config: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> httpx.Response:
    """POST /chat/completions with retries on transient transport errors."""
    url = f"{config['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(1, _HTTP_RETRY_ATTEMPTS + 1):
        try:
            return httpx.post(url, headers=headers, json=payload, timeout=timeout)
        except _TRANSIENT_HTTPX_ERRORS as exc:
            last_error = exc
            if attempt >= _HTTP_RETRY_ATTEMPTS:
                break
            time.sleep(_HTTP_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
    assert last_error is not None
    raise RuntimeError(
        f"DeepSeek connection failed after {_HTTP_RETRY_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def chat_text(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
) -> str:
    """Plain-text chat completion (no JSON response_format)."""
    config = resolve_config(model=model)
    response = _post_chat_completions(
        config=config,
        payload={
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
            "thinking": {"type": "disabled"},
            "stream": False,
        },
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek returned an invalid chat response") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "").strip()


def chat_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
) -> dict[str, Any]:
    """Return one OpenAI-compatible assistant message with native tool calls."""
    if not tools:
        raise ValueError("tools must not be empty")
    config = resolve_config(model=model)
    response = _post_chat_completions(
        config=config,
        payload={
            "model": config["model"],
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "thinking": {"type": "disabled"},
            "stream": False,
        },
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        message = response.json()["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek returned an invalid tool chat response") from exc
    if not isinstance(message, dict):
        raise RuntimeError("DeepSeek returned an invalid assistant message")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return {
        "role": "assistant",
        "content": str(content or ""),
        "tool_calls": message.get("tool_calls") or [],
    }


def chat_tools_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Stream content/tool-call deltas and return the aggregated message."""
    if not tools:
        raise ValueError("tools must not be empty")
    config = resolve_config(model=model)
    payload = {
        "model": config["model"],
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
        "thinking": {"type": "disabled"},
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = f"{config['base_url']}/chat/completions"
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    try:
        with httpx.stream(url=url, method="POST", headers=headers, json=payload, timeout=timeout) as response:
            if response.status_code != HTTPStatus.OK:
                raise RuntimeError(
                    f"DeepSeek stream failed: status={response.status_code} "
                    f"body={response.text[:500]}"
                )
            for raw_line in response.iter_lines():
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                choices = chunk.get("choices") or []
                if not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, list):
                    content = "".join(
                        str(item.get("text") or "") if isinstance(item, dict) else str(item)
                        for item in content
                    )
                if content:
                    text = str(content)
                    content_parts.append(text)
                    if event_sink:
                        event_sink({"type": "token", "content": text})
                for raw_call in delta.get("tool_calls") or []:
                    if not isinstance(raw_call, dict):
                        continue
                    index = int(raw_call.get("index") or 0)
                    target = tool_calls.setdefault(index, {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if raw_call.get("id"):
                        target["id"] = str(raw_call["id"])
                    function = raw_call.get("function") or {}
                    if isinstance(function, dict):
                        if function.get("name"):
                            target["function"]["name"] += str(function["name"])
                        if function.get("arguments"):
                            target["function"]["arguments"] += str(function["arguments"])
                    if event_sink:
                        event_sink({
                            "type": "tool_call_delta",
                            "index": index,
                            "id": target["id"],
                            "name": target["function"]["name"],
                            "arguments": target["function"]["arguments"],
                        })
    except _TRANSIENT_HTTPX_ERRORS as exc:
        raise RuntimeError(f"DeepSeek streaming connection failed: {exc}") from exc
    return {
        "role": "assistant",
        "content": "".join(content_parts),
        "tool_calls": [tool_calls[index] for index in sorted(tool_calls)],
        "finish_reason": finish_reason,
    }


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
) -> dict[str, Any]:
    config = resolve_config(model=model)
    response = _post_chat_completions(
        config=config,
        payload={
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        },
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        content = response.json()["choices"][0]["message"]["content"]
        return parse_json_object(content)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("DeepSeek returned an invalid JSON response") from exc


def parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text, strict=False)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be a JSON object")
    return parsed
