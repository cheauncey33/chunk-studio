"""Jieba-pretokenized SQLite FTS5 index and retrieval shadow logging."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from typing import Any

import jieba

from . import chunk_schema, config, db


TOKENIZER_VERSION = "jieba_domain_v1"
PRODUCTION_ENABLED_SETTING = "retrieval.lexical_production_enabled"
SHADOW_ENABLED_SETTING = "retrieval.lexical_shadow_enabled"
SHADOW_MAX_RUNS = 200
SHADOW_ROUTE_TOP_K = 20
SHADOW_FINAL_PER_TYPE = 20
RRF_K = 60
CONTENT_TYPES = ("table", "section")
INDEX_FIELDS = (
    "standard_no",
    "section",
    "section_title",
    "table_no",
    "table_title",
    "table_columns",
    "keywords",
    "content",
)
FIELD_WEIGHTS = {
    "standard_no": 12.0,
    "section": 10.0,
    "section_title": 7.0,
    "table_no": 10.0,
    "table_title": 8.0,
    "table_columns": 7.0,
    "keywords": 6.0,
    "content": 1.0,
}

_TOKEN_RE = re.compile(
    r"[a-zA-Z][a-zA-Z0-9]*(?:[.\-/][a-zA-Z0-9]+)+|"
    r"\d+(?:\.\d+)?\s*(?:kva|kv|kw|hz|db|mω|mΩ|μs|ms|w|v|a|s|%)|"
    r"\d+(?:\.\d+)*",
    re.IGNORECASE,
)
_STOP_TOKENS = {
    "nbsp", "table", "tr", "td", "th", "tbody", "thead", "colspan", "rowspan",
    "以及", "或者", "进行", "应按", "符合", "规定", "要求", "试验", "标准",
}
_schema_lock = threading.Lock()
_tokenizer_lock = threading.Lock()
_schema_ready = False
_schema_db_path = ""
_tokenizer_ready = False


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainTextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _ensure_tokenizer() -> None:
    global _tokenizer_ready
    if _tokenizer_ready:
        return
    with _tokenizer_lock:
        if _tokenizer_ready:
            return
        jieba.setLogLevel(30)
        terms_path = config.PROJECT_ROOT / "evaluation" / "domain_terms_jieba.txt"
        if terms_path.is_file():
            jieba.load_userdict(str(terms_path))
        _tokenizer_ready = True


def normalize_text(value: Any) -> str:
    return (
        _plain_text(str(value or ""))
        .replace("（", "(")
        .replace("）", ")")
        .replace("％", "%")
        .lower()
    )


def normalize_token(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _exact_token(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", normalize_token(value))


def tokens_for_search(value: Any) -> list[str]:
    _ensure_tokenizer()
    normalized = normalize_text(value)
    tokens = [normalize_token(token) for token in jieba.lcut_for_search(normalized)]
    tokens.extend(_exact_token(match.group(0)) for match in _TOKEN_RE.finditer(normalized))
    return [
        token
        for token in dict.fromkeys(tokens)
        if token
        and token not in _STOP_TOKENS
        and not re.fullmatch(r"[\W_]+", token)
        and (len(token) >= 2 or token.isdigit())
    ]


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    return str(value)


def _raw_fields(row: Any) -> dict[str, str]:
    metadata = chunk_schema.parse_json_object(row["business_metadata"])
    return {
        "standard_no": _flatten(metadata.get("standard_no")),
        "section": _flatten(metadata.get("section")),
        "section_title": _flatten(metadata.get("section_title")),
        "table_no": _flatten(metadata.get("table_no")),
        "table_title": _flatten(metadata.get("table_title")),
        "table_columns": _flatten(metadata.get("table_columns")),
        "keywords": _flatten(metadata.get("keywords")),
        "content": str(row["text"] or ""),
    }


def _indexed_fields(row: Any) -> dict[str, str]:
    return {
        field: " ".join(tokens_for_search(value))
        for field, value in _raw_fields(row).items()
    }


def ensure_schema() -> None:
    global _schema_ready, _schema_db_path
    current_db_path = str(config.DB_PATH)
    if _schema_ready and _schema_db_path == current_db_path:
        return
    with _schema_lock:
        if _schema_ready and _schema_db_path == current_db_path:
            return
        conn = db.get_conn()
        if not conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]:
            raise RuntimeError("SQLite was built without FTS5 support")
        with db.transaction() as tx:
            tx.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                       chunk_id UNINDEXED,
                       content_type UNINDEXED,
                       standard_no,
                       section,
                       section_title,
                       table_no,
                       table_title,
                       table_columns,
                       keywords,
                       content,
                       tokenize='unicode61'
                   )"""
            )
            tx.execute(
                """CREATE TABLE IF NOT EXISTS lexical_index_state (
                       chunk_id TEXT PRIMARY KEY,
                       chunk_updated_at TEXT NOT NULL,
                       tokenizer_version TEXT NOT NULL,
                       indexed_at TEXT NOT NULL
                   )"""
            )
            tx.execute(
                """CREATE TABLE IF NOT EXISTS retrieval_shadow_runs (
                       id TEXT PRIMARY KEY,
                       query TEXT NOT NULL,
                       status TEXT NOT NULL,
                       duration_ms REAL NOT NULL DEFAULT 0,
                       payload TEXT NOT NULL DEFAULT '{}',
                       error TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL
                   )"""
            )
            tx.execute(
                """CREATE INDEX IF NOT EXISTS idx_retrieval_shadow_runs_created
                   ON retrieval_shadow_runs(created_at DESC)"""
            )
        _schema_ready = True
        _schema_db_path = current_db_path


