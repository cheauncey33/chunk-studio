"""Persistent LLM usage ledger for SQLite and PostgreSQL.

Events are the source of truth. Aggregates are always SUM() over this table.
The same ``request_id`` inserts once (ON CONFLICT DO NOTHING). Retry of a
job attempt writes new rows; it never replaces earlier attempt rows.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Protocol

from .. import config, db


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


def _json_load(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class UsageEvent:
    request_id: str
    id: str = ""
    workspace_id: str = ""
    job_id: str | None = None
    run_id: str | None = None
    case_id: str | None = None
    conversation_id: str | None = None
    job_attempt: int | None = None
    request_attempt: int = 1
    stage: str = "other"
    provider: str = ""
    model: str = ""
    status: str = "success"
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    cost_microunits: int | None = None
    pricing_missing: bool = False
    pricing_snapshot: dict[str, Any] | None = None
    usage_source: str = "unknown"
    created_at: str = ""


class UsageRepository(Protocol):
    def record_usage(self, event: UsageEvent) -> bool: ...

    def get_usage_summary(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]: ...

    def list_usage_events(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def lookup_job_workspace(self, job_id: str) -> str | None: ...

    def lookup_conversation_workspace(self, conversation_id: str) -> str | None: ...

    def get_batch_usage_summary(
        self, *, workspace_id: str, batch_id: str
    ) -> dict[str, Any]: ...

    def get_batch_usage_summaries(
        self, *, workspace_id: str, batch_ids: list[str]
    ) -> dict[str, dict[str, Any]]: ...


LLM_USAGE_EVENTS_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS llm_usage_events (
    id                    TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL,
    job_id                TEXT,
    run_id                TEXT,
    case_id               TEXT,
    conversation_id       TEXT,
    job_attempt           INTEGER,
    request_attempt       INTEGER NOT NULL DEFAULT 1,
    stage                 TEXT NOT NULL DEFAULT 'other',
    provider              TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    request_id            TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'success',
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    reasoning_tokens      INTEGER,
    cache_read_tokens     INTEGER,
    cache_write_tokens    INTEGER,
    total_tokens          INTEGER,
    cost_microunits       INTEGER,
    pricing_missing       INTEGER NOT NULL DEFAULT 0,
    pricing_snapshot      TEXT,
    usage_source          TEXT NOT NULL DEFAULT 'unknown',
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_job
    ON llm_usage_events(workspace_id, job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_case
    ON llm_usage_events(workspace_id, job_id, case_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_attempt
    ON llm_usage_events(job_id, job_attempt);
"""


