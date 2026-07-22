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
# Auto fields write into chunk.business_metadata only when empty, never overwriting edits.
SEED_FIELDS = [
    ("standard_no", "标准号", "auto", "free", "[]", "text",
     "文件名正则抽取，如 GB/T 6451-2023。", 1),
    ("content_type", "内容形态", "auto", "enum", '["table","text"]', "text",
     "含 <table> 标 table，否则 text。", 2),
    ("table_kind", "表格类型", "auto", "enum",
     '["numbered_table","continued_table","symbol_table","formula_table","report_form","calculation_table","unnumbered_table"]',
     "text", "表格细分角色，用于质量审计和检索过滤。", 3),
    ("table_no", "表号", "auto", "free", "[]", "text",
     "正则 表\\s*(\\d+) 取首个，仅表格 chunk。", 4),
    ("table_title", "表标题", "auto", "free", "[]", "text",
     "表格标题：正则抽取“表 N”后、表格主体前的标题文本。", 5),
    ("table_columns", "表列名", "auto", "free", "[]", "list",
     "<table> 首行单元格文本。", 6),
    ("keywords", "关键词", "llm", "free", "[]", "list",
     "提炼3-8个可用于电力标准检索的关键词。", 7),
    ("questions", "相关问题", "llm", "free", "[]", "list",
     "生成2-4个可由当前片段直接回答的检索问题。", 8),
    ("tags", "用户标签", "manual", "enum", "[]", "list",
     "用户自定义标签(可多选)。", 9),
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
    business_metadata TEXT NOT NULL DEFAULT '{}',
    metadata_llm   TEXT NOT NULL DEFAULT '{}',
    source_trace   TEXT NOT NULL DEFAULT '{}',
    chunk_logic    TEXT NOT NULL DEFAULT '{}',
    relations      TEXT NOT NULL DEFAULT '{}',
    ui_state       TEXT NOT NULL DEFAULT '{}',
    indexing       TEXT NOT NULL DEFAULT '{}',
    status        TEXT CHECK (status IN ('pending','reviewed','approved','rejected')) DEFAULT 'pending',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_file_page ON chunks(file_id, page);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id     TEXT NOT NULL,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,
    text_sha256  TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    token_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model, dimension),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model
    ON chunk_embeddings(model, dimension);

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

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL UNIQUE,
    description              TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active','archived')),
    is_default               INTEGER NOT NULL DEFAULT 0,
    parser_config            TEXT NOT NULL DEFAULT '{}',
    retrieval_config         TEXT NOT NULL DEFAULT '{}',
    manual_rules             TEXT NOT NULL DEFAULT '{}',
    few_shot_rules           TEXT NOT NULL DEFAULT '{}',
    default_naming_file_id   TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_base_files (
    knowledge_base_id TEXT NOT NULL,
    file_id           TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'source'
                      CHECK (role IN ('source','reference')),
    corpus_kind       TEXT NOT NULL DEFAULT 'standard'
                      CHECK (corpus_kind IN ('standard','spec')),
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (knowledge_base_id, file_id),
    FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_base_files_file
    ON knowledge_base_files(file_id, knowledge_base_id);

CREATE TABLE IF NOT EXISTS audit_assistants (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('draft','active','archived')),
    active_version_id TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (active_version_id) REFERENCES assistant_versions(id)
);

