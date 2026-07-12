from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_report_test_items import _parse_json_object, _validate_report


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

