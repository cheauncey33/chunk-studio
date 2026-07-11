from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_vector_retrieval import build_case_query


def test_build_case_query_uses_available_basis_and_audit_facts() -> None:
    case = {
        "audit_card": {
            "report": {"declared_bases": [
                {"standard_no": "GB/T 1-2024", "availability": "available"},
                {"standard_no": "GB/T 2-2024", "availability": "missing"},
            ]},
            "sample_context": {"facts": [{"raw_text": "额定容量 400 kVA"}]},
            "test_context": {"project_name": "负载损耗", "facts": [{"raw_text": "参考温度 75 °C"}]},
            "pending_rule": {"parameter_name": "负载损耗Pk", "raw_text": "Pk≤3.615 kW"},
        }
    }

    query = build_case_query(case)

    assert query == "GB/T 1-2024；额定容量 400 kVA；负载损耗；参考温度 75 °C；负载损耗Pk；Pk≤3.615 kW"
    assert "GB/T 2-2024" not in query
