from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_applicability_cases import cases
from evaluate_applicability_cases import prepare_merged_parameters
import evaluate_applicability_cases as evaluation
from inspect_applicability_quotes import inspect


def test_controlled_variants_do_not_reuse_real_report_evidence() -> None:
    replay = {"input": {"case_id": "real", "report_file_id": "real_report",
                        "retrieved_candidates": [{"standard_no": "Q/GDW 12126.4-2024"}]}}
    original = deepcopy(replay)
    fixtures = cases(replay, "scoped_standard")
    assert replay == original
    assert fixtures[0][0]["report_file_id"] == "real_report"
    for payload, expected in fixtures[1:]:
        assert "report_file_id" not in payload
        assert payload["file_scope"] == ["scoped_standard"]
        assert expected["kind"] == "controlled_fixture"
    missing = next(payload for payload, expected in fixtures if expected["id"] == "closure_missing")
    assert missing["parameter_evidence"]["core_closure"]["evidence"] == []
    conflict = next(payload for payload, expected in fixtures if expected["id"] == "material_conflict")
    assert conflict["sample_context"]["core_material"] == "电工钢"
    assert conflict["parameter_evidence"]["model_core_material"]["value"] == "非晶合金"


def test_inspector_exposes_source_without_accepting_paraphrases() -> None:
    replay = {"input": {"naming_rule_context": "<td>电工钢</td><td>—</td>"},
              "output": {"result": {"applicability_checks": [
                  {"source": "命名原文", "evidence_quote": "电工钢——（无字母）"}]}}}
    row = inspect(replay, "")[0]
    assert row["exact_match"] is False
    assert row["visible_match"] is False
    assert "电工钢" in row["nearby_source"]


def test_merged_evaluation_uses_current_node_and_removes_full_naming_input(monkeypatch):
    captured = []
    review = {"version": 2, "fields": {"model": {"state": "confirmed", "value": "S20"}}}
    def decode(parameters, naming, **kwargs):
        captured.append((parameters, naming))
        return {"parameter_review": review}
    monkeypatch.setattr(evaluation.workflow, "_cached_model_decode", decode)
    payload = {"sample_context": {"model": "S20"}, "naming_rule_context": "命名原文",
               "parameter_evidence": {"model": {"source": "report_extraction",
                   "evidence": [{"quote": "型号S20", "quote_verified": True}]}}}
    prepared, trace = prepare_merged_parameters(payload, prompt="prompt", model="test", schema={})
    assert captured[0][0]["_parameter_evidence"]["model"]["quote"] == "型号S20"
    assert captured[0][1] == "命名原文"
    assert prepared["parameter_review"] is review
    assert "naming_rule_context" not in prepared
    assert "naming_rule_context" in payload
    assert trace["output"]["parameter_review"] is review