CREATE TABLE IF NOT EXISTS assistant_versions (
    id                TEXT PRIMARY KEY,
    assistant_id      TEXT NOT NULL,
    version           INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','active','retired')),
    model_config      TEXT NOT NULL DEFAULT '{}',
    node_prompts      TEXT NOT NULL DEFAULT '{}',
    rules             TEXT NOT NULL DEFAULT '{}',
    retrieval_config  TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    activated_at      TEXT,
    UNIQUE (assistant_id, version),
    FOREIGN KEY (assistant_id) REFERENCES audit_assistants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assistant_knowledge_bases (
    assistant_id      TEXT NOT NULL,
    knowledge_base_id TEXT NOT NULL,
    priority          INTEGER NOT NULL DEFAULT 0,
    enabled           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (assistant_id, knowledge_base_id),
    FOREIGN KEY (assistant_id) REFERENCES audit_assistants(id) ON DELETE CASCADE,
    FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
);
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
    _migrate_chunk_status_values()
    _migrate_field_config()
    _migrate_knowledge_base_corpus_rules()
    _migrate_knowledge_base_corpus_kind()
    _backfill_chunk_layers()
    _backfill_auto_metadata()
    _seed_knowledge_base_and_assistant()
    _conn.commit()


def _seed_knowledge_base_and_assistant() -> None:
    """Create the non-destructive first migration boundary.

    Existing files are attached to the default knowledge base only when they
    have no knowledge-base relation yet. Prompt files remain versioned source
    assets, while their content is snapshotted into assistant version 1 so
    future edits create a new, auditable version.
    """
    import time

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    kb_id = "kb_uncategorized"
    assistant_id = "assistant_oil_transformer_audit"
    version_id = "assistant_oil_transformer_audit_v1"
    rules_path = config.PROJECT_ROOT / "evaluation" / "manual_knowledge_rules_v1.json"
    seed_manual_rules = (
        json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
    )
    _conn.execute(
        """INSERT OR IGNORE INTO knowledge_bases
           (id, name, description, status, is_default, parser_config,
            retrieval_config, manual_rules, few_shot_rules,
            default_naming_file_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            kb_id,
            "未分类知识库",
            "现有文件的默认归属",
            "active",
            1,
            "{}",
            json.dumps(
                {
                    "top_k": 10,
                    "similarity_threshold": 0.2,
                    "keyword_weight": 0.3,
                    "vector_weight": 0.7,
                    "content_type": "all",
                },
                ensure_ascii=False,
            ),
            json.dumps(seed_manual_rules, ensure_ascii=False),
            "{}",
            None,
            now,
            now,
        ),
    )
    # Ensure default parser_config has auto-chunk defaults when still empty.
    from . import chunk_pipeline

    kb_row = _conn.execute(
        "SELECT parser_config FROM knowledge_bases WHERE id=?",
        (kb_id,),
    ).fetchone()
    try:
        existing_parser = json.loads((kb_row["parser_config"] if kb_row else None) or "{}")
    except (json.JSONDecodeError, TypeError):
        existing_parser = {}
    if not existing_parser:
        _conn.execute(
            "UPDATE knowledge_bases SET parser_config=?, updated_at=? WHERE id=?",
            (
                json.dumps(chunk_pipeline.DEFAULT_PARSER_CONFIG, ensure_ascii=False),
                now,
                kb_id,
            ),
        )
    # Backfill corpus rules onto the default KB when empty (do not clobber edits).
    row = _conn.execute(
        "SELECT manual_rules FROM knowledge_bases WHERE id=?",
        (kb_id,),
    ).fetchone()
    existing_rules = {}
    try:
        existing_rules = json.loads((row["manual_rules"] if row else None) or "{}")
    except (json.JSONDecodeError, TypeError):
        existing_rules = {}
    if seed_manual_rules and not (existing_rules.get("rules") or []):
        _conn.execute(
            """UPDATE knowledge_bases
               SET manual_rules=?, updated_at=?
               WHERE id=?""",
            (json.dumps(seed_manual_rules, ensure_ascii=False), now, kb_id),
        )
    _conn.execute(
        """INSERT OR IGNORE INTO knowledge_base_files
           (knowledge_base_id, file_id, role, corpus_kind, enabled, created_at)
           SELECT ?, f.id, 'source', 'standard', 1, ?
           FROM files f
           WHERE NOT EXISTS (
             SELECT 1 FROM knowledge_base_files kbf WHERE kbf.file_id=f.id
           )
           AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_role'), ''))
               NOT IN ('report', 'naming')
           AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_type'), ''))
               NOT IN ('report', 'naming')""",
        (kb_id, now),
    )
    # Detach reports / naming files that were wrongly attached as corpus.
    _conn.execute(
        """DELETE FROM knowledge_base_files
           WHERE file_id IN (
             SELECT f.id FROM files f
             WHERE LOWER(COALESCE(json_extract(f.metadata, '$.doc_role'), ''))
                   IN ('report', 'naming')
                OR LOWER(COALESCE(json_extract(f.metadata, '$.doc_type'), ''))
                   IN ('report', 'naming')
           )"""
    )
    # Naming attribute files must not remain in corpus membership.
    _conn.execute(
        """DELETE FROM knowledge_base_files
           WHERE file_id IN (
             SELECT default_naming_file_id FROM knowledge_bases
             WHERE default_naming_file_id IS NOT NULL
               AND TRIM(default_naming_file_id) != ''
           )"""
    )
    _conn.execute(
        """INSERT OR IGNORE INTO audit_assistants
           (id, name, description, status, active_version_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            assistant_id,
            "油浸式变压器审查",
            "检测报告参数、项目、型号规则、证据检索与标准值审查",
            "active",
            None,
            now,
            now,
        ),
    )
    prompt_paths = {
        "report_parameters": "report_parameter_extraction_v1.md",
        "test_items": "report_test_item_extraction_v1.md",
        "model_decode": "model_naming_decode_v1.md",
        "query_planner": "retrieval_query_planner_v1.md",
        "audit_judge": "standard_value_audit_judge_v1.md",
    }
    prompt_dir = config.PROJECT_ROOT / "evaluation" / "prompts"
    node_prompts = {}
    for key, filename in prompt_paths.items():
        path = prompt_dir / filename
        node_prompts[key] = {
            "path": str(path.relative_to(config.PROJECT_ROOT)).replace("\\", "/"),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
        }
    rules = seed_manual_rules
    _conn.execute(
        """INSERT OR IGNORE INTO assistant_versions
           (id, assistant_id, version, status, model_config, node_prompts,
            rules, retrieval_config, created_at, activated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id,
            assistant_id,
            1,
            "active",
            json.dumps(
                {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "temperature": 0,
                    "response_format": "json_object",
                    "thinking": "disabled",
                },
                ensure_ascii=False,
            ),
            json.dumps(node_prompts, ensure_ascii=False),
            json.dumps(rules, ensure_ascii=False),
            json.dumps(
                {
                    "top_k": 10,
                    "route_top_k": 30,
                    "candidate_count_per_type": 20,
                    "final_per_type": 15,
                    "special_route_reserve": 3,
                    "similarity_threshold": 0.2,
                    "keyword_weight": 0.3,
                    "vector_weight": 0.7,
                },
                ensure_ascii=False,
            ),
            now,
            now,
        ),
    )
    _conn.execute(
        "UPDATE audit_assistants SET active_version_id=? WHERE id=? AND active_version_id IS NULL",
        (version_id, assistant_id),
    )
    _conn.execute(
        """INSERT OR IGNORE INTO assistant_knowledge_bases
           (assistant_id, knowledge_base_id, priority, enabled)
           VALUES (?,?,0,1)""",
        (assistant_id, kb_id),
    )


def _migrate_files_metadata() -> None:
    """Add file-level metadata to databases created before this column existed."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(files)").fetchall()
    }
    if "metadata" not in cols:
        _conn.execute("ALTER TABLE files ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")


