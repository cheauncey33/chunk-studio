"""Read-only business analytics snapshot and controlled Text2SQL execution."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import config, db, llm


MAX_RESULT_ROWS = 200
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(?:attach|detach|pragma|insert|update|delete|replace|create|alter|drop|"
    r"vacuum|reindex|analyze|load_extension|readfile|writefile)\b",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"--|/\*")

SEMANTIC_SCHEMA = {
    "knowledge_bases": [
        "id", "name", "status", "file_count", "assistant_count", "created_at", "updated_at",
    ],
    "source_files": [
        "id", "name", "created_at", "knowledge_base_count", "approved_chunk_count",
    ],
    "audit_reports": [
        "report_name", "report_file_name", "assistant_id", "assistant_name",
        "knowledge_base_id", "knowledge_base_name", "started_at", "finished_at",
        "case_count", "supported_count", "mismatch_count",
        "insufficient_context_count", "not_audited_count",
    ],
    "audit_cases": [
        "report_name", "case_id", "project_name", "requirement", "status",
        "assistant_id", "knowledge_base_id",
    ],
}

SEMANTIC_TABLE_DESCRIPTIONS = {
    "knowledge_bases": "当前知识库及其启用文件、绑定助手数量。",
    "source_files": "文件级业务统计，不暴露原始文档正文。",
    "audit_reports": "审查报告级汇总指标。",
    "audit_cases": "审查报告中的逐条检测项目及判定状态。",
}

SEMANTIC_COLUMN_DESCRIPTIONS = {
    "knowledge_bases.status": "知识库状态，例如 active 或 archived。",
    "source_files.approved_chunk_count": "该文件当前 approved 切片数量。",
    "audit_reports.case_count": "报告包含的审查条目数。",
    "audit_cases.status": "supported、mismatch、insufficient_context 或 not_audited。",
}


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _audit_payloads(
    reports_dir: Path,
    *,
    source: sqlite3.Connection | None = None,
    workspace_id: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    if not reports_dir.exists():
        return payloads
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
            continue
        if not (
            payload.get("scope") == "full_report_audit"
            or payload.get("audit_mode") == "full_report"
            or path.name.startswith(("end_to_end_audit_", "hbjc_end_to_end_audit"))
        ):
            continue
        if workspace_id:
            explicit = str(payload.get("workspace_id") or "").strip()
            if explicit:
                if explicit != workspace_id:
                    continue
            elif source is not None:
                matched = False
                for field, table in (
                    ("assistant_id", "audit_assistants"),
                    ("knowledge_base_id", "knowledge_bases"),
                    ("report_file_id", "files"),
                    ("job_id", "jobs"),
                ):
                    value = str(payload.get(field) or "").strip()
                    if not value:
                        continue
                    row = source.execute(
                        f"SELECT workspace_id FROM {table} WHERE id=?",
                        (value,),
                    ).fetchone()
                    if row:
                        matched = str(row["workspace_id"]) == workspace_id
                        break
                if not matched and workspace_id != config.DEFAULT_WORKSPACE_ID:
                    continue
            elif workspace_id != config.DEFAULT_WORKSPACE_ID:
                continue
        payloads.append((path.name, payload))
    return payloads


def build_business_snapshot(
    *,
    source: sqlite3.Connection | None = None,
    reports_dir: Path | None = None,
    workspace_id: str | None = None,
) -> sqlite3.Connection:
    """Copy curated source fields into a new ephemeral SQLite database."""
    source_conn = source or db.get_conn()
    report_root = reports_dir or (config.DATA_DIR / "reports")
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    snapshot.executescript(
        """
        CREATE TABLE knowledge_bases (
            id TEXT, name TEXT, status TEXT, file_count INTEGER,
            assistant_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE source_files (
            id TEXT, name TEXT, created_at TEXT,
            knowledge_base_count INTEGER, approved_chunk_count INTEGER
        );
        CREATE TABLE audit_reports (
            report_name TEXT, report_file_name TEXT, assistant_id TEXT,
            assistant_name TEXT, knowledge_base_id TEXT, knowledge_base_name TEXT,
            started_at TEXT, finished_at TEXT, case_count INTEGER,
            supported_count INTEGER, mismatch_count INTEGER,
            insufficient_context_count INTEGER, not_audited_count INTEGER
        );
        CREATE TABLE audit_cases (
            report_name TEXT, case_id TEXT, project_name TEXT, requirement TEXT,
            status TEXT, assistant_id TEXT, knowledge_base_id TEXT
        );
        """
    )
    kb_sql = """SELECT kb.id, kb.name, kb.status, kb.created_at, kb.updated_at,
                  COUNT(DISTINCT CASE WHEN kbf.enabled=1 THEN kbf.file_id END) AS file_count,
                  COUNT(DISTINCT CASE WHEN akb.enabled=1 THEN akb.assistant_id END) AS assistant_count
             FROM knowledge_bases kb
             LEFT JOIN knowledge_base_files kbf ON kbf.knowledge_base_id=kb.id
             LEFT JOIN assistant_knowledge_bases akb ON akb.knowledge_base_id=kb.id
            {workspace_clause}
            GROUP BY kb.id, kb.name, kb.status, kb.created_at, kb.updated_at"""
    workspace_clause = "WHERE kb.workspace_id=?" if workspace_id else ""
    kb_rows = source_conn.execute(
        kb_sql.format(workspace_clause=workspace_clause),
        (workspace_id,) if workspace_id else (),
    ).fetchall()
    snapshot.executemany(
        "INSERT INTO knowledge_bases VALUES (?,?,?,?,?,?,?)",
        [tuple(row) for row in kb_rows],
    )
    file_sql = """SELECT f.id, f.name, f.created_at,
                  COUNT(DISTINCT CASE WHEN kbf.enabled=1 THEN kbf.knowledge_base_id END) AS knowledge_base_count,
                  COUNT(DISTINCT CASE WHEN c.status='approved' THEN c.id END) AS approved_chunk_count
             FROM files f
             LEFT JOIN knowledge_base_files kbf ON kbf.file_id=f.id
             LEFT JOIN chunks c ON c.file_id=f.id
            {workspace_clause}
            GROUP BY f.id, f.name, f.created_at"""
    workspace_clause = "WHERE f.workspace_id=?" if workspace_id else ""
    file_rows = source_conn.execute(
        file_sql.format(workspace_clause=workspace_clause),
        (workspace_id,) if workspace_id else (),
    ).fetchall()
    snapshot.executemany(
        "INSERT INTO source_files VALUES (?,?,?,?,?)",
        [tuple(row) for row in file_rows],
    )

    for report_name, payload in _audit_payloads(
        report_root,
        source=source_conn,
        workspace_id=workspace_id,
    ):
        cases = [item for item in payload.get("cases") or [] if isinstance(item, dict)]
        counts = {status: 0 for status in (
            "supported", "mismatch", "insufficient_context", "not_audited",
        )}
        for case in cases:
            judgment = _record(case.get("judgment"))
            status = str(judgment.get("status") or case.get("status") or "")
            if status in counts:
                counts[status] += 1
            snapshot.execute(
                "INSERT INTO audit_cases VALUES (?,?,?,?,?,?,?)",
                (
                    report_name,
                    str(case.get("case_id") or case.get("id") or ""),
                    str(_record(case.get("test_item")).get("project_name") or ""),
                    str(_record(case.get("reported_requirement")).get("text") or ""),
                    status,
                    str(payload.get("assistant_id") or ""),
                    str(payload.get("knowledge_base_id") or ""),
                ),
            )
        summary_counts = _record(_record(payload.get("summary")).get("judgments"))
        for status in counts:
            if isinstance(summary_counts.get(status), int):
                counts[status] = int(summary_counts[status])
        snapshot.execute(
            "INSERT INTO audit_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                report_name,
                str(payload.get("report_file_name") or ""),
                str(payload.get("assistant_id") or ""),
                str(payload.get("assistant_name") or ""),
                str(payload.get("knowledge_base_id") or ""),
                str(payload.get("knowledge_base_name") or ""),
                str(payload.get("started_at") or ""),
                str(payload.get("finished_at") or ""),
                len(cases),
                counts["supported"],
                counts["mismatch"],
                counts["insufficient_context"],
                counts["not_audited"],
            ),
        )
    snapshot.commit()
    return snapshot


def describe_business_schema() -> dict[str, Any]:
    return {
        "source": "持久化 SQLite 应用库 -> 每次查询临时复制的内存 SQLite 快照",
        "read_only": True,
        "tables": SEMANTIC_SCHEMA,
        "table_descriptions": SEMANTIC_TABLE_DESCRIPTIONS,
        "column_descriptions": SEMANTIC_COLUMN_DESCRIPTIONS,
        "chart_types": ["metric", "pie", "bar", "table"],
        "max_rows": MAX_RESULT_ROWS,
    }


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def get_business_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    metrics = dict(conn.execute(
        """SELECT
          (SELECT COUNT(*) FROM knowledge_bases WHERE status='active') AS knowledge_bases,
          (SELECT COUNT(*) FROM source_files) AS files,
          (SELECT COALESCE(SUM(approved_chunk_count),0) FROM source_files) AS approved_chunks,
          (SELECT COUNT(*) FROM audit_reports) AS audit_reports,
          (SELECT COUNT(*) FROM audit_cases) AS audit_cases"""
    ).fetchone())
    statuses = _rows(
        conn,
        """SELECT CASE
                    WHEN status IN ('supported','mismatch','insufficient_context','not_audited')
                    THEN status ELSE 'other'
                  END AS label,
                  COUNT(*) AS value
             FROM audit_cases
            GROUP BY label ORDER BY value DESC, label""",
    )
    return {
        "metrics": metrics,
        "status_distribution": statuses,
        "denominator": sum(int(row["value"]) for row in statuses),
    }


def aggregate_audit_results(
    conn: sqlite3.Connection,
    *,
    group_by: str,
    status: str | None = None,
    report_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fixed bounded group-by for the Agent/tool adapter."""
    allowed_groups = {
        "status",
        "project_name",
        "report_name",
        "assistant_id",
        "knowledge_base_id",
    }
    if group_by not in allowed_groups:
        raise ValueError("unsupported audit aggregation field")
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if report_name:
        clauses.append("report_name=?")
        params.append(report_name)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 200)))
    return [
        dict(row)
        for row in conn.execute(
            f"""SELECT {group_by} AS label, COUNT(*) AS value
                  FROM audit_cases {where}
                 GROUP BY {group_by}
                 ORDER BY value DESC, label
                 LIMIT ?""",
            params,
        ).fetchall()
    ]


