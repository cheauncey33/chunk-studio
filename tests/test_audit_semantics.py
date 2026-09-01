from __future__ import annotations

from app.audit_semantics import (
    annotate_candidates,
    bind_table_row,
    build_deterministic_comparisons,
    classify_evidence_roles,
    compare_claims,
    derived_sum_formulas,
    evaluate_table_claims,
    extract_requirement_claim,
    parse_claim,
    parse_scope,
    resolve_applicability,
)


def _profile(voltage: str = "24/0.4 kV", capacity: str = "630 kVA") -> dict:
    return {
        "from_report": {
            "rated_voltage": voltage,
            "rated_capacity": capacity,
            "sample_name": "样品甲",
        },
        "from_model_decode": {},
    }


def test_profile_parameters_do_not_select_project_or_standard_branch() -> None:
    first = resolve_applicability(_profile(), project_name="项目甲", requirement_text="属性甲: 10")
    second = resolve_applicability(_profile(), project_name="完全不同的项目", requirement_text="另一属性: 20")

    assert first == second
    assert first["state"] == "parameters_only"
    assert first["parameters"]["system_nominal_voltage_kv"] == 24
    assert first["parameters"]["um_kv"] is None
    assert first["resolved"] is False
    assert "branch" not in first


def test_claim_parser_keeps_alphanumeric_category_as_text() -> None:
    claim = parse_claim("连接类别: AX12")

    assert claim["value"]["kind"] == "enum"
    assert claim["value"]["numbers"] == [12]
    assert claim["value"]["operator"] == "eq"


def test_scope_parser_preserves_total_and_per_entity_dimensions() -> None:
    scope = parse_scope("总计12次，每模块3次")

    assert scope["basis"] == "mixed"
    assert scope["totals"] == [12.0]
    assert scope["per_entities"][0]["denominator"] == "模块"
    assert scope["per_entities"][0]["count"] == 3.0


def test_table_binding_requires_one_fully_matching_row() -> None:
    candidate = {
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>持续时间(s)</td></tr>
          <tr><td>12</td><td>20</td></tr>
          <tr><td>24</td><td>30</td></tr>
        </table>
        """,
    }
    binding = bind_table_row(candidate, resolve_applicability(_profile()))

    assert binding is not None
    assert binding["state"] == "matched"
    assert binding["row_index"] == 2
    assert binding["column_values"]["持续时间(s)"] == "30"


def test_table_binding_refuses_multiple_fully_matching_rows() -> None:
    candidate = {
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>限值(kV)</td></tr>
          <tr><td>24</td><td>30</td></tr>
          <tr><td>24</td><td>35</td></tr>
        </table>
        """,
    }
    binding = bind_table_row(candidate, resolve_applicability(_profile()))

    assert binding is not None
    assert binding["state"] == "ambiguous"
    assert binding["matched"] is False
    assert binding["candidate_row_count"] == 2


def test_table_normalizer_expands_multirow_headers_and_rowspans() -> None:
    candidate = {
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td rowspan="2">系统标称电压(kV)</td><td colspan="2">持续条件</td></tr>
          <tr><td>持续时间(s)</td><td>保持时间(s)</td></tr>
          <tr><td rowspan="2">24</td><td>30</td><td>10</td></tr>
          <tr><td>35</td><td>15</td></tr>
        </table>
        """,
    }
    binding = bind_table_row(candidate, resolve_applicability(_profile()))

    assert binding is not None
    assert binding["state"] == "ambiguous"
    assert binding["headers"] == [
        "系统标称电压(kV)",
        "持续条件 / 持续时间(s)",
        "持续条件 / 保持时间(s)",
    ]
    assert binding["candidate_row_count"] == 2


def test_roles_are_linguistic_and_nonexclusive() -> None:
    candidate = {
        "content_type": "section",
        "business_metadata": {"content_type": "section"},
        "text": "当条件成立时，应为50 kV，允许偏差±2%；按照规定方法进行测量。",
    }

    assert classify_evidence_roles(candidate) == [
        "nominal_rule",
        "tolerance_rule",
        "method_rule",
        "applicability_rule",
    ]


def test_generic_scalar_comparison_respects_standard_operator() -> None:
    report = parse_claim("温升: 61 K")
    evidence = parse_claim("温升: 不应超过60 K", evidence_role="nominal_rule")

    result = compare_claims(report, evidence)

    assert result["relation"] == "conflicts"
    assert result["kind"] == "upper_bound"
    assert result["tightness"] == "looser"


def test_compare_claims_converts_kw_and_w_and_ignores_case() -> None:
    report = parse_claim("空载损耗P0(kW):≤0.370")
    evidence = parse_claim("空载损耗p0(W): 370")

    result = compare_claims(report, evidence)

    assert result["unit_normalize"]["state"] == "converted"
    assert result["tightness"] == "equal"
    assert result["relation"] == "supports"


def test_compare_claims_falls_back_when_units_are_incompatible() -> None:
    report = parse_claim("限值: 10 kW")
    evidence = parse_claim("限值: 10 s")

    result = compare_claims(report, evidence)

    assert result["relation"] == "not_comparable"
    assert result["reason"] == "unit_conversion_unavailable"


def test_bound_table_equal_limit_is_authoritative_supported() -> None:
    candidate = {
        "candidate_key": "c02",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>空载损耗P0(kW)</td></tr>
          <tr><td>12</td><td>0.20</td></tr>
          <tr><td>24</td><td>0.370</td></tr>
        </table>
        """,
    }
    candidates = [candidate]
    annotate_candidates(candidates, resolve_applicability(_profile()))
    evaluated = evaluate_table_claims("空载损耗P0(KW):≤0.370", "任意项目名称", candidates)

    assert evaluated["decision"]["mode"] == "programmatic_table"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["comparisons"][0]["tightness"] == "equal"
    assert any(
        node.get("node") == "table_bind" and node.get("state") == "matched"
        for attempt in evaluated["path"]["attempts"]
        for node in attempt["nodes"]
    )