LLM_USAGE_EVENTS_POSTGRES_DDL = [
    """CREATE TABLE IF NOT EXISTS llm_usage_events (
         id TEXT PRIMARY KEY,
         workspace_id TEXT NOT NULL,
         job_id TEXT,
         run_id TEXT,
         case_id TEXT,
         conversation_id TEXT,
         job_attempt INTEGER,
         request_attempt INTEGER NOT NULL DEFAULT 1,
         stage TEXT NOT NULL DEFAULT 'other',
         provider TEXT NOT NULL DEFAULT '',
         model TEXT NOT NULL DEFAULT '',
         request_id TEXT NOT NULL UNIQUE,
         status TEXT NOT NULL DEFAULT 'success',
         input_tokens INTEGER,
         output_tokens INTEGER,
         reasoning_tokens INTEGER,
         cache_read_tokens INTEGER,
         cache_write_tokens INTEGER,
         total_tokens INTEGER,
         cost_microunits BIGINT,
         pricing_missing BOOLEAN NOT NULL DEFAULT FALSE,
         pricing_snapshot JSONB,
         usage_source TEXT NOT NULL DEFAULT 'unknown',
         created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_llm_usage_events_job ON llm_usage_events(workspace_id, job_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_llm_usage_events_case ON llm_usage_events(workspace_id, job_id, case_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_usage_events_attempt ON llm_usage_events(job_id, job_attempt)",
    "ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS conversation_id TEXT",
    "CREATE INDEX IF NOT EXISTS ix_llm_usage_events_conversation ON llm_usage_events(workspace_id, conversation_id)",
]


def _row_to_event(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["pricing_snapshot"] = _json_load(item.get("pricing_snapshot"))
    missing = item.get("pricing_missing")
    item["pricing_missing"] = bool(missing) and missing not in (0, "0", "false", "False")
    for key in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "cost_microunits",
        "job_attempt",
        "request_attempt",
    ):
        item[key] = _int_or_none(item.get(key))
    created = item.get("created_at")
    if created is not None and not isinstance(created, str):
        item["created_at"] = created.isoformat()
    return item


def _sql_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _usage_where(
    placeholder: str,
    workspace_id: str,
    job_id: str | None,
    case_id: str | None,
) -> tuple[str, list[Any]]:
    clauses = [f"workspace_id={placeholder}"]
    params: list[Any] = [str(workspace_id)]
    if job_id:
        clauses.append(f"job_id={placeholder}")
        params.append(job_id)
    if case_id:
        clauses.append(f"case_id={placeholder}")
        params.append(case_id)
    return " AND ".join(clauses), params


def _aggregate_select(*, sqlite: bool, prefix: str = "") -> str:
    col = f"{prefix}." if prefix else ""
    missing_sum = (
        f"COALESCE(SUM({col}pricing_missing), 0)"
        if sqlite
        else f"COALESCE(SUM(CASE WHEN {col}pricing_missing THEN 1 ELSE 0 END), 0)"
    )
    return f"""
        COUNT(*) AS request_count,
        COALESCE(SUM({col}input_tokens), 0) AS input_tokens,
        COALESCE(SUM({col}output_tokens), 0) AS output_tokens,
        COALESCE(SUM({col}reasoning_tokens), 0) AS reasoning_tokens,
        COALESCE(SUM({col}cache_read_tokens), 0) AS cache_read_tokens,
        COALESCE(SUM({col}cache_write_tokens), 0) AS cache_write_tokens,
        COALESCE(SUM({col}total_tokens), 0) AS total_tokens,
        COALESCE(SUM({col}cost_microunits), 0) AS known_cost_microunits,
        COALESCE(SUM(CASE WHEN {col}cost_microunits IS NULL THEN 1 ELSE 0 END), 0)
            AS incomplete_cost_event_count,
        {missing_sum} AS pricing_missing_event_count,
        COALESCE(SUM(CASE WHEN {col}usage_source = 'unknown' THEN 1 ELSE 0 END), 0)
            AS usage_unknown_event_count
    """


def _sql_batch_usage_summary(
    execute: Any,
    *,
    placeholder: str,
    sqlite: bool,
    workspace_id: str,
    batch_id: str,
) -> dict[str, Any]:
    """Unbounded SUM over child-job ledger rows. Do not load events into Python."""
    agg = _aggregate_select(sqlite=sqlite, prefix="u")
    totals_row = execute(
        f"""SELECT {agg}
            FROM llm_usage_events u
            INNER JOIN audit_batch_items i
              ON i.audit_job_id = u.job_id
             AND i.workspace_id = u.workspace_id
            WHERE i.batch_id = {placeholder}
              AND i.workspace_id = {placeholder}""",
        [str(batch_id), str(workspace_id)],
    ).fetchone()
    summary = _finalize_aggregate(dict(totals_row) if totals_row is not None else None)
    summary["currency"] = "USD"
    summary["batch_id"] = batch_id
    return summary


def _sql_batch_usage_summaries(
    execute: Any,
    *,
    placeholder: str,
    sqlite: bool,
    workspace_id: str,
    batch_ids: list[str],
) -> dict[str, dict[str, Any]]:
    ids = [str(item).strip() for item in batch_ids if str(item).strip()]
    if not ids:
        return {}
    agg = _aggregate_select(sqlite=sqlite, prefix="u")
    in_clause = ", ".join(placeholder for _ in ids)
    rows = execute(
        f"""SELECT i.batch_id AS batch_id, {agg}
            FROM llm_usage_events u
            INNER JOIN audit_batch_items i
              ON i.audit_job_id = u.job_id
             AND i.workspace_id = u.workspace_id
            WHERE i.workspace_id = {placeholder}
              AND i.batch_id IN ({in_clause})
            GROUP BY i.batch_id""",
        [str(workspace_id), *ids],
    ).fetchall()
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        batch_id = str(data.get("batch_id") or "")
        if not batch_id:
            continue
        summary = _finalize_aggregate(data)
        summary["currency"] = "USD"
        summary["batch_id"] = batch_id
        found[batch_id] = summary
    empty = _finalize_aggregate(None)
    empty["currency"] = "USD"
    for batch_id in ids:
        if batch_id not in found:
            found[batch_id] = {**empty, "batch_id": batch_id}
    return found


def _finalize_aggregate(
    row: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = dict(row or {})
    known = _sql_int(data.get("known_cost_microunits"))
    complete = _sql_int(data.get("incomplete_cost_event_count")) == 0
    pricing_missing_count = _sql_int(data.get("pricing_missing_event_count"))
    usage_unknown_count = _sql_int(data.get("usage_unknown_event_count"))
    bucket: dict[str, Any] = {
        "request_count": _sql_int(data.get("request_count")),
        "input_tokens": _sql_int(data.get("input_tokens")),
        "output_tokens": _sql_int(data.get("output_tokens")),
        "reasoning_tokens": _sql_int(data.get("reasoning_tokens")),
        "cache_read_tokens": _sql_int(data.get("cache_read_tokens")),
        "cache_write_tokens": _sql_int(data.get("cache_write_tokens")),
        "total_tokens": _sql_int(data.get("total_tokens")),
        "known_cost_microunits": known,
        "cost_microunits": known if complete else None,
        "cost_complete": complete,
        "pricing_missing_event_count": pricing_missing_count,
        "usage_unknown_event_count": usage_unknown_count,
        "pricing_missing": pricing_missing_count > 0,
    }
    if extra:
        bucket.update(extra)
    return bucket


def _sql_usage_summary(
    execute: Any,
    *,
    placeholder: str,
    sqlite: bool,
    workspace_id: str,
    job_id: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Unbounded accounting aggregate. Do not SELECT rows and SUM in Python."""
    where, params = _usage_where(placeholder, workspace_id, job_id, case_id)
    agg = _aggregate_select(sqlite=sqlite)
    totals_row = execute(
        f"SELECT {agg} FROM llm_usage_events WHERE {where}",
        params,
    ).fetchone()
    summary = _finalize_aggregate(dict(totals_row) if totals_row is not None else None)
    summary["currency"] = "USD"

    stage_rows = execute(
        f"SELECT stage, {agg} FROM llm_usage_events WHERE {where} "
        "GROUP BY stage ORDER BY stage",
        params,
    ).fetchall()
    summary["by_stage"] = [
        _finalize_aggregate(
            dict(row), extra={"stage": str(dict(row).get("stage") or "other")}
        )
        for row in stage_rows
    ]

    model_rows = execute(
        f"SELECT provider, model, {agg} FROM llm_usage_events WHERE {where} "
        "GROUP BY provider, model ORDER BY provider, model",
        params,
    ).fetchall()
    summary["by_model"] = [
        _finalize_aggregate(
            dict(row),
            extra={
                "provider": str(dict(row).get("provider") or ""),
                "model": str(dict(row).get("model") or ""),
            },
        )
        for row in model_rows
    ]

    attempt_rows = execute(
        f"SELECT job_attempt, {agg} FROM llm_usage_events WHERE {where} "
        "AND job_attempt IS NOT NULL GROUP BY job_attempt ORDER BY job_attempt",
        params,
    ).fetchall()
    summary["by_attempt"] = [
        _finalize_aggregate(
            dict(row), extra={"attempt": _sql_int(dict(row).get("job_attempt"))}
        )
        for row in attempt_rows
    ]

    case_rows = execute(
        f"SELECT case_id, {agg} FROM llm_usage_events WHERE {where} "
        "AND case_id IS NOT NULL AND case_id != '' "
        "GROUP BY case_id ORDER BY case_id",
        params,
    ).fetchall()
    summary["by_case"] = [
        _finalize_aggregate(
            dict(row), extra={"case_id": str(dict(row).get("case_id") or "")}
        )
        for row in case_rows
    ]

    if job_id:
        summary["job_id"] = job_id
    if case_id:
        summary["case_id"] = case_id
    return summary


def compact_usage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_count": int(summary.get("request_count") or 0),
        "total_tokens": int(summary.get("total_tokens") or 0),
        "known_cost_microunits": int(summary.get("known_cost_microunits") or 0),
        "cost_microunits": summary.get("cost_microunits"),
        "cost_complete": bool(summary.get("cost_complete")),
        "currency": summary.get("currency") or "USD",
        "pricing_missing": bool(summary.get("pricing_missing")),
        "pricing_missing_event_count": int(
            summary.get("pricing_missing_event_count") or 0
        ),
        "usage_unknown_event_count": int(
            summary.get("usage_unknown_event_count") or 0
        ),
    }


