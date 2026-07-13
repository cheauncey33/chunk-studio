from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_report_audit_workflow import (
    _load_manual_knowledge_rules,
    _select_manual_knowledge_rules,
)


def test_manual_knowledge_rules_include_total_loss_formula() -> None:
    rules = _load_manual_knowledge_rules()

    assert rules["scope"] == "knowledge_base_manual_rules"
    assert rules["status"] == "project_owner_approved_pending_domain_review"

    by_id = {rule["rule_id"]: rule for rule in rules["rules"]}
    total_loss = by_id["transformer_total_loss_sum_v1"]

    assert total_loss["rule_type"] == "derived_numeric_formula"
    assert "P总 = P0 + Pk" in total_loss["rule_text"]
    assert total_loss["allowed_use"]
    assert total_loss["not_allowed_use"]


def test_manual_knowledge_rules_are_valid_json() -> None:
    path = ROOT / "evaluation" / "manual_knowledge_rules_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert isinstance(payload["rules"], list)


def test_total_loss_rule_is_selected_only_for_total_loss_cases() -> None:
    rules = _load_manual_knowledge_rules()
    total_loss_case = {
        "test_item": {"project_name": "短路阻抗和负载损耗测量"},
        "reported_requirement": {"text": "总损耗P总(kW):≤3.985"},
    }
    no_load_case = {
        "test_item": {"project_name": "空载损耗和空载电流测量"},
        "reported_requirement": {"text": "空载电流I0(%):≤0.16(1+30%)"},
    }

    assert [
        rule["rule_id"]
        for rule in _select_manual_knowledge_rules(rules, total_loss_case)["rules"]
    ] == ["transformer_total_loss_sum_v1"]
    assert _select_manual_knowledge_rules(rules, no_load_case)["rules"] == []
