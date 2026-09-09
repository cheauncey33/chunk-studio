"""OpenAI-compatible JSON chat client for generative LLM tasks."""
from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from typing import Any, Callable

import httpx

from . import db
from .llm_usage import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    UsageContext,
    new_request_id,
    normalize_openai_usage,
    record_normalized_usage,
    unknown_usage,
)


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def _first_env(*names: str) -> str:
    """First non-empty environment value. LLM_* is provider-agnostic; DEEPSEEK_* is legacy."""
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


# L0 request retry: transport jitter only (Connect/Read/Timeout). HTTP 429/5xx
# are not retried here; they surface to the caller and may retry at L2.
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
            _first_env("LLM_BASE_URL", "PI_BASE_URL", "DEEPSEEK_BASE_URL")
            or db.get_setting("llm.base_url")
            or DEFAULT_BASE_URL
        ).rstrip("/"),
        "model": (
            model
            or _first_env("LLM_MODEL", "PI_MODEL", "DEEPSEEK_MODEL")
            or db.get_setting("llm.model")
            or DEFAULT_MODEL
        ),
        "temperature": 0,
        "response_format": "json_object",
        "thinking": "disabled",
    }


def resolve_config(*, model: str | None = None) -> dict[str, str]:
    api_key = _first_env(
        "LLM_API_KEY",
        "PI_API_KEY",
        "ZHIPU_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
    ) or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("LLM API key is not configured")
    visible = public_config(model=model)
    return {
        "api_key": api_key,
        "base_url": str(visible["base_url"]),
        "model": str(visible["model"]),
    }


def infer_provider(config: dict[str, str] | None = None, *, base_url: str = "", model: str = "") -> str:
    """Best-effort provider id for metering labels. Not a billing key."""
    visible = config or {}
    base = str(base_url or visible.get("base_url") or "").lower()
    model_id = str(model or visible.get("model") or "").lower()
    if "bigmodel" in base or "zhipu" in base:
        return "zhipu"
    if "dashscope" in base:
        return "dashscope"
    if "deepseek" in base:
        return "deepseek"
    if model_id.startswith("glm"):
        return "zhipu"
    if model_id.startswith("qwen") or model_id.startswith("zhipu/"):
        return "qwen"
    return "openai"


def _meter_openai_response(
    *,
    config: dict[str, str],
    response_json: Any,
    usage_context: UsageContext | None,
    status: str,
    request_id: str | None = None,
) -> None:
    """Best-effort ledger write. Never raises into the chat caller."""
    if usage_context is None:
        return
    try:
        usage = (
            normalize_openai_usage(response_json)
            if status == STATUS_SUCCESS and isinstance(response_json, dict)
            else unknown_usage()
        )
        record_normalized_usage(
            context=usage_context,
            request_id=request_id or new_request_id(),
            usage=usage,
            status=status,
            provider=infer_provider(config),
            model=str(config.get("model") or usage_context.model or ""),
        )
    except Exception:
        return


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
    usage_context: UsageContext | None = None,
) -> str:
    """Plain-text chat completion (no JSON response_format)."""
    config = resolve_config(model=model)
    request_id = new_request_id() if usage_context is not None else None
    response = _post_chat_completions(
        config=config,
        payload=_apply_thinking_fields(
            {
                "model": config["model"],
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            },
            config,
        ),
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError("DeepSeek returned an invalid chat response") from exc
    _meter_openai_response(
        config=config,
        response_json=payload,
        usage_context=usage_context,
        status=STATUS_SUCCESS,
        request_id=request_id,
    )
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
    tool_choice: str = "auto",
    usage_context: UsageContext | None = None,
) -> dict[str, Any]:
    """Return one OpenAI-compatible assistant message with native tool calls."""
    if not tools:
        raise ValueError("tools must not be empty")
    config = resolve_config(model=model)
    request_id = new_request_id() if usage_context is not None else None
    response = _post_chat_completions(
        config=config,
        payload=_apply_thinking_fields(
            {
                "model": config["model"],
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
                "stream": False,
            },
            config,
        ),
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError("DeepSeek returned an invalid tool chat response") from exc
    _meter_openai_response(
        config=config,
        response_json=payload,
        usage_context=usage_context,
        status=STATUS_SUCCESS,
        request_id=request_id,
    )
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
    tool_choice: str = "auto",
    usage_context: UsageContext | None = None,
) -> dict[str, Any]:
    """Stream content/tool-call deltas and return the aggregated message."""
    if not tools:
        raise ValueError("tools must not be empty")
    config = resolve_config(model=model)
    payload = _apply_thinking_fields(
        {
            "model": config["model"],
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "stream": True,
        },
        config,
    )
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = f"{config['base_url']}/chat/completions"
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    stream_usage: dict[str, Any] | None = None
    request_id = new_request_id() if usage_context is not None else None
    try:
        with httpx.stream(url=url, method="POST", headers=headers, json=payload, timeout=timeout) as response:
            if response.status_code != HTTPStatus.OK:
                # httpx.stream() keeps the response body unread. Accessing
                # response.text before read() raises another exception and
                # hides the provider's actual error body from the Agent UI.
                response.read()
                _meter_openai_response(
                    config=config,
                    response_json=None,
                    usage_context=usage_context,
                    status=STATUS_FAILED,
                    request_id=request_id,
                )
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
                if isinstance(chunk.get("usage"), dict):
                    stream_usage = chunk.get("usage")
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
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError(f"DeepSeek streaming connection failed: {exc}") from exc
    _meter_openai_response(
        config=config,
        response_json={"usage": stream_usage} if stream_usage else {},
        usage_context=usage_context,
        status=STATUS_SUCCESS,
        request_id=request_id,
    )
    return {
        "role": "assistant",
        "content": "".join(content_parts),
        "tool_calls": [tool_calls[index] for index in sorted(tool_calls)],
        "finish_reason": finish_reason,
    }