def fixed_query_plan(question: str) -> dict[str, Any] | None:
    """Zero-token plans for high-frequency metrics and distributions."""
    normalized = re.sub(r"\s+", "", question).lower()
    wants_count = any(token in normalized for token in ("多少", "几个", "数量", "总数", "count"))
    if wants_count and "知识库" in normalized:
        return {"title": "知识库数量", "chart_type": "metric", "sql": "SELECT COUNT(*) AS value FROM knowledge_bases WHERE status='active'"}
    if wants_count and any(token in normalized for token in ("检测记录", "审查记录", "审查项", "case")):
        return {"title": "审查记录数量", "chart_type": "metric", "sql": "SELECT COUNT(*) AS value FROM audit_cases"}
    if wants_count and any(token in normalized for token in ("审查报告", "报告记录")):
        return {"title": "审查报告数量", "chart_type": "metric", "sql": "SELECT COUNT(*) AS value FROM audit_reports"}
    if wants_count and any(token in normalized for token in ("切片", "chunk")):
        return {"title": "已批准切片数量", "chart_type": "metric", "sql": "SELECT COALESCE(SUM(approved_chunk_count),0) AS value FROM source_files"}
    if wants_count and "文件" in normalized:
        return {"title": "文件数量", "chart_type": "metric", "sql": "SELECT COUNT(*) AS value FROM source_files"}
    if any(token in normalized for token in ("状态分布", "占比", "饼图")):
        return {
            "title": "审查状态分布",
            "chart_type": "pie",
            "sql": "SELECT CASE WHEN status IN ('supported','mismatch','insufficient_context','not_audited') THEN status ELSE 'other' END AS label, COUNT(*) AS value FROM audit_cases GROUP BY label ORDER BY value DESC",
        }
    if (
        any(token in normalized for token in ("按检测项目", "按项目", "各项目"))
        and any(token in normalized for token in ("不符合", "mismatch"))
    ):
        return {
            "title": "按检测项目统计不符合项",
            "chart_type": "bar",
            "sql": "SELECT project_name AS label, COUNT(*) AS value FROM audit_cases WHERE status='mismatch' GROUP BY project_name ORDER BY value DESC, label",
        }
    return None


