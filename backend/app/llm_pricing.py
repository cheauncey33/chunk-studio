"""Central LLM price registry.

Business code must not branch on model names. Rates live in env / settings /
an optional JSON file. Historical bills keep the snapshot stored on each
usage event; changing this registry never rewrites old rows.

Currency is USD. Costs are integer microunits: 1 USD = 1_000_000 microunits.
All arithmetic uses Decimal. The database never stores float money.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import json
import os
from pathlib import Path
from typing import Any

from . import db


MICROUNITS_PER_USD = 1_000_000
TOKENS_PER_MILLION = Decimal("1000000")
DEFAULT_CURRENCY = "USD"

_ZERO = Decimal("0")


@dataclass(frozen=True)
class ModelPrice:
    key: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal | None
    cache_write_per_million: Decimal | None
    reasoning_per_million: Decimal | None = None

    def snapshot(self) -> dict[str, str]:
        payload = {
            "key": self.key,
            "currency": self.currency,
            "input_per_million": _decimal_text(self.input_per_million),
            "output_per_million": _decimal_text(self.output_per_million),
        }
        if self.cache_read_per_million is not None:
            payload["cache_read_per_million"] = _decimal_text(self.cache_read_per_million)
        if self.cache_write_per_million is not None:
            payload["cache_write_per_million"] = _decimal_text(self.cache_write_per_million)
        if self.reasoning_per_million is not None:
            payload["reasoning_per_million"] = _decimal_text(self.reasoning_per_million)
        return payload


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite() or parsed < _ZERO:
        return None
    return parsed


def _normalize_model_key(provider: str, model: str) -> tuple[str, ...]:
    provider_key = str(provider or "").strip().lower()
    model_key = str(model or "").strip()
    lowered = model_key.lower()
    keys: list[str] = []
    if provider_key and model_key:
        keys.append(f"{provider_key}/{model_key}")
        keys.append(f"{provider_key}/{lowered}")
    if "/" in model_key:
        keys.append(model_key)
        keys.append(lowered)
        _, _, bare = model_key.partition("/")
        if bare:
            keys.append(bare)
            keys.append(bare.lower())
    elif model_key:
        keys.append(model_key)
        keys.append(lowered)
    # Preserve order, drop empties/dupes.
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def _parse_registry(raw: Any) -> dict[str, ModelPrice]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    registry: dict[str, ModelPrice] = {}
    for key, spec in raw.items():
        name = str(key or "").strip()
        if not name or not isinstance(spec, dict):
            continue
        input_price = _as_decimal(spec.get("input_per_million"))
        output_price = _as_decimal(spec.get("output_per_million"))
        if input_price is None or output_price is None:
            continue
        currency = str(spec.get("currency") or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY
        registry[name] = ModelPrice(
            key=name,
            currency=currency,
            input_per_million=input_price,
            output_per_million=output_price,
            cache_read_per_million=_as_decimal(spec.get("cache_read_per_million")),
            cache_write_per_million=_as_decimal(spec.get("cache_write_per_million")),
            reasoning_per_million=_as_decimal(spec.get("reasoning_per_million")),
        )
        lowered = name.lower()
        registry.setdefault(lowered, registry[name])
    return registry


def load_pricing_registry() -> dict[str, ModelPrice]:
    """Load rates from env, then an optional file, then the settings table."""
    env_json = str(os.environ.get("LLM_PRICING_JSON") or "").strip()
    if env_json:
        registry = _parse_registry(env_json)
        if registry:
            return registry
    path_raw = str(os.environ.get("LLM_PRICING_PATH") or "").strip()
    if path_raw:
        path = Path(path_raw)
        try:
            if path.is_file():
                registry = _parse_registry(path.read_text(encoding="utf-8"))
                if registry:
                    return registry
        except OSError:
            pass
    try:
        stored = db.get_setting("llm.pricing_json", "")
    except Exception:
        stored = ""
    return _parse_registry(stored)


def lookup_price(provider: str, model: str, *, registry: dict[str, ModelPrice] | None = None) -> ModelPrice | None:
    prices = registry if registry is not None else load_pricing_registry()
    for key in _normalize_model_key(provider, model):
        found = prices.get(key)
        if found is not None:
            return found
    return None


def _token_cost(tokens: int | None, rate: Decimal | None) -> Decimal:
    if tokens is None or rate is None or tokens <= 0:
        return _ZERO
    return (Decimal(int(tokens)) * rate) / TOKENS_PER_MILLION


def compute_cost_microunits(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    price: ModelPrice,
) -> int:
    """Return integer USD microunits for one normalized usage row.

    Reasoning tokens are billed only when the registry defines
    ``reasoning_per_million``. Otherwise they are assumed to already sit
    inside ``output_tokens`` (OpenAI-compatible / Zhipu / Qwen default).
    """
    total_usd = _ZERO
    total_usd += _token_cost(input_tokens, price.input_per_million)
    total_usd += _token_cost(output_tokens, price.output_per_million)
    if price.cache_read_per_million is not None:
        total_usd += _token_cost(cache_read_tokens, price.cache_read_per_million)
    if price.cache_write_per_million is not None:
        total_usd += _token_cost(cache_write_tokens, price.cache_write_per_million)
    if price.reasoning_per_million is not None:
        total_usd += _token_cost(reasoning_tokens, price.reasoning_per_million)
    microunits = (total_usd * Decimal(MICROUNITS_PER_USD)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(microunits)