class SqliteUsageRepository:
    """Ledger writes go through ``db.transaction()`` (process-wide SQLite lock)."""

    def lookup_job_workspace(self, job_id: str) -> str | None:
        jid = str(job_id or "").strip()
        if not jid:
            return None
        row = db.get_conn().execute(
            "SELECT workspace_id FROM jobs WHERE id=?",
            (jid,),
        ).fetchone()
        return str(row["workspace_id"]) if row and row["workspace_id"] else None

    def lookup_conversation_workspace(self, conversation_id: str) -> str | None:
        cid = str(conversation_id or "").strip()
        if not cid:
            return None
        row = db.get_conn().execute(
            "SELECT workspace_id FROM chat_conversations WHERE id=?",
            (cid,),
        ).fetchone()
        return str(row["workspace_id"]) if row and row["workspace_id"] else None

    def record_usage(self, event: UsageEvent) -> bool:
        created = event.created_at or _now_iso()
        event_id = event.id or event.request_id
        with db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO llm_usage_events (
                       id, workspace_id, job_id, run_id, case_id, conversation_id,
                       job_attempt, request_attempt, stage, provider, model, request_id, status,
                       input_tokens, output_tokens, reasoning_tokens,
                       cache_read_tokens, cache_write_tokens, total_tokens,
                       cost_microunits, pricing_missing, pricing_snapshot,
                       usage_source, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(request_id) DO NOTHING""",
                (
                    event_id,
                    str(event.workspace_id or "").strip(),
                    str(event.job_id or "").strip() or None,
                    str(event.run_id or "").strip() or None,
                    str(event.case_id or "").strip() or None,
                    str(event.conversation_id or "").strip() or None,
                    event.job_attempt,
                    max(1, int(event.request_attempt or 1)),
                    str(event.stage or "other"),
                    str(event.provider or ""),
                    str(event.model or ""),
                    str(event.request_id).strip(),
                    str(event.status or "success"),
                    event.input_tokens,
                    event.output_tokens,
                    event.reasoning_tokens,
                    event.cache_read_tokens,
                    event.cache_write_tokens,
                    event.total_tokens,
                    event.cost_microunits,
                    1 if event.pricing_missing else 0,
                    _json_dump(event.pricing_snapshot) if event.pricing_snapshot else None,
                    str(event.usage_source or "unknown"),
                    created,
                ),
            )
            return cursor.rowcount > 0

    def list_usage_events(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id=?"]
        params: list[Any] = [str(workspace_id)]
        if job_id:
            clauses.append("job_id=?")
            params.append(job_id)
        if case_id:
            clauses.append("case_id=?")
            params.append(case_id)
        params.append(max(1, min(int(limit), 5000)))
        rows = db.get_conn().execute(
            "SELECT * FROM llm_usage_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at ASC, id ASC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def get_usage_summary(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        return _sql_usage_summary(
            db.get_conn().execute,
            placeholder="?",
            sqlite=True,
            workspace_id=workspace_id,
            job_id=job_id,
            case_id=case_id,
        )

    def get_batch_usage_summary(
        self, *, workspace_id: str, batch_id: str
    ) -> dict[str, Any]:
        return _sql_batch_usage_summary(
            db.get_conn().execute,
            placeholder="?",
            sqlite=True,
            workspace_id=workspace_id,
            batch_id=batch_id,
        )

    def get_batch_usage_summaries(
        self, *, workspace_id: str, batch_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        return _sql_batch_usage_summaries(
            db.get_conn().execute,
            placeholder="?",
            sqlite=True,
            workspace_id=workspace_id,
            batch_ids=batch_ids,
        )


@dataclass(frozen=True)
class PostgresUsageRepository:
    dsn: str

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL usage ledger requires the optional psycopg dependency"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def lookup_job_workspace(self, job_id: str) -> str | None:
        jid = str(job_id or "").strip()
        if not jid:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM jobs WHERE id=%s",
                (jid,),
            ).fetchone()
        if not row:
            return None
        workspace = row.get("workspace_id") if isinstance(row, dict) else row[0]
        return str(workspace) if workspace else None

    def lookup_conversation_workspace(self, conversation_id: str) -> str | None:
        cid = str(conversation_id or "").strip()
        if not cid:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM chat_conversations WHERE id=%s",
                (cid,),
            ).fetchone()
        if not row:
            return None
        workspace = row.get("workspace_id") if isinstance(row, dict) else row[0]
        return str(workspace) if workspace else None

    def record_usage(self, event: UsageEvent) -> bool:
        created = event.created_at or _now_iso()
        event_id = event.id or event.request_id
        snapshot = json.dumps(event.pricing_snapshot, ensure_ascii=False) if event.pricing_snapshot else None
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO llm_usage_events (
                       id, workspace_id, job_id, run_id, case_id, conversation_id, job_attempt,
                       request_attempt, stage, provider, model, request_id, status,
                       input_tokens, output_tokens, reasoning_tokens,
                       cache_read_tokens, cache_write_tokens, total_tokens,
                       cost_microunits, pricing_missing, pricing_snapshot,
                       usage_source, created_at
                   ) VALUES (
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                   )
                   ON CONFLICT (request_id) DO NOTHING
                   RETURNING id""",
                (
                    event_id,
                    str(event.workspace_id or "").strip(),
                    str(event.job_id or "").strip() or None,
                    str(event.run_id or "").strip() or None,
                    str(event.case_id or "").strip() or None,
                    str(event.conversation_id or "").strip() or None,
                    event.job_attempt,
                    max(1, int(event.request_attempt or 1)),
                    str(event.stage or "other"),
                    str(event.provider or ""),
                    str(event.model or ""),
                    str(event.request_id).strip(),
                    str(event.status or "success"),
                    event.input_tokens,
                    event.output_tokens,
                    event.reasoning_tokens,
                    event.cache_read_tokens,
                    event.cache_write_tokens,
                    event.total_tokens,
                    event.cost_microunits,
                    bool(event.pricing_missing),
                    snapshot,
                    str(event.usage_source or "unknown"),
                    created,
                ),
            ).fetchone()
        return row is not None

    def list_usage_events(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id=%s"]
        params: list[Any] = [str(workspace_id)]
        if job_id:
            clauses.append("job_id=%s")
            params.append(job_id)
        if case_id:
            clauses.append("case_id=%s")
            params.append(case_id)
        params.append(max(1, min(int(limit), 5000)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_usage_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, id ASC LIMIT %s",
                params,
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def get_usage_summary(
        self,
        *,
        workspace_id: str,
        job_id: str | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            return _sql_usage_summary(
                conn.execute,
                placeholder="%s",
                sqlite=False,
                workspace_id=workspace_id,
                job_id=job_id,
                case_id=case_id,
            )

    def get_batch_usage_summary(
        self, *, workspace_id: str, batch_id: str
    ) -> dict[str, Any]:
        with self._connect() as conn:
            return _sql_batch_usage_summary(
                conn.execute,
                placeholder="%s",
                sqlite=False,
                workspace_id=workspace_id,
                batch_id=batch_id,
            )

    def get_batch_usage_summaries(
        self, *, workspace_id: str, batch_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            return _sql_batch_usage_summaries(
                conn.execute,
                placeholder="%s",
                sqlite=False,
                workspace_id=workspace_id,
                batch_ids=batch_ids,
            )


def get_usage_repository() -> UsageRepository:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL usage ledger")
        return PostgresUsageRepository(config.DATABASE_URL)
    return SqliteUsageRepository()
