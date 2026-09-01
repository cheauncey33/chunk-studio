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
    assert total_loss["formula"]["op"] == "sum"
    assert "总损耗" in total_loss["formula"]["result_aliases"]
    assert [item["id"] for item in total_loss["formula"]["addends"]] == ["p0", "pk"]
    assert total_loss["allowed_use"]
    assert total_loss["not_allowed_use"]


def test_manual_knowledge_rules_are_valid_json() -> None:
    path = ROOT / "evaluation" / "manual_knowledge_rules_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert isinstance(payload["rules"], list)


def test_manual_knowledge_rules_pass_through_to_judge() -> None:
    """All non-empty rules are injected into audit_judge (no per-case filter)."""
    rules = _load_manual_knowledge_rules()
    total_loss_case = {
        "test_item": {"project_name": "短路阻抗和负载损耗测量"},
        "reported_requirement": {"text": "总损耗P总(kW):≤3.985"},
    }
    no_load_case = {
        "test_item": {"project_name": "空载损耗和空载电流测量"},
        "reported_requirement": {"text": "空载电流I0(%):≤0.16(1+30%)"},
    }

    selected_total = _select_manual_knowledge_rules(rules, total_loss_case)
    selected_other = _select_manual_knowledge_rules(rules, no_load_case)

    assert [rule["rule_id"] for rule in selected_total["rules"]] == [
        rule["rule_id"] for rule in rules["rules"]
    ]
    assert [rule["rule_id"] for rule in selected_other["rules"]] == [
        rule["rule_id"] for rule in rules["rules"]
    ]
    assert "transformer_total_loss_sum_v1" in {
        rule["rule_id"] for rule in selected_other["rules"]
    }


def test_select_drops_empty_rule_text() -> None:
    payload = {
        "version": 1,
        "scope": "knowledge_base_manual_rules",
        "rules": [
            {"rule_id": "keep", "rule_text": "P总 = P0 + Pk"},
            {"rule_id": "drop", "rule_text": "  "},
        ],
    }
    selected = _select_manual_knowledge_rules(payload, None)
    assert [rule["rule_id"] for rule in selected["rules"]] == ["keep"]


def test_load_overlays_seed_formula_onto_stale_rule() -> None:
    payload = {
        "version": 1,
        "scope": "knowledge_base_manual_rules",
        "status": "stale_snapshot",
        "rules": [
            {
                "rule_id": "transformer_total_loss_sum_v1",
                "rule_type": "derived_numeric_formula",
                "rule_text": "在变压器损耗审查中，总损耗 P总 可以按空载损耗 P0 与负载损耗 Pk 之和计算，即 P总 = P0 + Pk。",
            }
        ],
    }

    loaded = _load_manual_knowledge_rules(payload)

    assert loaded["rules"][0]["formula"]["op"] == "sum"
    assert "P0" in loaded["rules"][0]["formula"]["addends"][0]["aliases"]