def _migrate_knowledge_base_corpus_rules() -> None:
    """Add KB-scoped naming / manual / few-shot rule columns."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()
    }
    if "manual_rules" not in cols:
        _conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN manual_rules TEXT NOT NULL DEFAULT '{}'"
        )
    if "few_shot_rules" not in cols:
        _conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN few_shot_rules TEXT NOT NULL DEFAULT '{}'"
        )
    if "default_naming_file_id" not in cols:
        _conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN default_naming_file_id TEXT"
        )


def _migrate_knowledge_base_corpus_kind() -> None:
    """Add corpus_kind (standard/spec) for KB file membership rows."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(knowledge_base_files)").fetchall()
    }
    if "corpus_kind" not in cols:
        _conn.execute(
            """ALTER TABLE knowledge_base_files
               ADD COLUMN corpus_kind TEXT NOT NULL DEFAULT 'standard'"""
        )
        _conn.execute(
            """UPDATE knowledge_base_files
               SET corpus_kind='spec'
               WHERE role='reference'"""
        )
        _conn.execute(
            """UPDATE knowledge_base_files
               SET corpus_kind='standard'
               WHERE corpus_kind IS NULL
                  OR TRIM(corpus_kind)=''
                  OR corpus_kind NOT IN ('standard','spec')"""
        )

