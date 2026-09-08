from __future__ import annotations

from typing import Any

from app.audit_caliber import derive, interval_relation, legacy_status, parse_interval


def _requirement(text: str, unit: str | None = None) -> dict[str, Any]:
    return {"text": text, "unit": unit}


def _real(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"discovery_method": "deterministic_comparison"}
    block.update(overrides)
    return block


def test_tighter_than_standard_is_match_with_flag() -> None:
    result = derive(
        _requirement("高压（线）电阻三相不平衡率最大值(%)：≤1.8", "%"),
        _real(raw_value="2.0", numeric_value=2.0, unit="%", operator="le"),
    )

    assert result["verdict"] == "match"
    assert result["kind"] == "within_standard"
    assert result["flags"] == ["tighter_than_standard"]
    assert "C-01" in result["derivation"]["rules"]
    assert result["legacy_status"] == "supported"


def test_looser_bound_is_a_numeric_looser_mismatch() -> None:
    result = derive(
        _requirement("高压（线）电阻三相不平衡率最大值(%)：≤2.2", "%"),
        _real(raw_value="2.0", numeric_value=2.0, unit="%", operator="le"),
    )

    assert result["verdict"] == "mismatch"
    assert result["kind"] == "numeric_looser"
    assert result["derivation"]["comparison"]["tightness"] == "looser"


def test_unit_equivalent_claim_matches_after_conversion() -> None:
    result = derive(
        _requirement("负载损耗（W）：≤270.0", "W"),
        _real(raw_value="0.27", numeric_value=0.27, unit="kW", operator="le"),
    )

    assert (result["verdict"], result["kind"]) == ("match", "unit_equivalent")
    assert result["derivation"]["comparison"]["unit_state"] == "converted"
    assert "C-02" in result["derivation"]["rules"]


def test_display_precision_difference_is_an_exact_match() -> None:
    result = derive(
        _requirement("持续时间（s）：60.001", "s"),
        _real(raw_value="60", numeric_value=60.0, unit="s", operator="eq"),
    )

    assert (result["verdict"], result["kind"]) == ("match", "exact")
    assert "C-04" in result["derivation"]["rules"]


def test_full_width_degree_unit_does_not_block_comparison() -> None:
    result = derive(
        _requirement("参考温度（℃）：75", "℃"),
        _real(raw_value="75", numeric_value=75.0, unit="°C", operator="eq"),
    )

    assert result["derivation"]["comparison"]["unit_state"] == "same"
    assert result["verdict"] == "match"


def test_qualitative_clause_is_out_of_scope() -> None:
    result = derive(
        _requirement("油箱密封应无渗漏"),
        _real(raw_value="无渗漏现象", discovery_method="judgment_comparison"),
    )

    assert result["verdict"] == "out_of_scope"
    assert result["kind"] is None
    assert result["derivation"]["rules"] == ["C-05"]
    assert result["legacy_status"] == "not_audited"


def test_placeholder_requirement_value_is_out_of_scope() -> None:
    result = derive(
        _requirement("内部无放电痕迹：/", "/"),
        _real(raw_value="没有发现内部放电的痕迹", discovery_method="judgment_comparison"),
    )

    assert result["verdict"] == "out_of_scope"
    assert "C-05" in result["derivation"]["rules"]


def test_missing_standard_value_is_standard_not_found() -> None:
    result = derive(
        _requirement("高压对低压及地绝缘电阻(MΩ)：≥1000", "MΩ"),
        _real(unit="MΩ", operator="unknown", discovery_method="judgment_comparison"),
        ["no_authoritative_table_claim"],
    )

    assert (result["verdict"], result["kind"]) == ("unevaluable", "standard_not_found")
    assert result["legacy_status"] == "insufficient_context"


def test_missing_context_field_is_applicability_undetermined() -> None:
    result = derive(
        _requirement("绕组平均温升（K）：≤65", "K"),
        _real(raw_value="65", numeric_value=65.0, unit="K", operator="le"),
        missing_context_fields=["cooling_class"],
    )

    assert (result["verdict"], result["kind"]) == ("unevaluable", "applicability_undetermined")
    assert "C-09" in result["derivation"]["rules"]


def test_unknown_standard_comparator_is_not_treated_as_a_found_bound() -> None:
    result = derive(
        _requirement("空载电流（%）：≤0.18", "%"),
        _real(raw_value="0.18", numeric_value=0.18, unit="%", operator="unknown"),
    )

    assert result["verdict"] == "unevaluable"
    assert result["derivation"]["notes"] == ["standard_operator_unknown"]


def test_immaterial_basis_conflict_does_not_block_the_verdict() -> None:
    result = derive(
        _requirement("联结组标号：Dyn11"),
        _real(raw_value="Dyn11", discovery_method="judgment_comparison"),
        ["conflicting_table_bindings"],
    )

    assert result["verdict"] == "match"
    assert "basis_conflict_immaterial" in result["derivation"]["notes"]


