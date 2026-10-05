"""Replay one real audit and controlled applicability variants without DB writes."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_report_audit_workflow as workflow


def cases(replay: dict, standard_file_id: str) -> list[tuple[dict, dict]]:
    original = deepcopy(replay["input"])
    original["case_id"] = "p0_real_report"
    pool = [card for card in original["retrieved_candidates"] if card.get("standard_no") == "Q/GDW 12126.4-2024"]
    variants = [
        ("steel_offload", "S20-M.RL-400/10-NX2", "电工钢", "无励磁调压", "闭口", "≤0.370", "6", "match"),
        ("steel_onload", "SZ20-M.RL-400/10-NX2", "电工钢", "有载调压", "闭口", "≤0.370", "9", "match"),
        ("amorphous_offload", "SH21-M.RL-400/10-NX2", "非晶合金", "无励磁调压", "闭口", "≤0.200", "7", "match"),
        ("closure_missing", "S20-M.RL-400/10-NX2", "电工钢", "无励磁调压", "", "≤0.370", None, "unevaluable"),
        ("material_conflict", "SH21-M.RL-400/10-NX2", "电工钢", "无励磁调压", "闭口", "≤0.200", None, "unevaluable"),
    ]
    result = [(original, {"id": "real_report", "kind": "real_replay", "expected": "match_or_explicit_evidence_gap"})]
    for name, model, material, regulation, closure, value, table, expected in variants:
        payload = deepcopy(original)
        payload["case_id"] = "p0_" + name
        # These are synthetic report facts, not alterations to the actual report.
        payload.pop("report_file_id", None)
        context = {
            "model": model, "rated_capacity": "400 kVA", "rated_voltage": "10/0.4 kV",
            "phase_count": "3", "connection_group": "Dyn11", "insulation_medium": "油浸式",
            "product_type": "配电变压器", "winding_count": "双绕组", "core_material": material,
            "core_structure": "立体卷铁芯", "regulation_method": regulation,
            "core_closure": closure, "sealing_type": "密封式", "rated_frequency": "50 Hz",
        }
        payload["sample_context"] = context
        payload["parameter_evidence"] = {
            key: {"label": key, "value": val, "source": "report_extraction" if val else "missing",
                  "verification": "quote_verified" if val else "missing",
                  "source_path": "synthetic_fixture:" + name,
                  "evidence": [{"quote": f"{key}: {val}", "quote_verified": True}] if val else []}
            for key, val in context.items()
        }
        if name == "material_conflict":
            payload["parameter_evidence"]["model_core_material"] = {
                "label": "型号H对应材质", "value": "非晶合金", "source": "model_decode",
                "verification": "requires_review", "evidence": [],
            }
        payload["file_scope"] = [standard_file_id]
        payload["retrieved_candidates"] = deepcopy(pool)
        payload["reported_requirement"] = {"text": "空载损耗P0(kW):" + value, "unit": "kW"}
        payload["production_query"] = " ".join([model, material, regulation, "空载损耗"])
        result.append((payload, {"id": name, "kind": "controlled_fixture", "expected": expected, "table": table}))
    return result


def run(payload: dict, expectation: dict, sidecar: str) -> dict:
    started = time.perf_counter()
    try:
        response = workflow._call_agent_sidecar(sidecar, payload)
        judgment = workflow._judgment_from_agent_result(response, reason_code="applicability_evaluation")
        model = response.get("result") or {}
        tables = [str(e.get("location") or "") for e in model.get("evidence") or []]
        issues = model.get("applicability_validation_issues") or []
        passed = (model.get("verdict") == expectation["expected"] and not issues
                  and (not expectation.get("table") or any("表" + expectation["table"] in location for location in tables)))
        if expectation["expected"] == "unevaluable":
            passed = passed and model.get("kind") == "applicability_undetermined"
        if expectation["kind"] == "real_replay":
            passed = not issues and (
                model.get("verdict") == "match"
                or model.get("verdict") == "unevaluable" and model.get("kind") == "applicability_undetermined"
            )
        return {"expectation": expectation, "input": payload, "output": response,
                "production_judgment": judgment, "passed": passed, "duration_s": round(time.perf_counter() - started, 2)}
    except Exception as exc:
        return {"expectation": expectation, "input": payload, "error": str(exc), "passed": False}


def prepare_merged_parameters(payload: dict, *, prompt: str, model: str, schema: dict) -> tuple[dict, dict]:
    """Use the production merged node; fixture quotes remain explicitly synthetic."""
    payload = deepcopy(payload)
    parameters = dict(payload["sample_context"])
    evidence = {}
    for key, record in payload.get("parameter_evidence", {}).items():
        if key not in parameters or record.get("source") != "report_extraction":
            continue
        quotes = [item for item in record.get("evidence", []) if item.get("quote_verified") is True]
        if quotes:
            evidence[key] = quotes[0]
    parameters["_parameter_evidence"] = evidence
    started = time.perf_counter()
    decoded = workflow._cached_model_decode(parameters, payload.get("naming_rule_context", ""),
        prompt=prompt, model=model, parameter_schema=schema)
    payload["parameter_review"] = decoded["parameter_review"]
    payload.pop("naming_rule_context", None)
    return payload, {"duration_s": round(time.perf_counter() - started, 2), "model": model, "output": decoded}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sidecar", default="http://127.0.0.1:8787")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--case", action="append", default=[], help="Run selected stable fixture labels only")
    parser.add_argument("--merged-parameters", action="store_true", help="Run the current combined decode/review node before each distinct sample")
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    locator = next(card for card in replay["input"]["retrieved_candidates"] if card.get("standard_no") == "Q/GDW 12126.4-2024")
    response = httpx.get(f"{args.api_base.rstrip('/')}/api/chunks/{locator['chunk_id']}", timeout=30)
    response.raise_for_status()
    fixtures = cases(replay, response.json()["file_id"])
    if args.case:
        fixtures = [(payload, expected) for payload, expected in fixtures if expected["id"] in args.case]
        if len(fixtures) != len(set(args.case)):
            raise ValueError("unknown fixture label")
    rows = []
    if args.merged_parameters:
        profile = workflow._load_assistant_version("assistant_oil_transformer_audit")
        prompt = workflow._prompt_content(profile, "model_decode", var_context=workflow._assistant_prompt_var_context("assistant_oil_transformer_audit", profile))
        schema = deepcopy(profile.get("parameter_schema") or {})
        schema.setdefault("fields", []).append({"key": "core_closure", "label": "铁芯开闭口", "hint": "不能由密封式推导", "required": False})
        model = workflow.llm.public_config()["model"]
        def execute(payload, expected):
            try:
                prepared, parameter_trace = prepare_merged_parameters(payload, prompt=prompt, model=model, schema=schema)
                row = run(prepared, expected, args.sidecar)
                row["parameter_node"] = parameter_trace
                return row
            except Exception as exc:
                return {"expectation": expected, "error": str(exc), "passed": False}
    else:
        def execute(payload, expected):
            return run(payload, expected, args.sidecar)
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(execute, payload, expected) for payload, expected in fixtures]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            args.output.write_text(json.dumps({"scope": "one real replay plus five synthetic applicability fixtures; stored first recall reused", "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
            outcome = (row.get("output") or {}).get("result") or {}
            print(row["expectation"]["id"], "PASS" if row["passed"] else "FAIL", outcome.get("verdict"), outcome.get("applicability_validation_issues"), row.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
