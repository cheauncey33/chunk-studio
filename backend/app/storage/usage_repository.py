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


LLM_USAGE_EVENTS_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS llm_usage_events (
    id                    TEXT PRIMARY KEY,
    workspace_id          TEXT NOT NULL,
    job_id                TEXT,
    run_id                TEXT,
    case_id               TEXT,
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


def _empty_bucket() -> dict[str, Any]:
    return {
        "request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_microunits": None,
        "pricing_missing_event_count": 0,
    }


def _accumulate(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["request_count"] += 1
    for key in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    ):
        value = row.get(key)
        if value is not None:
            bucket[key] += int(value)
    cost = row.get("cost_microunits")
    if cost is not None:
        bucket["cost_microunits"] = int(bucket["cost_microunits"] or 0) + int(cost)
    if row.get("pricing_missing"):
        bucket["pricing_missing_event_count"] += 1


def _summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _empty_bucket()
    by_stage: dict[str, dict[str, Any]] = {}
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_attempt: dict[int, dict[str, Any]] = {}
    by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        _accumulate(totals, row)
        stage = str(row.get("stage") or "other")
        stage_bucket = by_stage.setdefault(stage, {**_empty_bucket(), "stage": stage})
        _accumulate(stage_bucket, row)
        provider = str(row.get("provider") or "")
        model = str(row.get("model") or "")
        model_bucket = by_model.setdefault(
            (provider, model),
            {**_empty_bucket(), "provider": provider, "model": model},
        )
        _accumulate(model_bucket, row)
        attempt = row.get("job_attempt")
        if attempt is not None:
            attempt_bucket = by_attempt.setdefault(
                int(attempt), {**_empty_bucket(), "attempt": int(attempt)}
            )
            _accumulate(attempt_bucket, row)
        case_id = str(row.get("case_id") or "").strip()
        if case_id:
            case_bucket = by_case.setdefault(
                case_id, {**_empty_bucket(), "case_id": case_id}
            )
            _accumulate(case_bucket, row)
    return {
        "request_count": totals["request_count"],
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "cache_read_tokens": totals["cache_read_tokens"],
        "cache_write_tokens": totals["cache_write_tokens"],
        "total_tokens": totals["total_tokens"],
        "cost_microunits": totals["cost_microunits"],
        "currency": "USD",
        "pricing_missing_event_count": totals["pricing_missing_event_count"],
        "pricing_missing": totals["pricing_missing_event_count"] > 0,
        "by_stage": [
            by_stage[key]
            for key in sorted(by_stage)
        ],
        "by_model": [
            by_model[key]
            for key in sorted(by_model)
        ],
        "by_attempt": [
            by_attempt[key]
            for key in sorted(by_attempt)
        ],
        "by_case": [
            by_case[key]
            for key in sorted(by_case)
        ],
    }


def compact_usage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_count": int(summary.get("request_count") or 0),
        "total_tokens": int(summary.get("total_tokens") or 0),
        "cost_microunits": summary.get("cost_microunits"),
        "currency": summary.get("currency") or "USD",
        "pricing_missing": bool(summary.get("pricing_missing")),
        "pricing_missing_event_count": int(
            summary.get("pricing_missing_event_count") or 0
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

    def record_usage(self, event: UsageEvent) -> bool:
        created = event.created_at or _now_iso()
        event_id = event.id or event.request_id
        with db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO llm_usage_events (
                       id, workspace_id, job_id, run_id, case_id, job_attempt,
                       request_attempt, stage, provider, model, request_id, status,
                       input_tokens, output_tokens, reasoning_tokens,
                       cache_read_tokens, cache_write_tokens, total_tokens,
                       cost_microunits, pricing_missing, pricing_snapshot,
                       usage_source, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(request_id) DO NOTHING""",
                (
                    event_id,
                    str(event.workspace_id or "").strip(),
                    str(event.job_id or "").strip() or None,
                    str(event.run_id or "").strip() or None,
                    str(event.case_id or "").strip() or None,
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
        rows = self.list_usage_events(
            workspace_id=workspace_id,
            job_id=job_id,
            case_id=case_id,
            limit=5000,
        )
        summary = _summary_from_rows(rows)
        if job_id:
            summary["job_id"] = job_id
        if case_id:
            summary["case_id"] = case_id
        return summary


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

    def record_usage(self, event: UsageEvent) -> bool:
        created = event.created_at or _now_iso()
        event_id = event.id or event.request_id
        snapshot = json.dumps(event.pricing_snapshot, ensure_ascii=False) if event.pricing_snapshot else None
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO llm_usage_events (
                       id, workspace_id, job_id, run_id, case_id, job_attempt,
                       request_attempt, stage, provider, model, request_id, status,
                       input_tokens, output_tokens, reasoning_tokens,
                       cache_read_tokens, cache_write_tokens, total_tokens,
                       cost_microunits, pricing_missing, pricing_snapshot,
                       usage_source, created_at
                   ) VALUES (
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
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
        rows = self.list_usage_events(
            workspace_id=workspace_id,
            job_id=job_id,
            case_id=case_id,
            limit=5000,
        )
        summary = _summary_from_rows(rows)
        if job_id:
            summary["job_id"] = job_id
        if case_id:
            summary["case_id"] = case_id
        return summary


def get_usage_repository() -> UsageRepository:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL usage ledger")
        return PostgresUsageRepository(config.DATABASE_URL)
    return SqliteUsageRepository()