def test_conflicting_basis_blocks_when_the_claim_differs() -> None:
    result = derive(
        _requirement("联结组标号：Dyn11"),
        _real(raw_value="Yyn0", discovery_method="judgment_comparison"),
        ["conflicting_table_bindings"],
    )

    assert (result["verdict"], result["kind"]) == ("unevaluable", "basis_conflict")


def test_concatenated_standard_label_is_not_a_wrong_label_defect() -> None:
    result = derive(
        _requirement("联结组标号：Dyn11"),
        _real(raw_value="Dyn11Yyn0", discovery_method="judgment_comparison"),
    )

    assert (result["verdict"], result["kind"]) == ("unevaluable", "applicability_undetermined")
    assert result["derivation"]["comparison"]["reason"] == "categorical_enumeration_ambiguous"


def test_different_label_is_a_wrong_label_mismatch() -> None:
    result = derive(
        _requirement("联结组标号：Dyn11"),
        _real(raw_value="Yyn0", discovery_method="judgment_comparison"),
    )

    assert (result["verdict"], result["kind"]) == ("mismatch", "wrong_label")


def test_verbatim_requirement_quote_is_a_match() -> None:
    quote = "规定电压比的±0.5%与实际阻抗电压百分数的±1/10中较低者"
    result = derive(
        _requirement(f"主分接电压比偏差：{quote}"),
        _real(raw_value=quote, discovery_method="judgment_comparison"),
    )

    assert (result["verdict"], result["kind"]) == ("match", "exact")
    assert "C-12" in result["derivation"]["rules"]


def test_signed_impulse_band_matches_the_unsigned_standard_band() -> None:
    result = derive(
        _requirement("高压线端全波电压(kV)：-75.0（1±3％）", "kV"),
        _real(raw_value="75", numeric_value=75.0, unit="kV", operator="eq", tolerance="±3%"),
    )

    assert (result["verdict"], result["kind"]) == ("match", "exact")
    assert result["derivation"]["comparison"]["polarity_normalized"] is True


def test_wrong_nominal_in_a_tolerance_band_is_not_bandwidth_exceeded() -> None:
    result = derive(
        _requirement("高压线端全波电压(kV)：-76.0（1±3％）", "kV"),
        _real(raw_value="75", numeric_value=75.0, unit="kV", operator="eq", tolerance="±3%"),
    )

    assert result["verdict"] == "mismatch"
    assert result["kind"] == "numeric_looser"
    assert result["derivation"]["comparison"]["center_relation"] == "different"


def test_degenerate_standard_scalar_inherits_the_reported_bound_direction() -> None:
    result = derive(
        _requirement("空载电流I₀（%）：≤0.18（1+30%）", "%"),
        _real(raw_value="0.80", numeric_value=0.8, unit="%", operator="eq"),
    )

    assert result["verdict"] == "match"
    assert result["flags"] == ["tighter_than_standard"]
    assert result["derivation"]["comparison"]["interval_relation"] == "subset"


def test_reported_tolerance_without_a_recorded_standard_tolerance_is_unevaluable() -> None:
    result = derive(
        _requirement("空载电流I₀（%）：≤0.18（1+30%）", "%"),
        _real(raw_value="0.18", numeric_value=0.18, unit="%", operator="le"),
    )

    assert (result["verdict"], result["kind"]) == ("unevaluable", "applicability_undetermined")
    assert result["derivation"]["comparison"]["reason"] == "standard_tolerance_not_recorded"


def test_recorded_standard_tolerance_expands_the_standard_bound() -> None:
    result = derive(
        _requirement("空载电流Iₒ（%）：≤0.18（1+30%）", "%"),
        _real(
            raw_value="0.18",
            numeric_value=0.18,
            unit="%",
            operator="le",
            tolerance="+30%",
            discovery_method="judgment_comparison",
        ),
    )

    assert (result["verdict"], result["kind"]) == ("match", "exact")


def test_total_loss_limit_claim_keeps_bound_semantics() -> None:
    result = derive(
        _requirement("总损耗P总（kW）：≤2.400", "kW"),
        _real(
            raw_value="3.205",
            numeric_value=3.205,
            unit="kW",
            operator="le",
            scope="空载损耗+负载损耗",
        ),
        ["derived_sum_comparable"],
    )

    assert result["verdict"] == "match"
    assert result["flags"] == ["tighter_than_standard"]
    assert "C-03" in result["derivation"]["rules"]


def test_reported_total_loss_aggregate_requires_equality() -> None:
    result = derive(
        _requirement("总损耗P总（kW）：2.500", "kW"),
        _real(
            raw_value="3.205",
            numeric_value=3.205,
            unit="kW",
            operator="eq",
            scope="空载损耗+负载损耗",
        ),
        ["derived_sum_comparable"],
    )

    assert (result["verdict"], result["kind"]) == ("mismatch", "formula_aggregate")


