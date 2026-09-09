"""Audit batch orchestration tables for SQLite and PostgreSQL.

Child audit jobs remain the source of truth for execution. These tables
group jobs for scheduling, listing, and usage aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .. import config, db


BATCH_MODE_NIGHT = "night"
BATCH_MAX_CONCURRENCY = 1
NIGHT_BATCH_PRIORITY = -5
NIGHT_BATCH_MAX_REPORTS = 200

BATCH_STATUS_SCHEDULED = "scheduled"
BATCH_STATUS_RUNNING = "running"
BATCH_STATUS_COMPLETED = "completed"
BATCH_STATUS_PARTIAL_FAILED = "partial_failed"
BATCH_STATUS_FAILED = "failed"

BATCH_STATUSES = frozenset(
    {
        BATCH_STATUS_SCHEDULED,
        BATCH_STATUS_RUNNING,
        BATCH_STATUS_COMPLETED,
        BATCH_STATUS_PARTIAL_FAILED,
        BATCH_STATUS_FAILED,
    }
)


AUDIT_BATCHES_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS audit_batches (
    id                 TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL,
    assistant_id       TEXT NOT NULL,
    naming_rule_file_id TEXT,
    mode               TEXT NOT NULL DEFAULT 'night',
    status             TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_at       TEXT NOT NULL,
    max_concurrency    INTEGER NOT NULL DEFAULT 1,
    created_by         TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_batches_workspace
    ON audit_batches(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_batch_items (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    report_file_id  TEXT NOT NULL,
    audit_job_id    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(batch_id, report_file_id),
    UNIQUE(audit_job_id)
);
CREATE INDEX IF NOT EXISTS idx_audit_batch_items_batch
    ON audit_batch_items(batch_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_audit_batch_items_workspace
    ON audit_batch_items(workspace_id, batch_id);
"""


