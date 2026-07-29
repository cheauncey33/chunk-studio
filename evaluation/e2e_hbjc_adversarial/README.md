# HBJC 端到端对抗评测（改报告 MD）

目标：在**不改动标准知识库**的前提下，篡改 HBJC 报告 Markdown 中的「标准要求」，再跑完整审查工作流，衡量端到端判定（与检索召回指标分离）。

## 文件

| 文件 | 说明 |
|---|---|
| `build_adversarial_report.py` | 从生产用 MinerU MD 生成对抗报告 + `manifest.json` |
| `HBJC-ZY-202509137.baseline.md` | 基线副本（构建时生成） |
| `HBJC-ZY-202509137.adversarial.md` | 对抗报告（构建时生成） |
| `manifest.json` | 每条篡改的期望 `status` 与匹配关键字 |

## 覆盖场景（v2）

| kind | 含义 | 期望 |
|---|---|---|
| `numeric_looser` | 数值限值放宽 | mismatch |
| `comparator_flip` | 比较符/方向反转 | mismatch |
| `bandwidth_looser` | ± 允许带宽放宽 | mismatch |
| `wrong_level` | 电压/冲击/感应等级写错 | mismatch |
| `wrong_condition` | 试验条件（时长、次数）写错 | mismatch |
| `wrong_label` | 联结组等标号写错 | mismatch |
| `magnitude_error` | 数量级错误 | mismatch |
| `formula_aggregate` | 总损耗等派生限值放宽 | mismatch |
| `existing_conflict_control` | 原文已知冲突对照 | mismatch |
| `unit_equivalence` | 单位/百分数等价改写 | supported |
| `numeric_tighter` | 报告限值严于标准 | supported |
| `positive_control` | 未改动正控 | supported |

## 1. 生成对抗报告

```powershell
uv run python evaluation/e2e_hbjc_adversarial/build_adversarial_report.py
```

## 2. 跑审查工作流

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/run_report_audit_workflow.py `
  evaluation/e2e_hbjc_adversarial/HBJC-ZY-202509137.adversarial.md `
  --naming-rule backend/data/parses/b7cc1dec4548408f91242dcf22fb5dfd_3915e1b4b0474e92937ea84c5c92104d.md `
  --report-id HBJC `
  --assistant-id assistant_oil_transformer_audit `
  --report-file-id ef0d5c0329204e81ab93e2adc5fbae9c `
  --naming-rule-file-id b7cc1dec4548408f91242dcf22fb5dfd `
  --output backend/data/reports/e2e_hbjc_adversarial_rich_audit.json
```

同一 `--output` 可从 `.checkpoint.json` 续跑。

## 3. 打分

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/score_e2e_adversarial_audit.py `
  backend/data/reports/e2e_hbjc_adversarial_rich_audit.json `
  --output backend/data/reports/e2e_hbjc_adversarial_rich_score.json
```