def test_unbound_table_column_records_fallback_path() -> None:
    candidate = {
        "candidate_key": "c09",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>其他参数</td></tr>
          <tr><td>24</td><td>1</td></tr>
        </table>
        """,
    }
    candidates = [candidate]
    annotate_candidates(candidates, resolve_applicability(_profile()))
    evaluated = evaluate_table_claims("空载损耗: ≤0.370 kW", "任意项目名称", candidates)

    assert evaluated["decision"]["mode"] == "fallback_llm"
    assert evaluated["comparisons"] == []
    assert any(
        node.get("node") == "property_match" and node.get("state") in {"unbound", "ambiguous"}
        for attempt in evaluated["path"]["attempts"]
        for node in attempt["nodes"]
    )


def test_bound_table_conflict_uses_property_similarity_not_project_route() -> None:
    candidate = {
        "candidate_key": "c02",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>持续时间(s)</td><td>允许温升(K)</td></tr>
          <tr><td>12</td><td>20</td><td>55</td></tr>
          <tr><td>24</td><td>30</td><td>60</td></tr>
        </table>
        """,
    }
    candidates = [candidate]
    annotate_candidates(candidates, resolve_applicability(_profile()))

    comparisons = build_deterministic_comparisons("持续时间(s): 25", "任意项目名称", candidates)

    assert len(comparisons) == 1
    assert comparisons[0]["source"] == "generic_bound_table_claim"
    assert comparisons[0]["target_column"] == "持续时间(s)"
    assert comparisons[0]["report_value"] == 25
    assert comparisons[0]["standard_value"] == 30
    assert comparisons[0]["relation"] == "different"
    assert comparisons[0]["trace"]["property_similarity"] >= 0.9


def test_extract_requirement_claim_unifies_operator_led_value_and_unit() -> None:
    extracted = extract_requirement_claim(
        "≤ 480",
        unit="W",
        project_name="空载损耗",
    )

    assert extracted["program_ready"] is True
    assert extracted["reason_code"] == "scalar_quantity"
    assert extracted["split_method"] == "project_and_text"
    claim = extracted["claim"]
    assert claim["property"]["source_text"] == "空载损耗"
    assert claim["value"]["numbers"] == [480.0]
    assert claim["value"]["operator"] == "le"
    assert claim["value"]["unit"] == "w"
    assert any(node.get("node") == "inherit_requirement_unit" and node.get("state") == "applied" for node in extracted["nodes"])
    assert any(node.get("node") == "program_ready" and node.get("state") == "yes" for node in extracted["nodes"])


def test_extract_requirement_claim_keeps_separator_property() -> None:
    extracted = extract_requirement_claim(
        "空载损耗P0(kW):≤0.370",
        unit="kW",
        project_name="完全不同的项目名称",
    )

    assert extracted["split_method"] == "separator"
    assert extracted["claim"]["property"]["source_text"] == "空载损耗P0(kW)"
    assert extracted["claim"]["value"]["numbers"] == [0.370]
    assert extracted["claim"]["value"]["operator"] == "le"


def test_extract_requirement_claim_falls_back_when_requirement_is_unstructured() -> None:
    extracted = extract_requirement_claim(
        "绝缘电阻应符合规定",
        unit="/",
        project_name="绝缘电阻",
    )

    assert extracted["program_ready"] is False
    assert extracted["reason_code"] == "unstructured_text"