AUDIT_BATCHES_POSTGRES_DDL = [
    """CREATE TABLE IF NOT EXISTS audit_batches (
         id TEXT PRIMARY KEY,
         workspace_id TEXT NOT NULL,
         assistant_id TEXT NOT NULL,
         naming_rule_file_id TEXT,
         mode TEXT NOT NULL DEFAULT 'night',
         status TEXT NOT NULL DEFAULT 'scheduled',
         scheduled_at TIMESTAMPTZ NOT NULL,
         max_concurrency INTEGER NOT NULL DEFAULT 1,
         created_by TEXT NOT NULL DEFAULT '',
         created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         started_at TIMESTAMPTZ,
         finished_at TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS ix_audit_batches_workspace ON audit_batches(workspace_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS audit_batch_items (
         id TEXT PRIMARY KEY,
         workspace_id TEXT NOT NULL,
         batch_id TEXT NOT NULL,
         ordinal INTEGER NOT NULL,
         report_file_id TEXT NOT NULL,
         audit_job_id TEXT NOT NULL,
         created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         UNIQUE (batch_id, report_file_id),
         UNIQUE (audit_job_id)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_audit_batch_items_batch ON audit_batch_items(batch_id, ordinal)",
    "CREATE INDEX IF NOT EXISTS ix_audit_batch_items_workspace ON audit_batch_items(workspace_id, batch_id)",
]


def _timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso()).replace("+00:00", "Z")
    return str(value)


def _row_to_batch(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("scheduled_at", "created_at", "updated_at", "started_at", "finished_at"):
        item[key] = _timestamp(item.get(key))
    item["max_concurrency"] = int(item.get("max_concurrency") or BATCH_MAX_CONCURRENCY)
    return item


def _row_to_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        item[key] = _timestamp(item.get(key))
    item["ordinal"] = int(item.get("ordinal") or 0)
    return item


class BatchRepository(Protocol):
    def insert_batch(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def insert_item(self, values: dict[str, Any]) -> dict[str, Any]: ...

    def get_batch(
        self, batch_id: str, *, workspace_id: str
    ) -> dict[str, Any] | None: ...

    def list_batches(
        self, *, workspace_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def list_items(
        self, batch_id: str, *, workspace_id: str
    ) -> list[dict[str, Any]]: ...

    def update_batch_cache(
        self,
        batch_id: str,
        *,
        workspace_id: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        updated_at: str,
    ) -> None: ...

    def lookup_batch_for_job(self, job_id: str) -> dict[str, str] | None: ...


class SqliteBatchRepository:
    def insert_batch(self, values: dict[str, Any]) -> dict[str, Any]:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO audit_batches
                   (id, workspace_id, assistant_id, naming_rule_file_id, mode, status,
                    scheduled_at, max_concurrency, created_by, created_at, updated_at,
                    started_at, finished_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    values["id"],
                    values["workspace_id"],
                    values["assistant_id"],
                    values.get("naming_rule_file_id"),
                    values.get("mode") or BATCH_MODE_NIGHT,
                    values.get("status") or BATCH_STATUS_SCHEDULED,
                    values["scheduled_at"],
                    int(values.get("max_concurrency") or BATCH_MAX_CONCURRENCY),
                    values.get("created_by") or "",
                    values["created_at"],
                    values["updated_at"],
                    values.get("started_at"),
                    values.get("finished_at"),
                ),
            )
        row = self.get_batch(str(values["id"]), workspace_id=str(values["workspace_id"]))
        if row is None:
            raise RuntimeError("audit batch insert did not persist")
        return row

    def insert_item(self, values: dict[str, Any]) -> dict[str, Any]:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO audit_batch_items
                   (id, workspace_id, batch_id, ordinal, report_file_id, audit_job_id,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    values["id"],
                    values["workspace_id"],
                    values["batch_id"],
                    int(values["ordinal"]),
                    values["report_file_id"],
                    values["audit_job_id"],
                    values["created_at"],
                    values["updated_at"],
                ),
            )
        rows = [
            item
            for item in self.list_items(
                str(values["batch_id"]), workspace_id=str(values["workspace_id"])
            )
            if item["id"] == values["id"]
        ]
        if not rows:
            raise RuntimeError("audit batch item insert did not persist")
        return rows[0]

    def get_batch(self, batch_id: str, *, workspace_id: str) -> dict[str, Any] | None:
        row = db.get_conn().execute(
            "SELECT * FROM audit_batches WHERE id=? AND workspace_id=?",
            (batch_id, workspace_id),
        ).fetchone()
        return _row_to_batch(row) if row else None

    def list_batches(self, *, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = db.get_conn().execute(
            """SELECT * FROM audit_batches
               WHERE workspace_id=?
               ORDER BY created_at DESC
               LIMIT ?""",
            (workspace_id, max(1, min(int(limit), 200))),
        ).fetchall()
        return [_row_to_batch(row) for row in rows]

    def list_items(self, batch_id: str, *, workspace_id: str) -> list[dict[str, Any]]:
        rows = db.get_conn().execute(
            """SELECT * FROM audit_batch_items
               WHERE batch_id=? AND workspace_id=?
               ORDER BY ordinal ASC, created_at ASC""",
            (batch_id, workspace_id),
        ).fetchall()
        return [_row_to_item(row) for row in rows]

    def update_batch_cache(
        self,
        batch_id: str,
        *,
        workspace_id: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        updated_at: str,
    ) -> None:
        with db.transaction() as conn:
            conn.execute(
                """UPDATE audit_batches
                   SET status=?, started_at=COALESCE(?, started_at),
                       finished_at=?, updated_at=?
                   WHERE id=? AND workspace_id=?""",
                (status, started_at, finished_at, updated_at, batch_id, workspace_id),
            )

    def lookup_batch_for_job(self, job_id: str) -> dict[str, str] | None:
        jid = str(job_id or "").strip()
        if not jid:
            return None
        row = db.get_conn().execute(
            "SELECT batch_id, workspace_id FROM audit_batch_items WHERE audit_job_id=?",
            (jid,),
        ).fetchone()
        if not row or not row["batch_id"]:
            return None
        return {
            "batch_id": str(row["batch_id"]),
            "workspace_id": str(row["workspace_id"] or ""),
        }


@dataclass(frozen=True)
class PostgresBatchRepository:
    dsn: str

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL audit batches require the optional psycopg dependency"
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def insert_batch(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO audit_batches
                   (id, workspace_id, assistant_id, naming_rule_file_id, mode, status,
                    scheduled_at, max_concurrency, created_by, created_at, updated_at,
                    started_at, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    values["id"],
                    values["workspace_id"],
                    values["assistant_id"],
                    values.get("naming_rule_file_id"),
                    values.get("mode") or BATCH_MODE_NIGHT,
                    values.get("status") or BATCH_STATUS_SCHEDULED,
                    values["scheduled_at"],
                    int(values.get("max_concurrency") or BATCH_MAX_CONCURRENCY),
                    values.get("created_by") or "",
                    values["created_at"],
                    values["updated_at"],
                    values.get("started_at"),
                    values.get("finished_at"),
                ),
            ).fetchone()
        return _row_to_batch(row)

    def insert_item(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """INSERT INTO audit_batch_items
                   (id, workspace_id, batch_id, ordinal, report_file_id, audit_job_id,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    values["id"],
                    values["workspace_id"],
                    values["batch_id"],
                    int(values["ordinal"]),
                    values["report_file_id"],
                    values["audit_job_id"],
                    values["created_at"],
                    values["updated_at"],
                ),
            ).fetchone()
        return _row_to_item(row)

    def get_batch(self, batch_id: str, *, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM audit_batches WHERE id=%s AND workspace_id=%s",
                (batch_id, workspace_id),
            ).fetchone()
        return _row_to_batch(row) if row else None

    def list_batches(self, *, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM audit_batches
                   WHERE workspace_id=%s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (workspace_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [_row_to_batch(row) for row in rows]

    def list_items(self, batch_id: str, *, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM audit_batch_items
                   WHERE batch_id=%s AND workspace_id=%s
                   ORDER BY ordinal ASC, created_at ASC""",
                (batch_id, workspace_id),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def update_batch_cache(
        self,
        batch_id: str,
        *,
        workspace_id: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        updated_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE audit_batches
                   SET status=%s, started_at=COALESCE(%s, started_at),
                       finished_at=%s, updated_at=%s
                   WHERE id=%s AND workspace_id=%s""",
                (status, started_at, finished_at, updated_at, batch_id, workspace_id),
            )

    def lookup_batch_for_job(self, job_id: str) -> dict[str, str] | None:
        jid = str(job_id or "").strip()
        if not jid:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT batch_id, workspace_id FROM audit_batch_items WHERE audit_job_id=%s",
                (jid,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        batch_id = str(data.get("batch_id") or "")
        if not batch_id:
            return None
        return {
            "batch_id": batch_id,
            "workspace_id": str(data.get("workspace_id") or ""),
        }


def get_batch_repository() -> BatchRepository:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for PostgreSQL audit batches")
        return PostgresBatchRepository(config.DATABASE_URL)
    return SqliteBatchRepository()
