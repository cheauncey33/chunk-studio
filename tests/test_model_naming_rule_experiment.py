from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_model_naming_rule import _parse_json_object


def test_parse_json_object_accepts_plain_and_fenced_json() -> None:
    assert _parse_json_object('{"raw_model":"S20"}') == {"raw_model": "S20"}
    assert _parse_json_object('```json\n{"raw_model":"S20"}\n```') == {"raw_model": "S20"}