def _text2sql_plan(question: str, *, model: str = llm.DEFAULT_MODEL) -> dict[str, Any]:
    prompt = (
        "You generate one read-only SQLite query for a curated business analytics schema. "
        "Return JSON only with sql, title, chart_type. chart_type is metric, pie, bar, or table. "
        "Never use PRAGMA, comments, writes, schema changes, attachments, or unavailable fields. "
        "Use exact denominators and COUNT(DISTINCT ...) when the question asks unique entities."
    )
    result = llm.chat_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"question": question, "schema": SEMANTIC_SCHEMA}, ensure_ascii=False)},
        ],
        model=model,
        temperature=0,
    )
    return {
        "sql": str(result.get("sql") or "").strip(),
        "title": str(result.get("title") or "查询结果").strip()[:120],
        "chart_type": str(result.get("chart_type") or "table").strip(),
    }


def validate_readonly_sql(sql: str) -> str:
    statement = str(sql or "").strip().rstrip(";").strip()
    if not statement or not re.match(r"^(?:select|with)\b", statement, re.IGNORECASE):
        raise ValueError("only SELECT or WITH queries are allowed")
    if ";" in statement or _COMMENT_RE.search(statement):
        raise ValueError("multiple statements and SQL comments are not allowed")
    if _FORBIDDEN_SQL_RE.search(statement):
        raise ValueError("query contains a forbidden SQL operation")
    return statement


