"""Settings: LLM / OCR config stored as key/value. Export bundle too."""
from __future__ import annotations

import json

from fastapi import APIRouter

from .. import db
from ..models import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])

# Keys we recognize. The frontend renders known keys into forms; unknown keys
# are still preserved round-trip.
KNOWN_KEYS = [
    "llm.base_url", "llm.api_key", "llm.model", "llm.response_format",
    "mineru.token", "mineru.base_url", "mineru.model_version",
    "ocr.mode", "ocr.base_url", "ocr.token", "ocr.headers",
    "ocr.auto_on_create", "ocr.max_concurrency",
    "ocr.request_template", "ocr.response_text_path",
    "ocr.async_mode", "ocr.submit_url", "ocr.poll_url", "ocr.result_url",
]


@router.get("")
def get_settings():
    s = db.get_all_settings()
    # mask api_key / token for display
    masked = dict(s)
    for k in ("llm.api_key", "ocr.token"):
        if k in masked and masked[k]:
            v = masked[k]
            masked[k] = v[:4] + "…" + v[-4:] if len(v) > 8 else "••••"
    return masked


@router.put("")
def update_settings(body: SettingsUpdate):
    for k, v in body.settings.items():
        # never persist the masked sentinel back
        if k in ("llm.api_key", "ocr.token") and v.endswith("…"):
            continue
        db.set_setting(k, v)
    return get_settings()