def test_comparator_direction_conflict_is_a_comparator_flip() -> None:
    result = derive(
        _requirement("绝缘电阻(MΩ)：≤1000", "MΩ"),
        _real(raw_value="1000", numeric_value=1000.0, unit="MΩ", operator="ge"),
    )

    assert (result["verdict"], result["kind"]) == ("mismatch", "comparator_flip")


def test_order_of_magnitude_error_outranks_numeric_looser() -> None:
    result = derive(
        _requirement("负载损耗（W）：≤2700", "W"),
        _real(raw_value="270", numeric_value=270.0, unit="W", operator="le"),
    )

    assert (result["verdict"], result["kind"]) == ("mismatch", "magnitude_error")


def test_workflow_error_yields_no_verdict() -> None:
    result = derive(
        _requirement("绕组平均温升（K）：≤65", "K"),
        _real(raw_value="65", numeric_value=65.0, unit="K", operator="le"),
        ["workflow_error"],
    )

    assert result["verdict"] is None
    assert result["underivable_reason"] == "workflow_error"
    assert result["legacy_status"] is None


def test_unverified_model_value_is_never_scoreable() -> None:
    result = derive(
        _requirement("空载电流（%）：≤0.16"),
        _real(discovery_method="unverified_model_value"),
        ["no_authoritative_table_claim"],
    )

    assert result["scoreable"] is False
    assert result["derivation"]["scoreable_reason"] == "unverified_model_value"


def test_human_confirmation_makes_a_judgment_case_scoreable() -> None:
    result = derive(
        _requirement("高压（线）电阻三相不平衡率最大值(%)：≤1.8", "%"),
        _real(
            raw_value="2.0",
            numeric_value=2.0,
            unit="%",
            operator="le",
            discovery_method="judgment_comparison",
        ),
        review={"status": "human_confirmed"},
    )

    assert result["scoreable"] is True
    assert result["derivation"]["scoreable_reason"] == "human_confirmed"


def test_every_verdict_records_the_rules_it_applied() -> None:
    inputs = [
        (_requirement("参数：≤1.8", "%"), _real(numeric_value=2.0, unit="%", operator="le"), None),
        (_requirement("参数：≤2.2", "%"), _real(numeric_value=2.0, unit="%", operator="le"), None),
        (_requirement("参数：60", "s"), _real(numeric_value=60.0, unit="s", operator="eq"), None),
        (_requirement("应无渗漏"), _real(raw_value="无渗漏"), None),
        (_requirement("参数：≥1000", "MΩ"), _real(operator="unknown"), ["no_authoritative_table_claim"]),
    ]

    for requirement, real, conditions in inputs:
        result = derive(requirement, real, conditions)
        assert result["verdict"] is not None
        assert result["derivation"]["rules"], result


def test_basis_check_adds_flags_without_changing_the_verdict() -> None:
    result = derive(
        _requirement("高压（线）电阻三相不平衡率最大值(%)：≤1.8", "%"),
        _real(raw_value="2.0", numeric_value=2.0, unit="%", operator="le"),
        basis_check=["edition_mismatch"],
    )

    assert result["verdict"] == "match"
    assert "basis_edition_mismatch" in result["flags"]
    assert "C-07" in result["derivation"]["rules"]


def test_parse_interval_reads_the_documented_tolerance_forms() -> None:
    assert parse_interval("15≤t≤60")["low"] == 15.0
    assert parse_interval("15≤t≤60")["high"] == 60.0
    assert parse_interval("3μs~6μs")["form"] == "range"

    symmetric = parse_interval("4.0(1±10%)")
    assert (round(symmetric["low"], 6), round(symmetric["high"], 6)) == (3.6, 4.4)
    assert symmetric["center"] == 4.0

    one_sided = parse_interval("≤0.16(1+30%)")
    assert one_sided["low"] is None
    assert round(one_sided["high"], 6) == 0.208

    assert parse_interval("0.3（构造值：0.3m）")["form"] == "scalar"
    assert parse_interval("120*(额定频率/试验频率),但不少于15s") is None


def test_interval_relation_distinguishes_containment_from_disjointness() -> None:
    standard = {"low": 10.0, "high": 20.0, "form": "range", "center": 15.0}

    assert interval_relation({**standard, "low": 12.0, "high": 18.0}, standard) == "subset"
    assert interval_relation(standard, standard) == "equal"
    assert interval_relation({**standard, "low": 5.0, "high": 25.0}, standard) == "superset"
    assert interval_relation({**standard, "low": 15.0, "high": 25.0}, standard) == "overlap"
    assert interval_relation({**standard, "low": 30.0, "high": 40.0}, standard) == "disjoint"


def test_legacy_status_covers_every_verdict() -> None:
    assert legacy_status("match") == "supported"
    assert legacy_status("mismatch") == "mismatch"
    assert legacy_status("unevaluable") == "insufficient_context"
    assert legacy_status("out_of_scope") == "not_audited"
    assert legacy_status(None) is None
