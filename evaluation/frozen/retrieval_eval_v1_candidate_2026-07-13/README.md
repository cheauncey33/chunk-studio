# Retrieval Eval v1 Candidate Freeze

冻结日期：2026-07-13

状态：`candidate_gold_frozen_pending_domain_review`

这不是 final benchmark。这个目录冻结的是当前 40 条检索评测候选集、候选 gold、模型查询、recovery 产物和相关 prompt，用于防止后续继续修改时丢失当前状态。

## 当前用途

- 固定一版候选评测集，用于调试检索、query planner、rerank 和证据选择策略。
- 对比后续改动是否相对改善召回和证据覆盖。
- 保留 `backend/data/reports` 中未被 git 跟踪的运行产物。

## 不应使用

- 不应把这一版称为领域专家确认的 final gold。
- 不应用这版测试集反复调参后再宣称泛化效果。
- 不应把 `gold_discovery_only` recovery query 当作正式被评测系统输入。

## 关键统计

- Case 数：40
- Split：development 10 / validation 10 / test 20
- Query model：`qwen3.6-27b`
- Query routes：production 40 / semantic 40 / keyword 40 / table_target 29 / section_target 35
- Selected evidence：139
- Direct evidence cases：38/40
- Missing direct evidence cases：2/40
- Locator validation：139 checked / 0 invalid

## 术语说明

`expected_answer` 不是要求程序判断数值对错。它只是把报告里的专家标准值结构化记录下来，例如 raw text、比较符、数值、单位。后续仍然可以让 LLM 判断适用性和复杂数值关系，但模型必须围绕这个报告值判断，而不是在候选证据里自由漂移。

`gap` 指证据缺口原因，不是错误本身。比如 direct evidence 缺失时，需要区分是 query 没召回、标准库缺文件、parser/chunk 没切出来，还是报告依据来自招标文件或内部规范。这类分类用于排查系统问题。

`quote_verified=false` 不是人工未审核。它只是脚本做的机械检查：模型给出的 `evidence_quote` 是否能在候选 chunk 文本中逐字找到。false 可能来自 quote 被改写、空白/标点差异、截断、OCR 差异，也可能是真引用不严谨。

## 后续版本建议

下一版不要直接覆盖这个目录。建议新增 `retrieval_eval_v1_candidate_plus_answer_anchor_YYYY-MM-DD` 或升级为 `retrieval_eval_v2_*`，再加入：

- report-value anchor / `expected_answer`
- missing-direct gap classification
- quote verification cleanup or explicit failure reason
- LLM judge prompt that明确使用报告标准值作为 answer-level ground truth

## Baseline 报告

`baseline_current_retrieval.json` 和 `baseline_current_retrieval.md` 是用 `scripts/evaluate_frozen_retrieval.py` 对当前检索系统重新跑出的基线结果。

这份报告只衡量冻结 gold evidence 是否被召回，不判断报告数值是否正确，也不调用 LLM judge。
