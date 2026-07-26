from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_report_test_items import (
    _normalize_report,
    _parse_json_object,
    _validate_report,
)


def test_normalize_report_drops_items_without_requirements() -> None:
    result = {
        "report_id": "HBJC",
        "items": [
            {
                "item_no": "1",
                "project_name": "空载损耗和空载电流测量",
                "phase": "initial",
                "requirements": [{"requirement_text": "空载损耗P0(kW): ≤0.215"}],
            },
            {
                "item_no": "15",
                "project_name": "仅标题无要求",
                "phase": "initial",
                "requirements": [],
            },
            {
                "item_no": "16",
                "project_name": "空白要求应剔除",
                "phase": "repeat_routine",
                "requirements": [{"requirement_text": "  "}, "绝缘电阻 ≥ 500 MΩ"],
            },
        ],
    }
    _normalize_report(result)
    assert len(result["items"]) == 2
    assert result["items"][0]["item_no"] == "1"
    assert result["items"][1]["requirements"] == [
        {"requirement_text": "绝缘电阻 ≥ 500 MΩ"},
    ]
    _validate_report(result, "HBJC")


def test_validate_report_accepts_required_shape() -> None:
    result = {
        "report_id": "EZC",
        "items": [{
            "item_no": "1",
            "project_name": "空载损耗和空载电流测量",
            "phase": "initial",
            "requirements": [{"requirement_text": "空载损耗P0(kW): ≤0.215", "unit": "kW"}],
        }],
    }
    _validate_report(result, "EZC")


def test_normalize_report_strips_measured_result_field() -> None:
    result = {
        "report_id": "EZC",
        "items": [{
            "item_no": "1",
            "project_name": "空载损耗和空载电流测量",
            "phase": "initial",
            "requirements": [{
                "requirement_text": "空载损耗P0(kW): ≤0.215",
                "unit": "kW",
                "measured_result": "0.207",
            }],
        }],
    }
    _normalize_report(result)
    assert "measured_result" not in result["items"][0]["requirements"][0]
    _validate_report(result, "EZC")


def test_validate_report_rejects_measured_result_field() -> None:
    result = {
        "report_id": "EZC",
        "items": [{
            "item_no": "1",
            "project_name": "空载损耗和空载电流测量",
            "phase": "initial",
            "requirements": [{
                "requirement_text": "空载损耗P0(kW): ≤0.215",
                "unit": "kW",
                "measured_result": "0.207",
            }],
        }],
    }
    with pytest.raises(ValueError, match="result field"):
        _validate_report(result, "EZC")


def test_parse_json_object_accepts_fenced_json() -> None:
    assert _parse_json_object('```json\n{"report_id":"EZC"}\n```') == {"report_id": "EZC"}

