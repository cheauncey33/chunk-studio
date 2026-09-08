"""Build a private, corpus-backed extension without altering the legacy benchmark.

Knowledge-rule verification and report-source verification are kept separate so
an extracted report record is never presented as source-document verification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.evidence_locator import chunk_text_sha256, resolve_evidence_locator


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evidence_record(chunk: dict, reason: str) -> dict:
    return {
        "locator": chunk["locator"], "evidence_quote": chunk["text"],
        "quote_verified": True, "reason": reason,
        "legacy_label": "direct_candidate", "discovery_method": "independent_corpus_annotation",
        "source_trace": chunk.get("source_trace", {}), "bbox": chunk.get("bbox"),
    }


def build(source: Path, annotations: Path, output: Path, database: Path) -> dict:
    cases, corpus, rules = read(source / "cases.json"), read(source / "corpus.json"), read(annotations)
    by_hash = {c["locator"]["text_sha256"]: c for c in corpus}
    case_map = {c["case_key"]: c for c in cases}
    if set(case_map) != set(rules["assignments"]):
        raise ValueError("Every extracted case must have exactly one independent annotation")
    reports = {r["asset_number"]: r for r in read(source / "reports.json")}
    used = {h for a in rules["assignments"].values() for group in a.get("groups", []) for h in group}
    conn = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for h in sorted(used):
        chunk = by_hash[h]
        if chunk_text_sha256(chunk["text"]) != h:
            raise ValueError(f"Corrupted corpus text: {h}")
        if len(resolve_evidence_locator(conn, chunk["locator"])) != 1:
            raise ValueError(f"Gold does not uniquely resolve in the database: {h}")
    conn.close()
    rows = []
    for case in cases:
        a = rules["assignments"][case["case_key"]]
        report = reports[case["asset_number"]]
        source_path = Path(report["source_path"])
        source_present = source_path.is_file()
        source_hash_matches = source_present and hashlib.sha256(source_path.read_bytes()).hexdigest() == report["source_sha256"]
        groups = [
            {"group_id": f"required_{i + 1}", "requirement": a["basis"],
             "alternatives": [evidence_record(by_hash[h], a["basis"]) for h in group]}
            for i, group in enumerate(a.get("groups", []))
        ]
        rows.append({
            "case_id": case["case_key"], "dataset_split": "development",
            "asset_number": case["asset_number"], "phase": case["phase"],
            "domain": case["domain"], "family_id": a["family_id"],
            "detection_project": {"project_name": case["project"],
                "reported_requirement": {"text": case["requirement"], "unit": case["unit"]},
                "sample_context": a.get("sample_context", {})},
            "source_report": {"source_sha256": report["source_sha256"],
                "source_name": report["source_name"], "source_path": report["source_path"],
                "source_present": source_present, "source_hash_matches": source_hash_matches,
                "extraction_verified": False, "source_case_id": case["source_case_id"],
                "note": "原始报告与页/bbox尚不可核验；输入来自保留的抽取记录。"},
            "required_evidence_groups": groups,
            "relevant_evidence": list({e["locator"]["text_sha256"]: e for g in groups for e in g["alternatives"]}.values()),
            "real": a["real"], "expected_status": a["expected_status"],
            "basis": a["basis"], "conditions": a.get("conditions", []),
            "missing_context_fields": a.get("missing_context_fields", []),
            "knowledge_rule_verified": bool(groups),
            "report_judgment_scoreable": False,
            "review": {"status": "ai_corpus_verified" if groups else "ai_abstention",
                "human_review_required": False, "reviewer": "Codex",
                "note": "用户授权自动标注；不冒充人工复核。报告事实核验与标准规则核验分别记录。"},
        })
    synthetic = []
    for spec in rules["mutations"]:
        base = next(r for r in rows if r["case_id"] == spec["base_case_id"])
        row = deepcopy(base)
        row.update({"case_id": spec["edit_id"], "synthetic": True,
            "parent_case_id": base["case_id"], "kind": spec["kind"],
            "expected_status": spec["expected_status"], "report_judgment_scoreable": True,
            "conditions": spec["conditions"], "missing_context_fields": [],
            "basis": spec["note"], "mutation": spec})
        row["detection_project"]["reported_requirement"]["text"] = spec["after"]
        row["detection_project"]["sample_context"] = spec["sample_context"]
        row["real"] = spec["real"]
        row["source_report"]["note"] = "人工构造的测试输入；继承原案例 lineage，不代表原报告存在该缺陷。"
        synthetic.append(row)
    output.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": "transformer_gold_v2", "status": "ai_annotated",
        "policy": rules["policy"], "cases": rows, "synthetic_cases": synthetic,
        "summary": {"report_cases": len(rows), "synthetic_cases": len(synthetic),
            "unique_gold_chunks": len(used), "case_status_counts": dict(Counter(r["expected_status"] for r in rows)),
            "synthetic_status_counts": dict(Counter(r["expected_status"] for r in synthetic)),
            "source_report_verified_cases": 0, "human_review_required": 0}}
    write(output / "gold.json", result)
    write(output / "manifest.json", {"edits": rules["mutations"], "matching": "case_id; never requirement substring alone"})
    legacy = read(ROOT / "evaluation" / "test_set.json")
    write(output / "combined_test_set.json", {"schema_version": "combined_benchmark_v1",
        "legacy": legacy, "transformer_extension": result,
        "total_case_count": len(legacy["cases"]) + len(rows) + len(synthetic),
        "split_policy": "All new cases and their mutations are development-only; keep report/family lineage together."})
    lines = ["# 变压器扩充测试集", "", json.dumps(result["summary"], ensure_ascii=False), "",
        "real 是知识库标准要求，不是报告实测结果。conditional_supported 表示所列条件成立时支持；不是已核实原报告合规。",
        "原报告路径缺失/抽取未核验的项不进入报告端到端评分；无需人工审批。", "",
        "|case|项目与报告要求|real|判定|依据|", "|---|---|---|---|---|"]
    for r in rows + synthetic:
        def cell(v: Any) -> str:
            return str(v).replace("|", "\\|").replace("\n", " ")
        refs = "; ".join(f"{e['locator']['standard_no']} / {e['locator'].get('table_no', e['locator'].get('section', ''))} / p{e['locator']['page_start']}" for g in r["required_evidence_groups"] for e in g["alternatives"])
        lines.append("|" + "|".join(map(cell, [r["case_id"], r["detection_project"]["project_name"] + "：" + r["detection_project"]["reported_requirement"]["text"], r["real"], r["expected_status"], r["basis"] + " " + refs])) + "|")
    (output / "逐条真值.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/evaluation_private/transformer_reports_v1")
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/evaluation_private/transformer_reports_v2")
    parser.add_argument("--database", type=Path, default=ROOT / "backend/data/chunkstudio.db")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.annotations or args.source / "independent_annotations.json", args.output, args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
