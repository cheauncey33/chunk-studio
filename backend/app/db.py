"""SQLite schema, WAL setup, and connection helpers.

Uses a single connection per process with check_same_thread=False (acceptable
for a local single-user tool; FastAPI runs sync endpoints in a threadpool).
WAL mode avoids "database is locked" under uvicorn reload + background workers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from . import chunk_schema, config

# Seed field config: (field_key, display_name, extract_source, value_constraint,
#   label_list_json, value_type, llm_description, order_index)
#
# extract_source ∈ {manual, auto, llm}:
#   manual — user-editable, surfaced in the chunk editor
#   auto   — regex/heuristic, populated on create/OCR (see app.extractors)
#   llm    — small-model suggestion, written to metadata_llm and adopted by user
#
# Auto fields write into chunk.metadata_v2 only when empty, never overwriting edits.
SEED_FIELDS = [
    ("standard_no", "标准号", "auto", "free", "[]", "text",
     "文件名正则抽取，如 GB/T 6451-2023。", 1),
    ("content_type", "内容形态", "auto", "enum", '["table","text"]', "text",
     "含 <table> 标 table，否则 text。", 2),
    ("table_no", "表号", "auto", "free", "[]", "text",
     "正则 表\\s*(\\d+) 取首个，仅表格 chunk。", 3),
    ("table_header", "表头", "auto", "free", "[]", "text",
     "表格标题：正则抽取“表 N”后、表格主体前的标题文本。", 4),
    ("table_columns", "表列名", "auto", "free", "[]", "list",
     "<table> 首行单元格文本。", 5),
    ("table_ref", "表引用", "auto", "free", "[]", "list",
     "见?表\\d+ / 如表N所示，记录引用的表号。", 6),
    ("summary", "摘要", "llm", "free", "[]", "text",
     "一句话概括这段内容。", 7),
    ("keywords", "关键词", "llm", "free", "[]", "list",
     "提炼3-8个关键词。", 8),
    ("scope", "适用范围/对象", "llm", "free", "[]", "text",
     "片段描述的对象/适用范围。", 9),
    ("tags", "用户标签", "manual", "enum", "[]", "list",
     "用户自定义标签(可多选)。", 10),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,          -- rel to DATA_DIR, forward slashes
    sha         TEXT,
    page_count  INTEGER,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    file_id       TEXT NOT NULL,
    page          INTEGER NOT NULL,
    bbox           TEXT NOT NULL,        -- JSON {x,y,w,h} 0-1 normalized
    rotation       INTEGER DEFAULT 0,
    crop_path     TEXT,                  -- rel to DATA_DIR
    text          TEXT,
    text_source   TEXT CHECK (text_source IN ('digital','manual','ocr','pending')) DEFAULT 'pending',
    metadata       TEXT NOT NULL DEFAULT '{}',
    metadata_v2    TEXT NOT NULL DEFAULT '{}',
    metadata_llm   TEXT NOT NULL DEFAULT '{}',
    source_trace   TEXT NOT NULL DEFAULT '{}',
    chunk_logic    TEXT NOT NULL DEFAULT '{}',
    ui_state       TEXT NOT NULL DEFAULT '{}',
    indexing       TEXT NOT NULL DEFAULT '{}',
    status        TEXT DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_file_page ON chunks(file_id, page);

CREATE TABLE IF NOT EXISTS field_config (
    field_key        TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    extract_source   TEXT CHECK (extract_source IN ('manual','auto','llm')) NOT NULL,
    value_constraint TEXT CHECK (value_constraint IN ('free','enum')) NOT NULL,
    label_list       TEXT NOT NULL DEFAULT '[]',
    value_type       TEXT NOT NULL DEFAULT 'text',
    llm_description  TEXT NOT NULL DEFAULT '',
    order_index      INTEGER NOT NULL DEFAULT 0,
    storage_path     TEXT NOT NULL DEFAULT '',
    accepted_storage_path TEXT NOT NULL DEFAULT '',
    scope            TEXT NOT NULL DEFAULT 'chunk',
    editable         INTEGER NOT NULL DEFAULT 1,
    filterable       INTEGER NOT NULL DEFAULT 1,
    indexable        INTEGER NOT NULL DEFAULT 1,
    visible          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    status        TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 2,
    error         TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_type ON jobs(status, type, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target_type, target_id, type, created_at);

CREATE TABLE IF NOT EXISTS document_parses (
    id             TEXT PRIMARY KEY,
    file_id        TEXT NOT NULL,
    provider       TEXT NOT NULL DEFAULT 'mineru',
    status         TEXT NOT NULL DEFAULT 'queued',
    markdown_path  TEXT,
    raw_zip_path   TEXT,
    result         TEXT NOT NULL DEFAULT '{}',
    error          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_document_parses_file ON document_parses(file_id, created_at);
"""

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def init_db() -> None:
    config.ensure_dirs()
    global _conn
    _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL;")
    _conn.execute("PRAGMA foreign_keys=ON;")
    _conn.executescript(_SCHEMA)
    _conn.commit()
    _migrate_files_metadata()
    _migrate_chunk_layer_columns()
    _migrate_field_config()
    _backfill_chunk_layers()
    _backfill_auto_metadata()
    _conn.commit()