def sync_index(*, force: bool = False, limit: int | None = None) -> dict[str, int | str]:
    ensure_schema()
    conn = db.get_conn()
    stale_rows = conn.execute(
        """SELECT s.chunk_id
           FROM lexical_index_state s
           LEFT JOIN chunks c ON c.id=s.chunk_id
           WHERE c.id IS NULL OR c.status!='approved'"""
    ).fetchall()
    params: list[Any] = [TOKENIZER_VERSION]
    pending_sql = """SELECT c.id, c.text, c.business_metadata, c.updated_at
                     FROM chunks c
                     LEFT JOIN lexical_index_state s ON s.chunk_id=c.id
                     WHERE c.status='approved'
                       AND (? OR s.chunk_id IS NULL OR s.chunk_updated_at!=c.updated_at
                            OR s.tokenizer_version!=?)
                     ORDER BY c.file_id, c.page, c.created_at"""
    params = [int(force), TOKENIZER_VERSION]
    if limit is not None:
        pending_sql += " LIMIT ?"
        params.append(limit)
    pending_rows = conn.execute(pending_sql, params).fetchall()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as tx:
        for row in stale_rows:
            tx.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (row["chunk_id"],))
            tx.execute("DELETE FROM lexical_index_state WHERE chunk_id=?", (row["chunk_id"],))
        for row in pending_rows:
            metadata = chunk_schema.parse_json_object(row["business_metadata"])
            content_type = str(metadata.get("content_type") or "text")
            fields = _indexed_fields(row)
            tx.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (row["id"],))
            tx.execute(
                """INSERT INTO chunk_fts(
                       chunk_id, content_type, standard_no, section, section_title,
                       table_no, table_title, table_columns, keywords, content
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], content_type,
                    *(fields[field] for field in INDEX_FIELDS),
                ),
            )
            tx.execute(
                """INSERT INTO lexical_index_state(
                       chunk_id, chunk_updated_at, tokenizer_version, indexed_at
                   ) VALUES (?,?,?,?)
                   ON CONFLICT(chunk_id) DO UPDATE SET
                     chunk_updated_at=excluded.chunk_updated_at,
                     tokenizer_version=excluded.tokenizer_version,
                     indexed_at=excluded.indexed_at""",
                (row["id"], row["updated_at"], TOKENIZER_VERSION, now),
            )
    return {
        "tokenizer_version": TOKENIZER_VERSION,
        "indexed": len(pending_rows),
        "removed": len(stale_rows),
    }


def _pending_count() -> int:
    return int(db.get_conn().execute(
        """SELECT COUNT(*)
           FROM chunks c
           LEFT JOIN lexical_index_state s ON s.chunk_id=c.id
           WHERE c.status='approved'
             AND (s.chunk_id IS NULL OR s.chunk_updated_at!=c.updated_at
                  OR s.tokenizer_version!=?)""",
        (TOKENIZER_VERSION,),
    ).fetchone()[0])


def index_status() -> dict[str, Any]:
    ensure_schema()
    conn = db.get_conn()
    production = production_enabled()
    shadow = shadow_enabled()
    return {
        "enabled": production or shadow,
        "production_enabled": production,
        "shadow_enabled": shadow,
        "fts5_available": bool(
            conn.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]
        ),
        "tokenizer_version": TOKENIZER_VERSION,
        "approved_chunks": int(
            conn.execute("SELECT COUNT(*) FROM chunks WHERE status='approved'").fetchone()[0]
        ),
        "indexed_chunks": int(conn.execute("SELECT COUNT(*) FROM lexical_index_state").fetchone()[0]),
        "fts_rows": int(conn.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0]),
        "pending_chunks": _pending_count(),
    }


def _match_expression(tokens: list[str]) -> str:
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _search_postgres(
    query: str,
    *,
    content_type: str,
    top_k: int,
    file_ids: list[str] | None,
) -> dict[str, Any]:
    from .storage.repositories import get_content_repository, get_content_write_repository

    repository = get_content_repository() or get_content_write_repository()
    if repository is None:
        raise RuntimeError("PostgreSQL lexical retrieval requires a content repository")
    tokens = tokens_for_search(query)
    if not tokens or file_ids is not None and not file_ids:
        return {
            "query": query,
            "query_tokens": tokens,
            "content_type": content_type,
            "hits": [],
            "sync": {"backend": "postgres"},
        }
    token_set = set(tokens)
    scored: list[tuple[float, str, dict[str, Any], dict[str, list[str]]]] = []
    for row in repository.list_lexical_rows(
        content_type=content_type,
        file_ids=file_ids,
        limit=max(1000, top_k * 20),
    ):
        raw_fields = _raw_fields(row)
        matched_fields = {
            field: sorted(token_set & set(tokens_for_search(value)))
            for field, value in raw_fields.items()
            if value
        }
        matched_fields = {field: values for field, values in matched_fields.items() if values}
        if not matched_fields:
            continue
        score = sum(
            FIELD_WEIGHTS[field] * len(values)
            for field, values in matched_fields.items()
        )
        scored.append((score, str(row["id"]), row, matched_fields))
    scored.sort(key=lambda item: (-item[0], item[1]))
    hits = []
    for score, _, row, matched_fields in scored[:top_k]:
        metadata = chunk_schema.parse_json_object(row["business_metadata"])
        crop_object_key = str(row.get("crop_object_key") or "")
        crop_path = row.get("crop_path")
        hits.append({
            "chunk_id": row["id"],
            "score": float(score),
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "page": row["page"],
            "crop_url": (
                f"/api/chunks/{row['id']}/crop"
                if crop_object_key
                else (f"/crops/{str(crop_path).split('/')[-1]}" if crop_path else None)
            ),
            "text": row["text"] or "",
            "business_metadata": metadata,
            "source_trace": chunk_schema.parse_json_object(row["source_trace"]),
            "matched_fields": matched_fields,
        })
    return {
        "query": query,
        "query_tokens": tokens,
        "content_type": content_type,
        "hits": hits,
        "sync": {"backend": "postgres"},
    }


def search(
    query: str,
    *,
    content_type: str,
    top_k: int = SHADOW_ROUTE_TOP_K,
    sync: bool = True,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
    if content_type not in CONTENT_TYPES:
        raise ValueError(f"unsupported lexical content type: {content_type}")
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        return _search_postgres(
            query,
            content_type=content_type,
            top_k=top_k,
            file_ids=file_ids,
        )
    if sync:
        sync_result = sync_index()
    else:
        ensure_schema()
        sync_result = None
    tokens = tokens_for_search(query)
    if not tokens:
        return {"query": query, "query_tokens": [], "hits": [], "sync": sync_result}
    weights = [0.0, 0.0, *(FIELD_WEIGHTS[field] for field in INDEX_FIELDS)]
    placeholders = ",".join("?" for _ in weights)
    if file_ids is not None and not file_ids:
        return {
            "query": query,
            "query_tokens": tokens,
            "content_type": content_type,
            "hits": [],
            "sync": sync_result,
        }
    file_clause = ""
    file_params: list[str] = []
    if file_ids is not None:
        file_clause = f" AND c.file_id IN ({','.join('?' for _ in file_ids)})"
        file_params = file_ids
    sql = f"""SELECT c.id, c.file_id, f.name AS file_name, c.page, c.crop_path,
                     c.text, c.business_metadata, c.source_trace,
                     bm25(chunk_fts, {placeholders}) AS rank_score
              FROM chunk_fts
              JOIN chunks c ON c.id=chunk_fts.chunk_id
              JOIN files f ON f.id=c.file_id
              WHERE chunk_fts MATCH ? AND chunk_fts.content_type=?
                AND c.status='approved'{file_clause}
              ORDER BY rank_score, c.id
              LIMIT ?"""
    rows = db.get_conn().execute(
        sql,
        (*weights, _match_expression(tokens), content_type, *file_params, top_k),
    ).fetchall()
    hits = []
    for row in rows:
        metadata = chunk_schema.parse_json_object(row["business_metadata"])
        raw_fields = _raw_fields(row)
        token_set = set(tokens)
        matched_fields = {
            field: sorted(token_set & set(tokens_for_search(value)))
            for field, value in raw_fields.items()
            if value
        }
        matched_fields = {field: values for field, values in matched_fields.items() if values}
        hits.append({
            "chunk_id": row["id"],
            "score": -float(row["rank_score"]),
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "page": row["page"],
            "crop_url": f"/crops/{row['crop_path'].split('/')[-1]}" if row["crop_path"] else None,
            "text": row["text"] or "",
            "business_metadata": metadata,
            "source_trace": chunk_schema.parse_json_object(row["source_trace"]),
            "matched_fields": matched_fields,
        })
    return {
        "query": query,
        "query_tokens": tokens,
        "content_type": content_type,
        "hits": hits,
        "sync": sync_result,
    }


def shadow_enabled() -> bool:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        return False
    return db.get_setting(SHADOW_ENABLED_SETTING, "true").strip().lower() == "true"


def production_enabled() -> bool:
    if config.DATABASE_BACKEND in {"postgres", "postgresql"}:
        return True
    return db.get_setting(PRODUCTION_ENABLED_SETTING, "true").strip().lower() == "true"


def run_shadow(
    query: str,
    query_routes: dict[str, str],
    production_hit_ids: list[str],
) -> str | None:
    if not shadow_enabled():
        return None
    ensure_schema()
    run_id = str(uuid.uuid4())
    created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    started = time.perf_counter()
    try:
        sync_result = sync_index()
        routes = {"production": query}
        keyword = str(query_routes.get("keyword") or "").strip()
        if keyword and keyword != query:
            routes["keyword"] = keyword
        query_tokens = {route: tokens_for_search(value) for route, value in routes.items()}
        merged_hits = []
        for content_type in CONTENT_TYPES:
            merged: dict[str, dict[str, Any]] = {}
            for route, route_query in routes.items():
                result = search(
                    route_query,
                    content_type=content_type,
                    top_k=SHADOW_ROUTE_TOP_K,
                    sync=False,
                )
                for rank, hit in enumerate(result["hits"], start=1):
                    item = merged.setdefault(
                        hit["chunk_id"],
                        {
                            **hit,
                            "content_type": content_type,
                            "route_ranks": {},
                            "rrf_score": 0.0,
                        },
                    )
                    item["route_ranks"][route] = rank
                    item["rrf_score"] += 1 / (RRF_K + rank)
            ranked = sorted(
                merged.values(),
                key=lambda item: (-item["rrf_score"], -item["score"], item["chunk_id"]),
            )[:SHADOW_FINAL_PER_TYPE]
            merged_hits.extend(ranked)
        merged_hits.sort(key=lambda item: (-item["rrf_score"], item["chunk_id"]))
        lexical_ids = [hit["chunk_id"] for hit in merged_hits]
        production_ids = list(dict.fromkeys(production_hit_ids))
        overlap_ids = sorted(set(lexical_ids) & set(production_ids))
        payload = {
            "query_routes": routes,
            "query_tokens": query_tokens,
            "index_sync": sync_result,
            "production_hit_ids": production_ids,
            "lexical_hit_count": len(merged_hits),
            "overlap_count": len(overlap_ids),
            "overlap_ids": overlap_ids,
            "lexical_hits": [_shadow_hit(hit) for hit in merged_hits],
        }
        duration_ms = (time.perf_counter() - started) * 1000
        _store_shadow_run(
            run_id, query, "complete", duration_ms, payload, "", created_at
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        _store_shadow_run(
            run_id,
            query,
            "failed",
            duration_ms,
            {"query_routes": query_routes, "production_hit_ids": production_hit_ids},
            f"{type(exc).__name__}: {exc}",
            created_at,
        )
    return run_id


def _shadow_hit(hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "chunk_id": hit["chunk_id"],
        "content_type": hit["content_type"],
        "score": hit["score"],
        "rrf_score": hit["rrf_score"],
        "route_ranks": hit["route_ranks"],
        "matched_fields": hit["matched_fields"],
        "file_name": hit["file_name"],
        "page": hit["page"],
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "text_excerpt": re.sub(r"\s+", " ", _plain_text(hit.get("text") or ""))[:500],
    }


def _store_shadow_run(
    run_id: str,
    query: str,
    status: str,
    duration_ms: float,
    payload: dict[str, Any],
    error: str,
    created_at: str,
) -> None:
    ensure_schema()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO retrieval_shadow_runs(
                   id, query, status, duration_ms, payload, error, created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                run_id, query, status, duration_ms,
                json.dumps(payload, ensure_ascii=False), error, created_at,
            ),
        )
        conn.execute(
            """DELETE FROM retrieval_shadow_runs
               WHERE id IN (
                 SELECT id FROM retrieval_shadow_runs
                 ORDER BY created_at DESC, id DESC
                 LIMIT -1 OFFSET ?
               )""",
            (SHADOW_MAX_RUNS,),
        )


def list_shadow_runs(*, limit: int = 25) -> list[dict[str, Any]]:
    ensure_schema()
    rows = db.get_conn().execute(
        """SELECT id, query, status, duration_ms, payload, error, created_at
           FROM retrieval_shadow_runs
           ORDER BY created_at DESC, id DESC
           LIMIT ?""",
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [_shadow_run_row(row, include_hits=True) for row in rows]


def get_shadow_run(run_id: str) -> dict[str, Any] | None:
    ensure_schema()
    row = db.get_conn().execute(
        """SELECT id, query, status, duration_ms, payload, error, created_at
           FROM retrieval_shadow_runs WHERE id=?""",
        (run_id,),
    ).fetchone()
    return _shadow_run_row(row, include_hits=True) if row else None


def _shadow_run_row(row: Any, *, include_hits: bool) -> dict[str, Any]:
    payload = chunk_schema.parse_json_object(row["payload"])
    if not include_hits:
        payload.pop("lexical_hits", None)
    return {
        "id": row["id"],
        "query": row["query"],
        "status": row["status"],
        "duration_ms": row["duration_ms"],
        "error": row["error"],
        "created_at": row["created_at"],
        "payload": payload,
    }
