from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.parameter_schema import (
    generic_parameter_schema,
    normalize_extracted_parameters,
    oil_parameter_schema,
    resolve_parameter_schema,
)


def test_resolve_parameter_schema_falls_back_to_oil_fields() -> None:
    resolved = resolve_parameter_schema({})
    assert [field["key"] for field in resolved["fields"]] == [
        "model",
        "rated_capacity",
        "rated_voltage",
        "phase_count",
        "connection_group",
        "cooling_method",
        "insulation_level",
    ]
    assert resolved["allow_extra"] is False


def test_normalize_open_list_keeps_extras_when_allowed() -> None:
    schema = generic_parameter_schema()
    out = normalize_extracted_parameters(
        {
            "parameters": [
                {"key": "model", "value": "S20-M·R-400/10", "unit": ""},
                {"key": "cable_spec", "value": "YJLV22", "unit": "kV"},
            ]
        },
        schema,
    )
    assert out["model"] == "S20-M·R-400/10"
    assert out["cable_spec"] == "YJLV22 kV"


def test_normalize_legacy_flat_map_with_oil_schema() -> None:
    schema = oil_parameter_schema()
    out = normalize_extracted_parameters(
        {
            "model": "S20",
            "rated_capacity": "400 kVA",
            "rated_voltage": "10 kV",
            "phase_count": "三相",
            "connection_group": "Dyn11",
            "cooling_method": "ONAN",
            "insulation_level": "LI75AC35",
            "extra_ignored": "x",
        },
        schema,
    )
    assert out["model"] == "S20"
    assert out["rated_capacity"] == "400 kVA"
    assert "extra_ignored" not in out


def test_normalize_fills_missing_schema_keys() -> None:
    out = normalize_extracted_parameters(
        {"parameters": [{"key": "model", "value": "ABC", "unit": ""}]},
        {
            "version": 1,
            "allow_extra": False,
            "fields": [
                {"key": "model", "label": "型号", "required": True, "hint": ""},
                {"key": "rated_voltage", "label": "额定电压", "required": False, "hint": ""},
            ],
        },
    )
    assert out == {"model": "ABC", "rated_voltage": ""}


def test_normalize_pads_required_schema_keys_when_absent() -> None:
    out = normalize_extracted_parameters(
        {"parameters": [{"key": "rated_voltage", "value": "10", "unit": "kV"}]},
        {
            "version": 1,
            "allow_extra": True,
            "fields": [
                {"key": "model", "label": "型号", "required": True, "hint": ""},
                {"key": "rated_voltage", "label": "额定电压", "required": False, "hint": ""},
            ],
        },
    )
    assert out["model"] == ""
    assert out["rated_voltage"] == "10 kV"
