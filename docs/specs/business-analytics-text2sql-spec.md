# Business Analytics and Controlled Text2SQL Demo Spec

Status: implementation specification  
Branch: `codex/pi-agentic-rag`  
Priority: audit aggregation correctness, query safety, low token cost

## 1. Goal

Add one global “业务问答” page for audit aggregation, pie/bar charts, and natural-language business queries such as:

- 有多少个知识库、标准文件和已批准切片？
- 最近审查了多少份报告？
- 某份报告的不符合项占比是多少？
- 按项目统计 mismatch 数量。

Knowledge-document Q&A remains inside each knowledge base. This page queries system business metadata and persisted audit results; it does not search standard text.

## 2. Marketplace decision

The official MCP Registry was reviewed. Generic SQLite servers expose a much broader tool surface than this demo needs, including capabilities that would require a separate security audit. The historical SQLite reference server is not a current core reference server.

Decision:

- Do not make a third-party SQLite MCP server a production dependency.
- Implement domain tools behind MCP-compatible JSON contracts.
- Keep execution local, read-only, and limited to a curated analytics snapshot.
- A future MCP adapter may expose these same contracts without changing the Agent or UI.

No generic filesystem, shell, database-write, schema-change, attach-database, pragma, or arbitrary raw-SQL tool is exposed to the model.

## 3. Architecture

```text
User question
  -> deterministic intent router
       -> common metric template (zero LLM calls)
       -> or one Flash Text2SQL planning call
  -> SQL policy validator
  -> ephemeral curated SQLite snapshot
  -> bounded rows
  -> deterministic answer + chart specification
```

The snapshot copies only approved business fields from the source database and report JSON files. Generated SQL never runs on `chunkstudio.db`.

## 4. Curated semantic tables

### `knowledge_bases`

`id`, `name`, `status`, `file_count`, `assistant_count`, `created_at`, `updated_at`

### `source_files`

`id`, `name`, `created_at`, `knowledge_base_count`, `approved_chunk_count`

### `audit_reports`

`report_name`, `report_file_name`, `assistant_id`, `assistant_name`, `knowledge_base_id`, `knowledge_base_name`, `started_at`, `finished_at`, `case_count`, and the four judgment counts.

### `audit_cases`

`report_name`, `case_id`, `project_name`, `requirement`, `status`, `assistant_id`, `knowledge_base_id`

Full report Markdown, chunk text, API keys, prompts, model traces, and file paths are excluded.

## 5. Tool contracts

### T1 `get_business_overview`

Fixed aggregation for knowledge bases, files, approved chunks, audit reports, audit cases, and judgment distribution. Zero model calls.

### T2 `aggregate_audit_results`

Fixed group-by over `status`, `project_name`, `report_name`, `assistant_id`, or `knowledge_base_id`, with optional exact scope filters. Zero model calls.

### T3 `describe_business_schema`

Returns the curated schema and allowed chart types. It never exposes the source database schema.

### T4 `query_business_data`

Natural-language-to-SQL fallback. One `deepseek-v4-flash` JSON call, thinking disabled. Returns `sql`, `title`, and `chart_type` only. Runtime validates and executes it.

### T5 `render_chart_spec`

Deterministically converts bounded tabular results to `pie`, `bar`, `metric`, or `table` data. It is not another model call.

## 6. SQL safety policy

- Only one `SELECT` or `WITH ... SELECT` statement.
- Reject comments, semicolon-separated statements, PRAGMA, ATTACH, DDL, DML, virtual tables, and extension loading.
- Execute only on a new in-memory SQLite snapshot.
- SQLite authorizer permits read/select/function opcodes only.
- Progress handler interrupts excessive work.
- Maximum returned rows: 200.
- Model SQL identifiers must belong to the curated schema.
- Errors return a safe diagnostic; there is no automatic model retry in the demo.

## 7. State and trace

Each response records:

- `route`: `fixed_metric` or `text2sql`
- normalized question
- generated SQL
- rows/columns and truncation state
- chart specification
- model name only when Text2SQL was used
- validation/execution error without secrets

Conversation persistence is deferred. The UI may keep current-page messages in memory, but every business query is independently reproducible.

## 8. UI

Global navigation adds “业务问答”. The page contains:

- current overview metric cards
- audit-status pie chart
- natural-language query box with example prompts
- answer, executed SQL, result table, and optional pie/bar visualization

No chart package is added for the demo; bounded CSS/SVG rendering avoids bundle and dependency cost.

## 9. Acceptance criteria

- Common count/distribution questions use zero LLM calls.
- Open-ended business questions use at most one Flash call.
- All executed SQL is read-only and runs on the ephemeral snapshot.
- Audit status totals equal the persisted report cases used by the snapshot.
- Query and chart outputs retain exact denominators.
- Backend unit/security tests pass.
- Frontend lint and production build pass.
- Existing recovery/retrieval tests do not regress.

## 10. Deferred

- External production MCP installation.
- Direct access to arbitrary customer databases.
- SQL writes or report mutation.
- Persistent multi-user conversations and authorization roles.
- Cross-database joins and scheduled dashboards.

## 11. Implementation and verification record

Implemented on `codex/pi-agentic-rag` with the following demo snapshot observed on
2026-08-05. Runtime data may change independently of source code.

- 3 knowledge bases, 34 source files, and 1,175 approved chunks.
- 48 audit reports and 1,688 audit cases.
- Audit pie denominator: 1,688. This includes 196 legacy/unknown statuses grouped
  as `other`, so no persisted case silently disappears from the chart.
- “按检测项目统计不符合项” used the fixed route with zero model calls and returned
  11 project groups totaling 95 mismatch cases.
- 65 focused backend tests passed, including Text2SQL call count, SQL rejection,
  legacy-status denominator, recovery gate/tools, and report workflow coverage.
- Frontend lint completed with pre-existing warnings only; production build passed.
  The lazy analytics chunk is 8.35 kB (3.15 kB gzip), with no chart dependency added.
