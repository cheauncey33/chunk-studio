from __future__ import annotations

from app.audit_semantics import (
    annotate_candidates,
    bind_table_row,
    build_deterministic_comparisons,
    classify_evidence_roles,
    compare_claims,
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