def _migrate_chunk_layer_columns() -> None:
    """Add JSON layer columns to existing chunk tables."""
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(chunks)").fetchall()
    }
    for name in ("business_metadata", "source_trace", "chunk_logic", "relations", "ui_state", "indexing"):
        if name not in cols:
            _conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")


def _migrate_chunk_status_values() -> None:
    """Normalize pre-workflow values before API transition checks apply."""
    _conn.execute(
        """UPDATE chunks SET status='pending'
           WHERE status IS NULL OR status NOT IN ('pending','reviewed','approved','rejected')"""
    )
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status)")


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
        _retire_deprecated_field_configs()
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
    _retire_deprecated_field_configs()
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
    """Populate layer columns without losing values from intermediate schemas.

    Upgrade precedence is ``business_metadata > metadata_v2 > metadata``:
    current writes win, the previous business layer supplies missing values,
    and the oldest flat object is only a final compatibility fallback.
    """
    cols = {
        row["name"]
        for row in _conn.execute("PRAGMA table_info(chunks)").fetchall()
    }
    metadata_v2_select = "metadata_v2" if "metadata_v2" in cols else "'{}' AS metadata_v2"
    rows = _conn.execute(
        f"""SELECT id, metadata, {metadata_v2_select}, business_metadata,
                   source_trace, chunk_logic, relations
            FROM chunks"""
    ).fetchall()
    for r in rows:
        business_metadata = chunk_schema.parse_json_object(r["business_metadata"])
        chunk_schema.merge_missing(
            business_metadata,
            chunk_schema.parse_json_object(r["metadata_v2"]),
        )
        layers = chunk_schema.ensure_layered_chunk(
            metadata=r["metadata"],
            business_metadata=business_metadata,
            source_trace=r["source_trace"],
            chunk_logic=r["chunk_logic"],
            relations=r["relations"],
        )
        if (
            chunk_schema.parse_json_object(r["business_metadata"]) != layers["business_metadata"]
            or chunk_schema.parse_json_object(r["source_trace"]) != layers["source_trace"]
            or chunk_schema.parse_json_object(r["chunk_logic"]) != layers["chunk_logic"]
            or chunk_schema.parse_json_object(r["relations"]) != layers["relations"]
        ):
            _conn.execute(
                """UPDATE chunks
                   SET business_metadata=?, source_trace=?, chunk_logic=?, relations=?
                   WHERE id=?""",
                (
                    json.dumps(layers["business_metadata"], ensure_ascii=False),
                    json.dumps(layers["source_trace"], ensure_ascii=False),
                    json.dumps(layers["chunk_logic"], ensure_ascii=False),
                    json.dumps(layers["relations"], ensure_ascii=False),
                    r["id"],
                ),
            )


def _backfill_auto_metadata() -> None:
    """Populate auto fields for chunks created before extractors existed.

    Idempotent: only fills empty slots, so safe to run repeatedly. Used both as
    a one-shot migration and conceptually the same merge that runs on create.
    """
    from . import extractors  # local import: extractors imports nothing from db
    rows = _conn.execute("SELECT id, file_id, text, metadata, business_metadata FROM chunks").fetchall()
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
            business_metadata=r["business_metadata"],
        )
        meta = layers["business_metadata"]
        before = dict(meta)
        extractors.merge_auto_metadata(meta, r["text"], fname)
        if meta != before:
            _conn.execute(
                "UPDATE chunks SET business_metadata=? WHERE id=?",
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
                else f"business_metadata.{row['field_key']}"
            )
        if row["extract_source"] == "llm" and not accepted_storage_path:
            accepted_storage_path = f"business_metadata.{row['field_key']}"
        if storage_path != row["storage_path"] or accepted_storage_path != row["accepted_storage_path"]:
            _conn.execute(
                """UPDATE field_config
                   SET storage_path=?, accepted_storage_path=?
                   WHERE field_key=?""",
                (storage_path, accepted_storage_path, row["field_key"]),
            )


def _retire_deprecated_field_configs() -> None:
    _conn.execute(
        """DELETE FROM field_config
           WHERE field_key IN (
             'table_header', 'table_ref', 'figure_header', 'figure_ref',
             'summary', 'scope'
           )"""
    )


def _field_seed_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    key, display, source, constraint, labels, value_type, description, order = row
    storage_path = f"metadata_llm.{key}" if source == "llm" else f"business_metadata.{key}"
    accepted_storage_path = f"business_metadata.{key}" if source == "llm" else ""
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


