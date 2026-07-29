"""Build an adversarially edited HBJC report markdown + expectation manifest.

Baseline is the MinerU markdown used by the production audit workflow.
Only report-side「标准要求」strings are changed; the standards KB is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASELINE = (
    ROOT
    / "backend"
    / "data"
    / "parses"
    / "ef0d5c0329204e81ab93e2adc5fbae9c_e655ea86968541138981f679f92ea085.md"
)
OUT_DIR = Path(__file__).resolve().parent
OUT_MD = OUT_DIR / "HBJC-ZY-202509137.adversarial.md"
OUT_MANIFEST = OUT_DIR / "manifest.json"
BASELINE_COPY = OUT_DIR / "HBJC-ZY-202509137.baseline.md"

# Each edit: unique source string → adversarial string + expected judge status.
# match: used later to locate the corresponding audit case by requirement text.
EDITS: list[dict[str, str]] = [
    # --- numeric looser ---
    {
        "edit_id": "e01_no_load_looser",
        "kind": "numeric_looser",
        "old": "空载损耗P0(kW):≤0.370",
        "new": "空载损耗P0(kW):≤0.500",
        "expected_status": "mismatch",
        "match_contains": "空载损耗P0",
        "note": "报告限值宽于标准限值，应判冲突",
    },
    {
        "edit_id": "e02_load_looser",
        "kind": "numeric_looser",
        "old": "负载损耗Pk(kW):≤3.615",
        "new": "负载损耗Pk(kW):≤5.000",
        "expected_status": "mismatch",
        "match_contains": "负载损耗Pk",
        "note": "报告限值宽于标准限值，应判冲突",
    },
    {
        "edit_id": "e08_no_load_current_looser",
        "kind": "numeric_looser",
        "old": "空载电流I0(%):≤0.16(1+30%)",
        "new": "空载电流I0(%):≤0.50(1+30%)",
        "expected_status": "mismatch",
        "match_contains": "空载电流I0",
        "note": "空载电流限值放宽，应判冲突",
    },
    {
        "edit_id": "e09_total_loss_looser",
        "kind": "formula_aggregate",
        "old": "总损耗P总(kW):≤3.985",
        "new": "总损耗P总(kW):≤5.500",
        "expected_status": "mismatch",
        "match_contains": "总损耗P总",
        "note": "总损耗派生限值放宽，应判冲突",
    },
    {
        "edit_id": "e10_winding_temp_looser",
        "kind": "numeric_looser",
        "old": "高压绕组平均温升(K):≤60",
        "new": "高压绕组平均温升(K):≤80",
        "expected_status": "mismatch",
        "match_contains": "高压绕组平均温升",
        "note": "绕组温升限值放宽，应判冲突",
    },
    {
        "edit_id": "e17_sound_power_looser",
        "kind": "numeric_looser",
        "old": "声功率级LWA[dB(A)]:≤46",
        "new": "声功率级LWA[dB(A)]:≤60",
        "expected_status": "mismatch",
        "match_contains": "声功率级LWA",
        "note": "声功率级限值放宽，应判冲突",
    },
    {
        "edit_id": "e24_bushing_temp_looser",
        "kind": "numeric_looser",
        "old": "套管最高温升:≤85",
        "new": "套管最高温升:≤120",
        "expected_status": "mismatch",
        "match_contains": "套管最高温升",
        "note": "套管温升限值放宽，应判冲突",
    },
    # --- comparator / direction ---
    {
        "edit_id": "e03_oil_temp_flip",
        "kind": "comparator_flip",
        "old": "顶层油温升:≤55",
        "new": "顶层油温升:≥55",
        "expected_status": "mismatch",
        "match_contains": "顶层油温升",
        "note": "比较方向反转，应判冲突",
    },
    {
        "edit_id": "e04_breakdown_flip",
        "kind": "comparator_flip",
        "old": "击穿电压(kV):≥40",
        "new": "击穿电压(kV):≤40",
        "expected_status": "mismatch",
        "match_contains": "击穿电压",
        "note": "下限改成上限，应判冲突",
        "replace_all": "true",
    },
    # --- bandwidth / tolerance ---
    {
        "edit_id": "e11_main_tap_bandwidth",
        "kind": "bandwidth_looser",
        "old": "主分接电压比偏差:±0.5%与实际阻抗电压百分数的±1/10中较低者",
        "new": "主分接电压比偏差:±1.0%与实际阻抗电压百分数的±1/10中较低者",
        "expected_status": "mismatch",
        "match_contains": "主分接电压比偏差",
        "note": "主分接允许偏差带宽放宽，应判冲突",
    },
    {
        "edit_id": "e15_impedance_bandwidth",
        "kind": "bandwidth_looser",
        "old": "短路阻抗Zk(%):4.0(1±10%)",
        "new": "短路阻抗Zk(%):4.0(1±20%)",
        "expected_status": "mismatch",
        "match_contains": "短路阻抗Zk",
        "note": "阻抗允许偏差带宽放宽，应判冲突",
    },
    {
        "edit_id": "e06_other_tap_keep",
        "kind": "existing_conflict_control",
        "old": "其他分接电压比偏差:变压器阻抗电压值(%)的1/10以内,且允许偏差应为±1%",
        "new": "其他分接电压比偏差:变压器阻抗电压值(%)的1/10以内,且允许偏差应为±1%",
        "expected_status": "mismatch",
        "match_contains": "其他分接电压比偏差",
        "note": "原文已与标准 ±0.5% 冲突，保持不变作为对照",
        "noop": "true",
    },
    # --- wrong level / condition / label ---
    {
        "edit_id": "e12_applied_voltage_wrong",
        "kind": "wrong_level",
        "old": "高压对低压及地试验电压(kV):35",
        "new": "高压对低压及地试验电压(kV):25",
        "expected_status": "mismatch",
        "match_contains": "高压对低压及地试验电压",
        "note": "外施耐压电压等级写错，应判冲突",
        "replace_all": "true",
    },
    {
        "edit_id": "e16_induction_level_wrong",
        "kind": "wrong_level",
        "old": "高压试验电压:2Ur",
        "new": "高压试验电压:1.5Ur",
        "expected_status": "mismatch",
        "match_contains": "高压试验电压",
        "note": "感应耐压倍数写错，应判冲突",
    },
    {
        "edit_id": "e18_impulse_level_wrong",
        "kind": "wrong_level",
        "old": "高压线端全波电压(kV): -75(1±3%)波前时间: T1: 1.2μs±30%半峰值时间: T2: 50μs±20%",
        "new": "高压线端全波电压(kV): -60(1±3%)波前时间: T1: 1.2μs±30%半峰值时间: T2: 50μs±20%",
        "expected_status": "mismatch",
        "match_contains": "高压线端全波电压",
        "note": "雷电冲击全波电压等级写错，应判冲突",
    },
    {
        "edit_id": "e13_seal_duration_wrong",
        "kind": "wrong_condition",
        "old": "持续时间:12",
        "new": "持续时间:6",
        "expected_status": "mismatch",
        "match_contains": "持续时间:6",
        "note": "压力密封持续时间条件写错，应判冲突",
    },
    {
        "edit_id": "e25_short_circuit_count_wrong",
        "kind": "wrong_condition",
        "old": "试验次数:9次",
        "new": "试验次数:3次",
        "expected_status": "mismatch",
        "match_contains": "试验次数",
        "note": "短路承受试验次数写错，应判冲突",
    },
    {
        "edit_id": "e20_vector_group_wrong",
        "kind": "wrong_label",
        "old": "联结组标号:Dyn11",
        "new": "联结组标号:Dyn5",
        "expected_status": "mismatch",
        "match_contains": "联结组标号",
        "note": "联结组标号写错，应判冲突",
        "replace_all": "true",
    },
    {
        "edit_id": "e19_sound_pressure_magnitude",
        "kind": "magnitude_error",
        "old": "声压级 $\\overline{L_{PA}}$  [dB(A)]:≤40",
        "new": "声压级 $\\overline{L_{PA}}$  [dB(A)]:≤400",
        "expected_status": "mismatch",
        "match_contains": "声压级",
        "note": "声压级数量级错误，应判冲突",
    },
    # --- unit equivalence / tighter / positives ---
    {
        "edit_id": "e05_tand_unit_equiv",
        "kind": "unit_equivalence",
        "old": "介质损耗因数tanδ(90°C):≤1",
        "new": "介质损耗因数tanδ(90°C):≤0.010",
        "expected_status": "supported",
        "match_contains": "介质损耗因数tanδ(90°C)",
        "note": "1% 与 0.010 等价；应仍支持",
    },
    {
        "edit_id": "e14_lv_phase_tighter",
        "kind": "numeric_tighter",
        "old": "低压(相)电阻三相不平衡率最大值(%):≤4",
        "new": "低压(相)电阻三相不平衡率最大值(%):≤3",
        "expected_status": "supported",
        "match_contains": "低压(相)电阻三相不平衡率最大值",
        "note": "报告限值严于标准，应仍支持",
    },
    {
        "edit_id": "e07_resistance_positive",
        "kind": "positive_control",
        "old": "高压(线)电阻三相不平衡率最大值(%):≤2",
        "new": "高压(线)电阻三相不平衡率最大值(%):≤2",
        "expected_status": "supported",
        "match_contains": "高压(线)电阻三相不平衡率最大值",
        "note": "未改动正控，应仍支持",
        "noop": "true",
    },
    {
        "edit_id": "e21_lv_line_positive",
        "kind": "positive_control",
        "old": "低压(线)电阻三相不平衡率最大值(%):≤2",
        "new": "低压(线)电阻三相不平衡率最大值(%):≤2",
        "expected_status": "supported",
        "match_contains": "低压(线)电阻三相不平衡率最大值",
        "note": "未改动正控，应仍支持",
        "noop": "true",
    },
]


def main() -> None:
    if not BASELINE.exists():
        raise SystemExit(f"baseline markdown not found: {BASELINE}")
    text = BASELINE.read_text(encoding="utf-8")
    BASELINE_COPY.write_text(text, encoding="utf-8")

    applied: list[dict[str, str]] = []
    for edit in EDITS:
        old = edit["old"]
        new = edit["new"]
        if edit.get("noop") == "true":
            count = text.count(old)
            if count < 1:
                raise SystemExit(f"positive/control string missing: {old!r}")
            applied.append({**edit, "occurrences": str(count)})
            continue
        count = text.count(old)
        if count < 1:
            raise SystemExit(f"edit source missing: {old!r}")
        if edit.get("replace_all") == "true":
            text = text.replace(old, new)
        else:
            if count != 1:
                raise SystemExit(
                    f"expected unique source for {edit['edit_id']}, found {count}"
                )
            text = text.replace(old, new, 1)
        applied.append({**edit, "occurrences": str(count)})

    OUT_MD.write_text(text, encoding="utf-8")
    kinds = sorted({edit["kind"] for edit in applied})
    manifest = {
        "version": 2,
        "report_id": "HBJC",
        "baseline_source": str(BASELINE.relative_to(ROOT)).replace("\\", "/"),
        "baseline_copy": BASELINE_COPY.name,
        "adversarial_report": OUT_MD.name,
        "naming_rule": "backend/data/parses/b7cc1dec4548408f91242dcf22fb5dfd_3915e1b4b0474e92937ea84c5c92104d.md",
        "assistant_id": "assistant_oil_transformer_audit",
        "kinds": kinds,
        "edits": applied,
        "scoring": {
            "match": "reported_requirement.text contains match_contains (phase=initial preferred)",
            "metrics": [
                "attack_mismatch_recall",
                "attack_mismatch_precision_on_tracked",
                "equivalence_accuracy",
                "positive_control_accuracy",
                "tighter_accuracy",
                "overall_edit_accuracy",
                "accuracy_by_kind",
            ],
        },
    }
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_attack = sum(1 for e in applied if e["expected_status"] == "mismatch")
    n_support = sum(1 for e in applied if e["expected_status"] == "supported")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_MANIFEST}")
    print(
        f"edits={len(applied)} applied={sum(1 for e in applied if e.get('noop') != 'true')} "
        f"expect_mismatch={n_attack} expect_supported={n_support} kinds={len(kinds)}"
    )


if __name__ == "__main__":
    main()
