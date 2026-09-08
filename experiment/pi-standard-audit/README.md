# Pi 垂类审计 Agent（能力探测实验）

在 Pi agent 底座上构建 chunk-studio 垂类 agent，用于**检测报告标准值审查**。与生产 7 步工作流不同，本实验只给模型一个任务定义 + 3 个 RAG 工具，**不加过程约束**，观察无约束模型在标准库检索辅助下的真实 agent 判定能力。

## 实验假设

- 当前生产审查效果不佳、流程复杂不清晰，瓶颈被怀疑在 judge。
- Pi 只做极少假设（工具少、提示词少），但 turn/会话/可移植性工程好。
- 给模型 `search_standards` / `read_chunk` / `read_report` 三工具 + 任务定义，看它能不能自己完成"检索 → 核实 → 判定"。

## 架构

```

┌─ runner.ts (Pi SDK, in-process) ─────────────────────────┐
│  createAgentSession                                      │
│    ├ customTools: [search_standards, read_chunk,         │
│    │               read_report]                          │
│    ├ setActiveToolsByName([...])  ← 白名单收缩到 3 工具  │
│    ├ resourceLoader.systemPromptOverride ← 任务人设      │
│    └ session.prompt(case) 逐 case                        │
│  session.subscribe → 录音轨 (tool_call/message_end)      │
└──────────────────────────────────────────────────────────┘
            │ HTTP (127.0.0.1:8000)
┌───────────▼──────────────────────────────────────────────┐
│ chunk-studio 后端 (sqlite local profile)                 │
│  /api/search        hybrid_search(..., file_ids)         │
│  /api/chunks/{id}   读标准片段                           │
│  /api/audit/reports/{name}   读检测报告                  │
└──────────────────────────────────────────────────────────┘
```

## 文件

| 文件 | 作用 |
|------|------|
| `tools.ts` | `defineTool` × 3：`search_standards` / `read_chunk` / `read_report`，HTTP 调后端 |
| `skill.md` | 任务定义（四向判定 + 证据可定位硬约束 + 输出 JSON schema）；给模型看 |
| `runner.ts` | SDK 驱动：建会话、白名单、逐 case 跑、录音轨、解析 verdict、对比 gold |
| `package.json` | 依赖 `@earendil-works/pi-coding-agent@0.85.1` + `typebox` + `tsx` |

## 前置

1. 后端以 sqlite 本地模式运行：
   ```powershell
   $env:PYTHONPATH='backend'
   $env:DEPLOYMENT_PROFILE='local'
   $env:DATABASE_BACKEND='sqlite'
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
   （可用 `curl http://127.0.0.1:8000/api/health` 确认 `deployment_profile: local`）

2. 模型走智谱官方 OpenAI 兼容端点（bigmodel.cn，已充值）：
   - `.env`（已 gitignore，含 key）配置 `PI_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`PI_MODEL=glm-5.3-flash`、`PI_API_KEY`
   - `CHUNK_STUDIO_API_BASE` — 可选，默认 `http://127.0.0.1:8000`
   - 换其他 OpenAI 兼容端点/模型：覆盖 `PI_BASE_URL` / `PI_MODEL` / `PI_API_KEY`

3. 装依赖：
   ```bash
   cd experiment/pi-standard-audit && npm install
   ```

## 运行

```bash
# 全部 10 个 case（用 npm scripts 自动加载 .env）
npm run run

# 前 3 个（快速冒烟）
node --env-file=.env --import tsx runner.ts --limit 3

# 指定 case
node --env-file=.env --import tsx runner.ts --case hbjc-4-r1
```

输出：

- `traces/<case_id>.jsonl` — 每个 case 的结构化会话轨迹（工具调用、assistant 消息摘要、最终文本）
- `results.json` — 汇总：每个 case 的 gold / 模型 verdict / 是否一致 / 最终文本 / 轨迹

## 预算与提速控制（env）

| 变量 | 默认 | 作用 |
|------|------|------|
| `PI_MAX_TOOL_CALLS` | `8` | 单 case 工具调用硬上限（Pi `tool_call` 拦截，超限强制模型收尾），控制 token/时间 |
| `PI_TRACE_DIR` | `./traces` | trace 输出目录 |
| `PI_REPORT_PATH` | 后端默认 HBJC 报告 | 评测报告 JSON |

## 判定对比

gold `judgment.status` 与模型输出 `verdict` 精确匹配计为 `exact_match`；解析失败计 `unparsed`。评分逻辑在 `runner.ts` 的 `summarize()`。

## 注意

- 这是能力探测实验，非生产替代，不改动生产 7 步流程。
- `file_ids` 已加入 `POST /api/search`（`backend/app/models.py` + `routers/search.py`），供检测依据锁定时限定检索范围，向后兼容（不传则全库检索）。
- **模型（2026-09-08 实测）**：使用智谱官方端点 `https://open.bigmodel.cn/api/paas/v4` + `glm-5.3-flash`（充值账号，稳定）。GLM 是强制 thinking 模型（`thinking.type` 仅支持 enabled），思考档位仅 `max/high/low`，runner 用 `thinkingLevel: "low"` 提速。此前在百炼 DashScope 开通的 GLM 呈"闪断"状态（`product is not activated` / `code 1004`），已弃用。
- **关于 maxTokens**：`max_tokens` 是总生成预算（含 reasoning），曾因设 4096 导致思考占满后 `finish_reason=length`、最终 JSON 被截断（6/10 parse fail）。已调至 16384。
- **已知工程问题与修复**：
  - extensionFactories 闭包计数器跨 session 泄漏曾导致预算从第 2 个 case 起提前拦截 → 改为模块级 `toolCallBudget` + 每 case `resetToolCallBudget()`。
  - `summarizeMessageText` 曾因运算符优先级错误把 trace 里的消息摘要全都写成 `[thinking]` → 已改为显式判断。
  - 报告 JSON 的 `report` 字段是外部 markdown 路径字符串而非分段对象；`read_report` 已改为返回 `parameters`/`model_decode`/`summary` 等真实内容，正文为外部路径时明确说明。