def assistant_scoped_file_ids(assistant_id: str) -> list[str]:
    rows = get_conn().execute(
        """SELECT DISTINCT kbf.file_id
           FROM assistant_knowledge_bases akb
           JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
           JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=akb.knowledge_base_id
           WHERE akb.assistant_id=? AND akb.enabled=1
             AND kb.status='active' AND kbf.enabled=1
           ORDER BY kbf.file_id""",
        (assistant_id,),
    ).fetchall()
    return [row["file_id"] for row in rows]


def assistant_bound_knowledge_bases(assistant_id: str) -> list[dict[str, Any]]:
    """Enabled bound KBs ordered by priority ascending (higher priority last / wins)."""
    rows = get_conn().execute(
        """SELECT kb.*, akb.priority AS bind_priority
           FROM assistant_knowledge_bases akb
           JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
           WHERE akb.assistant_id=? AND akb.enabled=1 AND kb.status='active'
           ORDER BY akb.priority ASC, kb.name ASC""",
        (assistant_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _loads_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def merge_manual_rules(
    kb_rows: list[dict[str, Any]],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge KB manual rules; later (higher-priority) KBs overwrite same rule_id."""
    merged: dict[str, dict[str, Any]] = {}
    meta: dict[str, Any] = {
        "version": 1,
        "scope": "knowledge_base_manual_rules",
        "status": "merged_from_knowledge_bases",
    }
    for row in kb_rows:
        payload = _loads_json(row.get("manual_rules"), {})
        if not isinstance(payload, dict):
            continue
        for key in ("version", "status"):
            if payload.get(key) is not None:
                meta[key] = payload[key]
        if payload.get("scope"):
            meta["scope"] = payload["scope"]
        for rule in payload.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("rule_id") or "").strip()
            if rule_id:
                merged[rule_id] = rule
    if merged:
        return {**meta, "scope": "knowledge_base_manual_rules", "rules": list(merged.values())}
    fallback_payload = fallback if isinstance(fallback, dict) else {}
    if fallback_payload.get("rules"):
        return fallback_payload
    return {**meta, "rules": []}


def merge_few_shot_rules(kb_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge KB few-shot packs; later KBs overwrite same item id."""
    merged: dict[str, dict[str, Any]] = {}
    version = 1
    for row in kb_rows:
        payload = _loads_json(row.get("few_shot_rules"), {})
        if not isinstance(payload, dict):
            continue
        if payload.get("version") is not None:
            version = payload["version"]
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if item_id:
                merged[item_id] = item
    return {"version": version, "items": list(merged.values())}


def resolve_assistant_manual_rules(
    assistant_id: str,
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return merge_manual_rules(
        assistant_bound_knowledge_bases(assistant_id),
        fallback=fallback,
    )


def resolve_assistant_few_shot_rules(assistant_id: str) -> dict[str, Any]:
    return merge_few_shot_rules(assistant_bound_knowledge_bases(assistant_id))


def assistant_default_naming_file_id(assistant_id: str) -> str | None:
    """Pick naming file from the highest-priority bound KB that has one set."""
    rows = get_conn().execute(
        """SELECT kb.default_naming_file_id
           FROM assistant_knowledge_bases akb
           JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
           WHERE akb.assistant_id=? AND akb.enabled=1 AND kb.status='active'
             AND kb.default_naming_file_id IS NOT NULL
             AND TRIM(kb.default_naming_file_id) != ''
           ORDER BY akb.priority DESC, kb.name ASC
           LIMIT 1""",
        (assistant_id,),
    ).fetchall()
    if not rows:
        return None
    return str(rows[0]["default_naming_file_id"])


def assistant_evidence_file_ids(
    assistant_id: str,
    *,
    excluded_file_ids: set[str] | None = None,
) -> list[str]:
    """Bound-KB files that may be used as retrieval evidence (after exclusions)."""
    excluded = excluded_file_ids or set()
    file_ids = [
        file_id
        for file_id in assistant_scoped_file_ids(assistant_id)
        if file_id not in excluded
    ]
    if not file_ids:
        raise ValueError(
            "assistant has no evidence files after excluding runtime inputs"
        )
    return file_ids


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