def execute_readonly_query(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    statement = validate_readonly_sql(sql)
    allowed_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }

    def authorizer(action: int, _arg1: str | None, _arg2: str | None, _db: str | None, _source: str | None) -> int:
        return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    conn.set_progress_handler(lambda: 1, 250_000)
    try:
        cursor = conn.execute(f"SELECT * FROM ({statement}) LIMIT {MAX_RESULT_ROWS + 1}")
        names = [item[0] for item in cursor.description or []]
        fetched = cursor.fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"query rejected: {exc}") from exc
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)
    truncated = len(fetched) > MAX_RESULT_ROWS
    rows = [dict(row) for row in fetched[:MAX_RESULT_ROWS]]
    return {"columns": names, "rows": rows, "truncated": truncated}


def render_chart_spec(chart_type: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    kind = chart_type if chart_type in {"metric", "pie", "bar", "table"} else "table"
    if kind == "table":
        return None
    if kind == "metric" and rows:
        value = next(iter(rows[0].values()), None)
        return {"type": "metric", "value": value}
    if kind in {"pie", "bar"} and rows and len(rows[0]) >= 2:
        keys = list(rows[0])
        data = [
            {"label": str(row.get(keys[0]) or "未命名"), "value": float(row.get(keys[1]) or 0)}
            for row in rows
        ]
        return {"type": kind, "data": data, "denominator": sum(item["value"] for item in data)}
    return None


def query_business_data(
    question: str,
    *,
    source: sqlite3.Connection | None = None,
    reports_dir: Path | None = None,
    model: str = llm.DEFAULT_MODEL,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    normalized = str(question or "").strip()
    if not normalized:
        raise ValueError("question must not be blank")
    plan = fixed_query_plan(normalized)
    route = "fixed_metric" if plan else "text2sql"
    active_plan = plan or _text2sql_plan(normalized, model=model)
    snapshot = build_business_snapshot(
        source=source,
        reports_dir=reports_dir,
        workspace_id=workspace_id,
    )
    try:
        result = execute_readonly_query(snapshot, str(active_plan["sql"]))
    finally:
        snapshot.close()
    chart = render_chart_spec(str(active_plan.get("chart_type") or "table"), result["rows"])
    answer = str(active_plan.get("title") or "查询结果")
    if chart and chart.get("type") == "metric":
        answer = f"{answer}：{chart.get('value')}"
    else:
        answer = f"{answer}，共返回 {len(result['rows'])} 行。"
    return {
        "answer": answer,
        "route": route,
        "question": normalized,
        "sql": validate_readonly_sql(str(active_plan["sql"])),
        "model": model if route == "text2sql" else None,
        "columns": result["columns"],
        "rows": result["rows"],
        "truncated": result["truncated"],
        "chart": chart,
    }
