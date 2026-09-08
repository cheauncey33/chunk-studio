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

## 判定口径（v7）

与 `experiment/pi-standard-audit/skill.md` 保持同步：四向 verdict + 强制 kind、
总损耗加和口径、单位等价、加严=mismatch、standard_not_found 需先 read_chunk
（host 端硬门禁：未读原文时自动追加一次重判提示）。

## 可靠性

- 单 case 硬超时 `AGENT_CASE_TIMEOUT_MS`（默认 420s），超时抛错可重试。
- 工具调用预算 `PI_MAX_TOOL_CALLS`（默认 8）：超预算后拦截工具并要求直接出判定。
- 并发 `AGENT_CONCURRENCY`（默认 1）：GLM 限速 + 延迟可预期；多余请求排队。
- trace JSONL 按目录存档，自动保留最近 `TRACE_KEEP` 个。