def test_operator_led_requirement_uses_unified_claim_for_table_compare() -> None:
    candidate = {
        "candidate_key": "c02",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>空载损耗P0(kW)</td></tr>
          <tr><td>12</td><td>0.20</td></tr>
          <tr><td>24</td><td>0.370</td></tr>
        </table>
        """,
    }
    candidates = [candidate]
    annotate_candidates(candidates, resolve_applicability(_profile()))
    evaluated = evaluate_table_claims(
        "≤ 0.370 kW",
        "空载损耗",
        candidates,
        unit="kW",
    )

    assert evaluated["requirement_extraction"]["program_ready"] is True
    assert evaluated["decision"]["mode"] == "programmatic_table"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["path"]["nodes"][0]["node"] == "unify_requirement"


def test_unstructured_requirement_skips_programmatic_table() -> None:
    candidate = {
        "candidate_key": "c02",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>空载损耗P0(kW)</td></tr>
          <tr><td>24</td><td>0.370</td></tr>
        </table>
        """,
    }
    candidates = [candidate]
    annotate_candidates(candidates, resolve_applicability(_profile()))
    evaluated = evaluate_table_claims("绝缘电阻应符合规定", "绝缘电阻", candidates)

    assert evaluated["decision"]["mode"] == "fallback_llm"
    assert evaluated["decision"]["reason_code"] == "requirement_not_program_ready"
    assert evaluated["comparisons"] == []
    assert evaluated["path"]["attempts"] == []


def test_voltage_class_limit_table_binds_without_nominal_voltage_header() -> None:
    limit = {
        "candidate_key": "oil",
        "content_type": "table",
        "business_metadata": {
            "content_type": "table",
            "table_title": "绝缘油耐压规定值",
        },
        "text": """
        <table>
          <tr><td>电压等级kV</td><td>击穿电压kV</td></tr>
          <tr><td>10</td><td>40</td></tr>
          <tr><td>35</td><td>50</td></tr>
        </table>
        """,
    }
    rating = {
        "candidate_key": "rating",
        "content_type": "table",
        "business_metadata": {
            "content_type": "table",
            "table_title": "10 kV 三相油浸式配电变压器性能参数",
        },
        "text": """
        <table>
          <tr><td>额定容量kVA</td><td>电压组合及分接范围 / 高压kV</td><td>空载损耗kW</td></tr>
          <tr><td>400</td><td>10</td><td>0.370</td></tr>
          <tr><td>630</td><td>10</td><td>0.480</td></tr>
        </table>
        """,
    }
    applicability = resolve_applicability(_profile(voltage="10/0.4 kV", capacity="400 kVA"))
    candidates = [rating, limit]
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "击穿电压(kV):≥40",
        "绝缘液试验",
        candidates,
        unit="kV",
        applicability=applicability,
    )

    assert evaluated["decision"]["mode"] == "programmatic_table"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["comparisons"][0]["target_column"] == "击穿电压kV"
    assert evaluated["comparisons"][0]["candidate_key"] == "oil"


def test_imbalance_qualifier_column_uses_parenthetical_not_phase_token() -> None:
    candidate = {
        "candidate_key": "imb",
        "content_type": "table",
        "business_metadata": {
            "content_type": "table",
            "table_title": "变压器绕组电阻不平衡率判定要求",
        },
        "text": """
        <table>
          <tr><td>变压器的要求</td><td>相</td><td>线</td></tr>
          <tr><td>10 kV</td><td>≤ 4 %</td><td>≤ 2 %</td></tr>
          <tr><td>35 kV</td><td>≤ 2 %</td><td>≤ 1 %</td></tr>
          <tr><td>容量 630 kVA 及以上</td><td>≤ 4 %</td><td>≤ 2 %</td></tr>
        </table>
        """,
    }
    applicability = resolve_applicability(_profile())
    candidates = [candidate]
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "高压(线)电阻三相不平衡率最大值(%):≤2",
        "绕组电阻测量",
        candidates,
        unit="%",
        applicability=applicability,
    )

    assert evaluated["decision"]["mode"] == "programmatic_table"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["comparisons"][0]["target_column"] == "线"


def test_rating_table_high_voltage_column_does_not_bind_imbalance_claim() -> None:
    candidate = {
        "candidate_key": "rating",
        "content_type": "table",
        "business_metadata": {
            "content_type": "table",
            "table_title": "10 kV 三相油浸式配电变压器性能参数",
        },
        "text": """
        <table>
          <tr><td>系统标称电压(kV)</td><td>电压组合及分接范围 / 高压kV</td><td>空载损耗kW</td></tr>
          <tr><td>24</td><td>24</td><td>0.370</td></tr>
        </table>
        """,
    }
    applicability = resolve_applicability(_profile())
    candidates = [candidate]
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "高压(线)电阻三相不平衡率最大值(%):≤2",
        "绕组电阻测量",
        candidates,
        unit="%",
        applicability=applicability,
    )

    assert evaluated["decision"]["mode"] == "fallback_llm"
    assert evaluated["comparisons"] == []
    assert any(
        node.get("node") == "property_match" and node.get("state") == "unbound"
        for attempt in evaluated["path"]["attempts"]
        for node in attempt["nodes"]
    )


