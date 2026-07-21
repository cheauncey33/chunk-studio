"""OpenAI-compatible JSON chat client for generative LLM tasks."""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from typing import Any

import httpx

from . import db


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


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


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    timeout: float = 180,
) -> dict[str, Any]:
    config = resolve_config(model=model)
    response = httpx.post(
        f"{config['base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        json={
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
