"""Settings: LLM / OCR config stored as key/value. Export bundle too."""
from __future__ import annotations

import os

from fastapi import APIRouter

from .. import db
from ..models import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])

# Keys we recognize. The frontend renders known keys into forms; unknown keys
# are still preserved round-trip.
KNOWN_KEYS = [
    "llm.base_url", "llm.api_key", "llm.model",
    "mineru.token", "mineru.base_url", "mineru.model_version",
    "ocr.mode", "ocr.base_url", "ocr.token", "ocr.headers",
    "ocr.auto_on_create", "ocr.max_concurrency",
    "ocr.request_template", "ocr.response_text_path",
    "ocr.async_mode", "ocr.submit_url", "ocr.poll_url", "ocr.result_url",
    "retrieval.lexical_production_enabled", "retrieval.lexical_shadow_enabled",
    "audit.default_assistant_id",
]

# Match runtime resolution order used by llm.py / adapters/ocr.py.
SECRET_ENV_KEYS = {
    "llm.api_key": ("DEEPSEEK_API_KEY", "env_first"),
    "mineru.token": ("MINERU_TOKEN", "db_first"),
    "ocr.token": (None, "db_first"),
}

DISPLAY_DEFAULTS = {
    "llm.base_url": "https://api.deepseek.com",
    "llm.model": "deepseek-v4-flash",
    "mineru.base_url": "https://mineru.net",
    "mineru.model_version": "vlm",
    "retrieval.lexical_production_enabled": "true",
    "retrieval.lexical_shadow_enabled": "true",
    "audit.default_assistant_id": "assistant_oil_transformer_audit",
}


@router.get("")
def get_settings():
    stored = db.get_all_settings()
    values: dict[str, str] = dict(stored)
    sources: dict[str, str] = {}

    for key, default in DISPLAY_DEFAULTS.items():
        if not str(values.get(key) or "").strip():
            values[key] = default
            sources[key] = "default"
        else:
            sources[key] = "db"

    for key, (env_name, order) in SECRET_ENV_KEYS.items():
        db_value = str(stored.get(key) or "").strip()
        env_value = str(os.environ.get(env_name) or "").strip() if env_name else ""
        if order == "env_first":
            if env_value:
                values[key] = _mask_secret(env_value)
                sources[key] = "env"
            elif db_value:
                values[key] = _mask_secret(db_value)
                sources[key] = "db"
            else:
                values[key] = ""
                sources[key] = "unset"
        else:
            if db_value:
                values[key] = _mask_secret(db_value)
                sources[key] = "db"
            elif env_value:
                values[key] = _mask_secret(env_value)
                sources[key] = "env"
            else:
                values[key] = ""
                sources[key] = "unset"

    return {"settings": values, "sources": sources}


@router.put("")
def update_settings(body: SettingsUpdate):
    for k, v in body.settings.items():
        # never persist the masked sentinel back (covers DB + env-sourced secrets)
        if k in SECRET_ENV_KEYS and _is_masked_secret(v):
            continue
        db.set_setting(k, v)
    return get_settings()


def _mask_secret(value: str) -> str:
    if len(value) > 8:
        return value[:4] + "…" + value[-4:]
    return "••••"


def _is_masked_secret(value: str) -> bool:
    return "…" in value or value == "••••"
