from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_retrieval_evidence_reviews import _parse_json_object, _production_query


def test_production_query_keeps_context_item_and_requirement() -> None:
    query = _production_query({
        "sample_context": {"model": "S20", "rated_capacity": "200 kVA"},
        "test_item": {"project_name": "负载损耗测量"},
        "reported_requirement": {"text": "负载损耗Pk(kW): ≤2.185"},
    })
    assert query == "S20 200 kVA 负载损耗测量 负载损耗Pk(kW): ≤2.185"


def test_parse_json_object_accepts_fenced_json() -> None:
    assert _parse_json_object('```json\n{"selected":[]}\n```') == {"selected": []}