def _migrate_files_metadata() -> None:
    """Add file-level metadata to databases created before this column existed."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(files)").fetchall()
    }
    if "metadata" not in cols:
        _conn.execute("ALTER TABLE files ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")


def _migrate_chunk_layer_columns() -> None:
    """Add v2 JSON layer columns to existing chunk tables."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(chunks)").fetchall()
    }
    for name in ("metadata_v2", "source_trace", "chunk_logic", "ui_state", "indexing"):
        if name not in cols:
            _conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")


def _migrate_field_config() -> None:
    """Rebuild field_config if it predates the 'auto' extract_source.

    SQLite can't ALTER a CHECK constraint, so on an existing DB the old
    ('manual','llm') constraint blocks the new seed. Detect by inspecting the
    table SQL; if 'auto' isn't permitted, drop+recreate+reseed, then backfill
    auto metadata into existing chunks. Existing field_config rows (the old
    default 10) are replaced — intended, since the schema is being redesigned.
    """
    row = _conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='field_config'"
    ).fetchone()
    sql_text = row["sql"] if row else ""
    if "'auto'" in sql_text:
        _migrate_field_config_columns()
        _seed_fields_if_empty()  # fresh install: ensure defaults present
        _normalize_field_config_paths()
        return  # already new schema
    _conn.execute("DROP TABLE IF EXISTS field_config")
    _conn.execute(
        """CREATE TABLE field_config (
            field_key        TEXT PRIMARY KEY,
            display_name     TEXT NOT NULL,
            extract_source   TEXT CHECK (extract_source IN ('manual','auto','llm')) NOT NULL,
            value_constraint TEXT CHECK (value_constraint IN ('free','enum')) NOT NULL,
            label_list       TEXT NOT NULL DEFAULT '[]',
            value_type       TEXT NOT NULL DEFAULT 'text',
            llm_description  TEXT NOT NULL DEFAULT '',
            order_index      INTEGER NOT NULL DEFAULT 0,
            storage_path     TEXT NOT NULL DEFAULT '',
            accepted_storage_path TEXT NOT NULL DEFAULT '',
            scope            TEXT NOT NULL DEFAULT 'chunk',
            editable         INTEGER NOT NULL DEFAULT 1,
            filterable       INTEGER NOT NULL DEFAULT 1,
            indexable        INTEGER NOT NULL DEFAULT 1,
            visible          INTEGER NOT NULL DEFAULT 1
        )"""
    )
    _seed_fields_if_empty()
    _normalize_field_config_paths()
    _backfill_auto_metadata()