def _loss_sum_rules() -> dict:
    return {
        "version": 1,
        "scope": "knowledge_base_manual_rules",
        "rules": [
            {
                "rule_id": "transformer_total_loss_sum_v1",
                "rule_type": "derived_numeric_formula",
                "rule_text": "P总 = P0 + Pk",
                "formula": {
                    "op": "sum",
                    "result_aliases": ["总损耗", "P总", "total loss"],
                    "addends": [
                        {"id": "p0", "aliases": ["空载损耗", "P0"]},
                        {"id": "pk", "aliases": ["负载损耗", "Pk"]},
                    ],
                },
            }
        ],
    }


def _loss_table_candidate(*, include_pk: bool = True) -> dict:
    pk_header = "<td>负载损耗Pk(kW)</td>" if include_pk else ""
    pk_12 = "<td>3.20</td>" if include_pk else ""
    pk_24 = "<td>3.615</td>" if include_pk else ""
    return {
        "candidate_key": "loss",
        "content_type": "table",
        "business_metadata": {"content_type": "table"},
        "text": f"""
        <table>
          <tr><td>系统标称电压(kV)</td><td>空载损耗P0(kW)</td>{pk_header}</tr>
          <tr><td>12</td><td>0.20</td>{pk_12}</tr>
          <tr><td>24</td><td>0.370</td>{pk_24}</tr>
        </table>
        """,
    }


def test_derived_sum_formulas_read_structured_sum_rule() -> None:
    formulas = derived_sum_formulas(_loss_sum_rules())
    assert len(formulas) == 1
    assert formulas[0]["op"] == "sum"
    assert "总损耗" in formulas[0]["result_aliases"]
    assert [item["id"] for item in formulas[0]["addends"]] == ["p0", "pk"]


def test_total_loss_uses_p0_plus_pk_formula() -> None:
    candidates = [_loss_table_candidate()]
    applicability = resolve_applicability(_profile())
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "总损耗P总(kW):≤3.985",
        "短路阻抗和负载损耗测量",
        candidates,
        unit="kW",
        applicability=applicability,
        manual_knowledge_rules=_loss_sum_rules(),
    )

    assert evaluated["decision"]["mode"] == "programmatic_formula"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["comparisons"][0]["source"] == "generic_derived_sum"
    assert evaluated["comparisons"][0]["derived_total"] == 3985.0
    assert evaluated["comparisons"][0]["target_column"] == "空载损耗P0(kW) + 负载损耗Pk(kW)"


def test_total_loss_formula_falls_back_when_pk_missing() -> None:
    candidates = [_loss_table_candidate(include_pk=False)]
    applicability = resolve_applicability(_profile())
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "总损耗P总(kW):≤3.985",
        "短路阻抗和负载损耗测量",
        candidates,
        unit="kW",
        applicability=applicability,
        manual_knowledge_rules=_loss_sum_rules(),
    )

    assert evaluated["decision"]["mode"] == "fallback_llm"
    assert evaluated["comparisons"] == []
    assert any(
        node.get("node") == "derived_sum" and node.get("state") == "incomplete"
        for node in evaluated["path"]["nodes"]
    )


def test_no_load_loss_still_uses_table_when_formula_present() -> None:
    candidates = [_loss_table_candidate()]
    applicability = resolve_applicability(_profile())
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "空载损耗P0(KW):≤0.370",
        "空载损耗和空载电流测量",
        candidates,
        unit="kW",
        applicability=applicability,
        manual_knowledge_rules=_loss_sum_rules(),
    )

    assert evaluated["decision"]["mode"] == "programmatic_table"
    assert evaluated["decision"]["status"] == "supported"
    assert evaluated["comparisons"][0]["source"] == "generic_bound_table_claim"
    assert evaluated["comparisons"][0]["target_column"] == "空载损耗P0(kW)"


def test_unrelated_claim_ignores_total_loss_formula() -> None:
    candidates = [_loss_table_candidate()]
    applicability = resolve_applicability(_profile())
    annotate_candidates(candidates, applicability)
    evaluated = evaluate_table_claims(
        "击穿电压(kV):≥40",
        "绝缘油击穿电压",
        candidates,
        unit="kV",
        applicability=applicability,
        manual_knowledge_rules=_loss_sum_rules(),
    )

    assert evaluated["decision"]["mode"] == "fallback_llm"
    assert evaluated["comparisons"] == []
    assert all(node.get("node") != "derived_sum" for node in evaluated["path"]["nodes"])
