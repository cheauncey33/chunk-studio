"""Build a stratified 40-case retrieval pool from four extracted reports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HBJC = ROOT / "backend" / "data" / "reports" / "report_test_items_hbjc_v1.json"
DEFAULT_OTHERS = ROOT / "backend" / "data" / "reports" / "report_test_items_other_three_v1.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "retrieval_case_pool_v1.json"


# report_id, item_no, requirement_index, retrieval_class, evidence_expectation, reason
SELECTIONS: list[tuple[str, str, int, str, str, str]] = [
    ("HBJC", "4", 0, "numeric_parameter_table", "table", "400 kVA S20-NX2空载损耗"),
    ("HBJC", "4", 1, "numeric_parameter_table", "mixed", "空载电流基准值与允许偏差"),
    ("HBJC", "5", 0, "numeric_parameter_table", "table", "400 kVA S20-NX2负载损耗"),
    ("HBJC", "7", 0, "insulation_voltage", "mixed", "外施耐压试验电压"),
    ("HBJC", "14", 0, "insulation_voltage", "mixed", "雷电全波冲击电压与波形参数"),
    ("HBJC", "5", 2, "rule_formula", "mixed", "总损耗需要分量值与求和规则"),
    ("HBJC", "6", 3, "rule_formula", "section", "感应耐压持续时间规则"),
    ("HBJC", "15.5", 0, "repeat_routine", "table", "重复例行阶段的负载损耗"),
    ("HBJC", "13", 3, "report_specific", "section", "短路试验后相电抗差规则"),
    ("HBJC", "9", 2, "report_specific", "section", "压力密封残余压力规则"),

    ("EZC", "4", 0, "numeric_parameter_table", "table", "200 kVA S20-NX2空载损耗"),
    ("EZC", "4", 1, "numeric_parameter_table", "mixed", "200 kVA空载电流与允许偏差"),
    ("EZC", "5", 1, "numeric_parameter_table", "table", "200 kVA S20-NX2负载损耗"),
    ("EZC", "10", 0, "insulation_voltage", "mixed", "雷电全波冲击电压"),
    ("EZC", "8", 0, "insulation_voltage", "section", "绝缘液击穿电压"),
    ("EZC", "5", 3, "rule_formula", "mixed", "总损耗分量与求和规则"),
    ("EZC", "7", 3, "rule_formula", "section", "频率相关的感应耐压持续时间公式"),
    ("EZC", "1", 0, "report_specific", "section", "报告特有绝缘电阻下限"),
    ("EZC", "11", 1, "report_specific", "mixed", "高压绕组平均温升65 K差异项"),
    ("EZC", "12", 3, "report_specific", "mixed", "短时过负载油箱外壳温升"),

    ("WHC", "4", 0, "numeric_parameter_table", "table", "200 kVA S20-NX2空载损耗"),
    ("WHC", "4", 1, "numeric_parameter_table", "mixed", "空载电流基准值与允许偏差"),
    ("WHC", "5", 1, "numeric_parameter_table", "table", "200 kVA S20-NX2负载损耗"),
    ("WHC", "6", 0, "insulation_voltage", "mixed", "外施耐压试验电压"),
    ("WHC", "10", 0, "insulation_voltage", "mixed", "雷电全波冲击完整波形参数"),
    ("WHC", "5", 3, "rule_formula", "mixed", "总损耗分量与求和规则"),
    ("WHC", "7", 3, "rule_formula", "section", "感应耐压持续时间范围"),
    ("WHC", "14.2.5", 1, "repeat_routine", "table", "重复例行阶段负载损耗"),
    ("WHC", "13", 1, "report_specific", "mixed", "声压级限值"),
    ("WHC", "14.1", 3, "report_specific", "section", "短路试验后最大相电抗差"),

    ("XYC", "4", 0, "numeric_parameter_table", "table", "200 kVA S20-NX2空载损耗"),
    ("XYC", "5", 1, "numeric_parameter_table", "table", "200 kVA S20-NX2负载损耗"),
    ("XYC", "5", 2, "numeric_parameter_table", "mixed", "短路阻抗及允许偏差"),
    ("XYC", "6", 0, "insulation_voltage", "mixed", "带±1%要求的外施耐压"),
    ("XYC", "10", 0, "insulation_voltage", "mixed", "雷电全波冲击电压"),
    ("XYC", "5", 3, "rule_formula", "mixed", "总损耗分量与求和规则"),
    ("XYC", "7", 3, "rule_formula", "section", "感应耐压持续时间范围"),
    ("XYC", "3", 0, "report_specific", "section", "主分接电压比复合偏差规则"),
    ("XYC", "11", 1, "report_specific", "mixed", "高压绕组平均温升"),
    ("XYC", "12", 3, "report_specific", "mixed", "短时过负载油箱外壳温升"),
]

SPLITS = {"HBJC": "development", "EZC": "validation", "WHC": "test", "XYC": "test"}


def _load_reports(paths: list[Path]) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for report in payload["reports"]:
            report_id = report["report_id"]
            if report_id in reports:
                raise ValueError(f"duplicate report {report_id}")
            reports[report_id] = report
    return reports


def _case_id(report_id: str, item_no: str, requirement_index: int) -> str:
    safe_item = item_no.replace(".", "-")
    return f"{report_id.lower()}-{safe_item}-r{requirement_index + 1}"


def build_cases(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for report_id, item_no, req_index, retrieval_class, evidence, reason in SELECTIONS:
        report = reports[report_id]
        matching = [item for item in report["items"] if str(item["item_no"]) == item_no]
        if len(matching) != 1:
            raise ValueError(f"{report_id} item {item_no} resolved to {len(matching)} items")
        item = matching[0]
        if not 0 <= req_index < len(item["requirements"]):
            raise ValueError(f"{report_id} item {item_no} requirement {req_index} is missing")
        requirement = item["requirements"][req_index]
        text = str(requirement["requirement_text"]).strip()
        cases.append({
            "case_id": _case_id(report_id, item_no, req_index),
            "report_id": report_id,
            "dataset_split": SPLITS[report_id],
            "source_file": report["source_file"],
            "sample_context": report.get("sample_context", {}),
            "test_item": {
                "item_no": item_no,
                "project_name": item["project_name"],
                "phase": item["phase"],
            },
            "reported_requirement": {
                "text": text,
                "unit": requirement.get("unit") or "/",
            },
            "source_locator": {
                "section": "检测结果汇总",
                "item_no": item_no,
                "phase": item["phase"],
                "requirement_index": req_index,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
            "retrieval_class": retrieval_class,
            "expected_evidence_type": evidence,
            "selection_reason": reason,
            "gold_status": "candidate_pending_evidence_review",
        })
    return cases


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    report_ids = sorted({case["report_id"] for case in cases})
    classes = sorted({case["retrieval_class"] for case in cases})
    return {
        "case_count": len(cases),
        "by_report": {report_id: sum(case["report_id"] == report_id for case in cases) for report_id in report_ids},
        "by_split": {
            split: sum(case["dataset_split"] == split for case in cases)
            for split in ("development", "validation", "test")
        },
        "by_class": {name: sum(case["retrieval_class"] == name for case in cases) for name in classes},
        "repeat_routine_cases": sum(case["test_item"]["phase"] == "repeat_routine" for case in cases),
        "by_evidence_type": {
            name: sum(case["expected_evidence_type"] == name for case in cases)
            for name in ("table", "section", "mixed")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hbjc", type=Path, default=DEFAULT_HBJC)
    parser.add_argument("--others", type=Path, default=DEFAULT_OTHERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    reports = _load_reports([args.hbjc, args.others])
    cases = build_cases(reports)
    summary = _summary(cases)
    if summary["case_count"] != 40 or any(count != 10 for count in summary["by_report"].values()):
        raise ValueError("v1 pool must contain exactly 10 cases per report")
    output = {
        "version": 1,
        "status": "candidate_pool_pending_evidence_review",
        "unit_of_evaluation": "one reported standard requirement",
        "selection_policy": "stratified by retrieval class; not random",
        "known_source_exclusions": [
            {
                "report_id": "EZC",
                "item_no": "6",
                "reason": "The source summary merged 60 s and 5 kV into '605/skV'; excluded rather than repaired from report body."
            }
        ],
        "summary": summary,
        "cases": cases,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