def _migrate_field_config_columns() -> None:
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(field_config)").fetchall()
    }
    additions = {
        "storage_path": "TEXT NOT NULL DEFAULT ''",
        "accepted_storage_path": "TEXT NOT NULL DEFAULT ''",
        "scope": "TEXT NOT NULL DEFAULT 'chunk'",
        "editable": "INTEGER NOT NULL DEFAULT 1",
        "filterable": "INTEGER NOT NULL DEFAULT 1",
        "indexable": "INTEGER NOT NULL DEFAULT 1",
        "visible": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, ddl in additions.items():
        if name not in cols:
            _conn.execute(f"ALTER TABLE field_config ADD COLUMN {name} {ddl}")


def _backfill_chunk_layers() -> None:
    """Populate v2 layer columns from legacy flat metadata if empty."""
    rows = _conn.execute(
        """SELECT id, metadata, metadata_v2, source_trace, chunk_logic
           FROM chunks"""
    ).fetchall()
    for r in rows:
        layers = chunk_schema.ensure_layered_chunk(
            metadata=r["metadata"],
            metadata_v2=r["metadata_v2"],
            source_trace=r["source_trace"],
            chunk_logic=r["chunk_logic"],
        )
        if (
            chunk_schema.parse_json_object(r["metadata_v2"]) != layers["metadata_v2"]
            or chunk_schema.parse_json_object(r["source_trace"]) != layers["source_trace"]
            or chunk_schema.parse_json_object(r["chunk_logic"]) != layers["chunk_logic"]
        ):
            _conn.execute(
                """UPDATE chunks
                   SET metadata_v2=?, source_trace=?, chunk_logic=?
                   WHERE id=?""",
                (
                    json.dumps(layers["metadata_v2"], ensure_ascii=False),
                    json.dumps(layers["source_trace"], ensure_ascii=False),
                    json.dumps(layers["chunk_logic"], ensure_ascii=False),
                    r["id"],
                ),
            )


def _backfill_auto_metadata() -> None:
    """Populate auto fields for chunks created before extractors existed.

    Idempotent: only fills empty slots, so safe to run repeatedly. Used both as
    a one-shot migration and conceptually the same merge that runs on create.
    """
    from . import extractors  # local import: extractors imports nothing from db
    rows = _conn.execute("SELECT id, file_id, text, metadata, metadata_v2 FROM chunks").fetchall()
    file_name_cache: dict[str, str] = {}
    for r in rows:
        fname = file_name_cache.get(r["file_id"])
        if fname is None:
            f = _conn.execute(
                "SELECT name FROM files WHERE id=?", (r["file_id"],)
            ).fetchone()
            fname = f["name"] if f else ""
            file_name_cache[r["file_id"]] = fname
        layers = chunk_schema.ensure_layered_chunk(
            metadata=r["metadata"],
            metadata_v2=r["metadata_v2"],
        )
        meta = layers["metadata_v2"]
        before = dict(meta)
        extractors.merge_auto_metadata(meta, r["text"], fname)
        if meta != before:
            _conn.execute(
                "UPDATE chunks SET metadata_v2=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), r["id"]),
            )


def _seed_fields_if_empty() -> None:
    _conn.executemany(
        """INSERT INTO field_config
           (field_key, display_name, extract_source, value_constraint,
            label_list, value_type, llm_description, order_index,
            storage_path, accepted_storage_path, scope, editable, filterable,
            indexable, visible)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(field_key) DO UPDATE SET
             display_name=excluded.display_name,
             extract_source=excluded.extract_source,
             value_constraint=excluded.value_constraint,
             label_list=excluded.label_list,
             value_type=excluded.value_type,
             llm_description=excluded.llm_description,
             order_index=excluded.order_index,
             storage_path=excluded.storage_path,
             accepted_storage_path=excluded.accepted_storage_path,
             scope=excluded.scope,
             editable=excluded.editable,
             filterable=excluded.filterable,
             indexable=excluded.indexable,
             visible=excluded.visible""",
        [_field_seed_row(row) for row in SEED_FIELDS],
    )


def _normalize_field_config_paths() -> None:
    rows = _conn.execute(
        "SELECT field_key, extract_source, storage_path, accepted_storage_path FROM field_config"
    ).fetchall()
    for row in rows:
        storage_path = row["storage_path"]
        accepted_storage_path = row["accepted_storage_path"]
        if not storage_path:
            storage_path = (
                f"metadata_llm.{row['field_key']}"
                if row["extract_source"] == "llm"
                else f"metadata_v2.{row['field_key']}"
            )
        if row["extract_source"] == "llm" and not accepted_storage_path:
            accepted_storage_path = f"metadata_v2.{row['field_key']}"
        if storage_path != row["storage_path"] or accepted_storage_path != row["accepted_storage_path"]:
            _conn.execute(
                """UPDATE field_config
                   SET storage_path=?, accepted_storage_path=?
                   WHERE field_key=?""",
                (storage_path, accepted_storage_path, row["field_key"]),
            )


def _field_seed_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    key, display, source, constraint, labels, value_type, description, order = row
    storage_path = f"metadata_llm.{key}" if source == "llm" else f"metadata_v2.{key}"
    accepted_storage_path = f"metadata_v2.{key}" if source == "llm" else ""
    filterable = 0 if source == "llm" else 1
    indexable = 0 if key in {"tags"} else 1
    return (
        key,
        display,
        source,
        constraint,
        labels,
        value_type,
        description,
        order,
        storage_path,
        accepted_storage_path,
        "chunk",
        1,
        filterable,
        indexable,
        1,
    )


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        init_db()
    assert _conn is not None
    return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Acquire the lock and yield the connection; commit/rollback on exit."""
    with _db_lock:
        conn = get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# --- Settings helpers (key/value table) ---
def get_setting(key: str, default: str = "") -> str:
    row = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    rows = get_conn().execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# --- Field config helpers ---
def get_field_configs() -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM field_config ORDER BY order_index, field_key"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["label_list"] = json.loads(d["label_list"] or "[]")
        for key in ("editable", "filterable", "indexable", "visible"):
            d[key] = bool(d.get(key, 1))
        out.append(d)
    return out