def _thinking_payload() -> dict[str, Any]:
    """Thinking request field; overridable for always-thinking models.

    DeepSeek (default) takes {"type": "disabled"}. Always-thinking endpoints
    (e.g. zhipu GLM-5.3) reject "disabled" and need an effort level instead:
    LLM_THINKING_PAYLOAD='{"type":"enabled","reasoning_effort":"low"}'.
    """
    raw = os.environ.get("LLM_THINKING_PAYLOAD", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"type": "disabled"}


def _uses_dashscope(config: dict[str, str]) -> bool:
    base = str(config.get("base_url") or "").lower()
    model = str(config.get("model") or "").lower()
    return "dashscope" in base or model.startswith("qwen") or model.startswith("zhipu/")


def _is_glm53(model: str) -> bool:
    return "glm-5.3" in str(model or "").lower()


def _glm_reasoning_effort() -> str:
    """GLM-5.3 accepts low/high/max; default low. medium is not a valid level."""
    level = (
        os.environ.get("PI_THINKING_LEVEL")
        or os.environ.get("LLM_THINKING_LEVEL")
        or "low"
    ).strip().lower()
    if level in {"off", "minimal"}:
        return "low"
    if level == "medium":
        return "high"
    if level in {"low", "high", "max"}:
        return level
    return "low"


def _apply_thinking_fields(payload: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    """DashScope uses enable_thinking; official Zhipu/DeepSeek use thinking{}."""
    if _uses_dashscope(config):
        payload.pop("thinking", None)
        if _is_glm53(str(config.get("model") or "")):
            # 百炼 ZHIPU/GLM-5.3-Flash cannot disable thinking.
            payload["enable_thinking"] = True
            payload["reasoning_effort"] = _glm_reasoning_effort()
            return payload
        raw = os.environ.get("LLM_THINKING_PAYLOAD", "").strip()
        enabled = False
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if "enable_thinking" in parsed:
                    enabled = bool(parsed["enable_thinking"])
                elif str(parsed.get("type") or "").lower() in {"enabled", "true"}:
                    enabled = True
        payload["enable_thinking"] = enabled
        return payload
    if _is_glm53(str(config.get("model") or "")):
        # Official Zhipu GLM-5.3: thinking.type only supports enabled.
        payload["thinking"] = {
            "type": "enabled",
            "reasoning_effort": _glm_reasoning_effort(),
        }
        return payload
    payload["thinking"] = _thinking_payload()
    return payload


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
    usage_context: UsageContext | None = None,
) -> dict[str, Any]:
    config = resolve_config(model=model)
    request_id = new_request_id() if usage_context is not None else None
    response = _post_chat_completions(
        config=config,
        payload=_apply_thinking_fields(
            {
                "model": config["model"],
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
            config,
        ),
        timeout=timeout,
    )
    if response.status_code != HTTPStatus.OK:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError(
            f"DeepSeek call failed: status={response.status_code} "
            f"body={response.text[:500]}"
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        parsed = parse_json_object(content)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _meter_openai_response(
            config=config,
            response_json=None,
            usage_context=usage_context,
            status=STATUS_FAILED,
            request_id=request_id,
        )
        raise RuntimeError("DeepSeek returned an invalid JSON response") from exc
    _meter_openai_response(
        config=config,
        response_json=payload,
        usage_context=usage_context,
        status=STATUS_SUCCESS,
        request_id=request_id,
    )
    return parsed


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
