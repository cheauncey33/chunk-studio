# pi-audit-sidecar

chunk-studio 的 Pi agent 标准值审查执行器（Node sidecar）。**无状态**：每个
`POST /audit/case` 起一个全新 Pi agent session（3 个 RAG 工具 + v7 任务定义），
跑完即返回判定；批处理、进度、checkpoint、重试都由 FastAPI 侧的
`run_report_audit_workflow.py --judge-mode agent` 负责。

## 启动

```powershell
cd services/pi-audit-sidecar
npm install
Copy-Item .env.example .env   # 填 PI_API_KEY（智谱开放平台）
npm start                     # 监听 http://127.0.0.1:8787
```

健康检查：`GET /health`（无需鉴权）。设置了 `AGENT_SIDECAR_TOKEN` 时，
`/audit/case` 需要 `Authorization: Bearer <token>`。

## POST /audit/case

请求体（与生产 `runtime_case` 对齐）：

```json
{
  "case_id": "item_abc_req_0",
  "sample_context": {"model": "S20-M.RL-400/10-NX2", "...": "..."},
  "test_item": {"item_no": "5", "project_name": "空载损耗和空载电流测量", "phase": "initial"},
  "reported_requirement": {"text": "空载损耗P0(kW):≤0.370", "unit": "kW"},
  "file_scope": ["<assistant 绑定知识库文件 id>"],
  "production_query": "S20-M.RL-400/10-NX2 400 kVA 10/0.4 kV 空载损耗和空载电流测量 空载损耗P0(kW):≤0.370",
  "retrieved_candidates": [
    {"chunk_id": "…", "content_type": "table", "standard_no": "Q/GDW 12126.4-2024", "table_no": "6", "bind_state": "matched"},
    {"chunk_id": "…", "content_type": "section", "standard_no": "GB/T 1094.3-2017", "section": "5.3", "text": "……条款正文……"}
  ],
  "tool_budget": 8
}
```

响应：

```json
{
  "ok": true,
  "case_id": "…",
  "result": {"case_id": "…", "verdict": "match", "kind": "exact", "standard_no": "…",
              "standard_value": "…", "reported_value": "…", "evidence": [], "reasoning": "…"},
  "status": "supported",
  "parse_mode": "strict",
  "stats": {"tool_calls": 3, "search_calls": 2, "read_chunks": 1, "turns": 3,
             "gate_reprompt": false, "duration_ms": 41000},
  "trace_file": "traces/…jsonl",
  "final_text_preview": "…"
}
```

- `status` 是旧词表映射（match→supported / unevaluable→insufficient_context /
  out_of_scope→not_audited），生产报告/前端直接可用；`verdict`/`kind` 是 v7 新分类。
- `parse_mode`：strict（完整 JSON）/ loose（散列字段正则）/ prose（中文散文兜底）。
- 失败返回 `{"ok": false, "error": "...", "retryable": true}`（HTTP 502）；
  调用方（workflow 脚本）按指数退避重试。
- Host 只核 `match`/`mismatch` 的 `chunk_id` 是否真的 `read_chunk` 过，并用 `readBodies`
  回填 `evidence.text`。引用协议失败记 `protocol_error`，不改写业务判定。

## 判定口径（v7）

与 `experiment/pi-standard-audit/skill.md` 保持同步：四向 verdict + 强制 kind、
总损耗加和口径、单位等价、加严=mismatch、standard_not_found 需先 read_chunk
（host 端硬门禁：未读原文时自动追加一次重判提示）。

生产路径：程序先多路召回，除无可比较要求的预筛外，交给 Agent 正式判定。进入 Agent 时带上第一轮定位卡和 `production_query`；
section 预览按该检索式抽 2～3 个命中窗口。Agent 先 `read_chunk`，不够才 `search_standards`。search 只返回定位预览，不含表格数值。

参数通过 `parameter_evidence` 保留报告提取/型号解码来源、引用和校验状态；`sample_context` 仅含报告抽取值。`naming_rule_context` 提供命名原文供核实推断。旧 checkpoint 没有参数引用时标 unverified，不能视作已验证。Agent 需输出 `applicability_checks`，Host 对缺失、未确认或不在样品来源中出现的引用追加一次补证据提示；仍失败时保留模型原始判定并记录 `applicability_validation_issues`，Python 将生产判定降为 insufficient_context / unevaluable，保留 agent_verdict 与校验错误，不接受为有效终判。此检查验证证据协议，语义及条件是否完整仍由 Agent 判断。

引用校验区分 `sample_fact`（报告/命名依据）与 `standard_condition`（实际 read_chunk 过的标准原文）。标准表题不能代替样品事实；只有标准引用的结果不能通过。允许 HTML 展示标签、空白及全半角标点差异，保留词语、数字、单位和比较方向，不做语义相似匹配。逐项匹配方式记录在 `applicability_validation`。

定向验证：`uv run python scripts/inspect_applicability_quotes.py --replay <实跑JSON> --report-markdown <报告.md> --output <引用诊断.json>`；`uv run python scripts/evaluate_applicability_cases.py --replay <实跑JSON> --output <对照结果.json>`。后者复用首轮定位卡，重新执行 Agent，包含一条真实回放和五条明确标注的合成参数变体，不修改原报告或数据库。

## 可靠性

- 单 case 硬超时 `AGENT_CASE_TIMEOUT_MS`（默认 420s），超时抛错可重试。
- 工具调用预算 `PI_MAX_TOOL_CALLS`（默认 8）：超预算后拦截工具并要求直接出判定。
- 并发 `AGENT_CONCURRENCY`（默认 1）：GLM 限速 + 延迟可预期；多余请求排队。
- trace JSONL 按目录存档，自动保留最近 `TRACE_KEEP` 个。
报告工作流在逐项审核前用同一次 `model_decode` 调用完成型号解析和参数核对：输入完整型号、报告提取参数及已验证原文片段、命名规则全文，不再次输入报告全文；输出逐字段 confirmed/missing/conflict/unverified 及引用。结果与输入哈希保存到 checkpoint；来源片段、提取值、命名规则、提示词、schema 或模型变化时失效；旧版解析缓存会重新运行合并节点。各 case 接收 `parameter_review`，复用 confirmed 参数，不再发送命名文件全文。当前标准的适用条件仍逐项核对；影响适用性的缺失/冲突才补查报告。直接调用 `/audit/case` 而不提供 review 时保留原有逐项核对行为。
