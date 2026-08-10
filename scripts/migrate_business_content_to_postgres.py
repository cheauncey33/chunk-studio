"""Migrate workspace-owned business content from SQLite to PostgreSQL.

This is the first business-data cutover slice.  It migrates files, chunks,
knowledge bases, assistant configuration, parse records and audit reviews.
The SQLite database remains the source of truth until the Repository slice is
switched on.  The command is dry-run by default and is safe to repeat while
that source-of-truth rule is in effect.

Embeddings are intentionally excluded: ``migrate_embeddings_to_pgvector.py``
owns that boundary and stores vectors in ``chunk_vector_index``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import config, db
from app.storage.repositories import postgres_content_schema_sql


CONTENT_TABLES = (
    "files",
    "chunks",
    "document_parses",
    "knowledge_bases",
    "knowledge_base_files",
    "audit_assistants",
    "assistant_versions",
    "assistant_knowledge_bases",
    "assistant_init_drafts",
    "audit_case_reviews",
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, default=config.DB_PATH)
    parser.add_argument("--dsn", default=config.DATABASE_URL)
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _configure_sqlite(path: Path) -> None:
    config.DB_PATH = path
    db._conn = None
    db.init_db()


def _jsonb(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return json.dumps(parsed if isinstance(parsed, (dict, list)) else {}, ensure_ascii=False)


def _bool(value: Any) -> bool:
    return bool(value) and str(value).strip().lower() not in {"0", "false", "no"}


def _scope_clause(scope: str, column: str = "workspace_id") -> tuple[str, tuple[str, ...]]:
    if scope:
        return f" WHERE {column}=?", (scope,)
    return "", ()


def _rows(workspace_id: str = "") -> dict[str, list[dict[str, Any]]]:
    """Read only rows owned by the requested workspace from SQLite."""
    scope = workspace_id.strip()
    conn = db.get_conn()
    where, params = _scope_clause(scope)
    rows: dict[str, list[dict[str, Any]]] = {
        "files": [dict(row) for row in conn.execute(
            "SELECT * FROM files" + where + " ORDER BY created_at, id", params
        ).fetchall()],
        "knowledge_bases": [dict(row) for row in conn.execute(
            "SELECT * FROM knowledge_bases" + where + " ORDER BY created_at, id", params
        ).fetchall()],
        "audit_assistants": [dict(row) for row in conn.execute(
            "SELECT * FROM audit_assistants" + where + " ORDER BY created_at, id", params
        ).fetchall()],
        "audit_case_reviews": [dict(row) for row in conn.execute(
            "SELECT * FROM audit_case_reviews" + where
            + " ORDER BY updated_at, report_name, case_id", params
        ).fetchall()],
    }

    chunk_where = " WHERE c.workspace_id=? AND f.workspace_id=?" if scope else ""
    chunk_params: tuple[str, ...] = (scope, scope) if scope else ()
    rows["chunks"] = [dict(row) for row in conn.execute(
        """SELECT c.* FROM chunks c
           JOIN files f ON f.id=c.file_id
        """ + chunk_where + " ORDER BY c.created_at, c.id", chunk_params
    ).fetchall()]
    rows["document_parses"] = [dict(row) for row in conn.execute(
        """SELECT p.* FROM document_parses p
           JOIN files f ON f.id=p.file_id
        """ + chunk_where.replace("c.workspace_id", "p.workspace_id")
        .replace("c.file_id", "p.file_id")
        + " ORDER BY p.created_at, p.id",
        chunk_params,
    ).fetchall()]
    rows["knowledge_base_files"] = [dict(row) for row in conn.execute(
        "SELECT * FROM knowledge_base_files" + where
        + " ORDER BY created_at, knowledge_base_id, file_id", params
    ).fetchall()]

    assistant_ids = [row["id"] for row in rows["audit_assistants"]]
    if assistant_ids:
        placeholders = ",".join("?" for _ in assistant_ids)
        rows["assistant_versions"] = [dict(row) for row in conn.execute(
            "SELECT * FROM assistant_versions WHERE assistant_id IN ("
            + placeholders + ") ORDER BY assistant_id, version",
            assistant_ids,
        ).fetchall()]
        rows["assistant_init_drafts"] = [dict(row) for row in conn.execute(
            "SELECT * FROM assistant_init_drafts WHERE assistant_id IN ("
            + placeholders + ") ORDER BY assistant_id",
            assistant_ids,
        ).fetchall()]
        assistant_kb_sql = (
            "SELECT * FROM assistant_knowledge_bases WHERE assistant_id IN ("
            + placeholders + ")"
        )
        assistant_kb_params: list[Any] = list(assistant_ids)
        if scope:
            assistant_kb_sql += " AND workspace_id=?"
            assistant_kb_params.append(scope)
        assistant_kb_sql += " ORDER BY assistant_id, knowledge_base_id"
        rows["assistant_knowledge_bases"] = [dict(row) for row in conn.execute(
            assistant_kb_sql, assistant_kb_params
        ).fetchall()]
    else:
        rows["assistant_versions"] = []
        rows["assistant_init_drafts"] = []
        rows["assistant_knowledge_bases"] = []

    return rows


def _summary(rows: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(items) for name, items in rows.items()}


def _apply(dsn: str, rows: dict[str, list[dict[str, Any]]]) -> None:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise SystemExit("psycopg is required; install the postgres dependency first") from exc

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cursor:
            for statement in postgres_content_schema_sql():
                cursor.execute(statement)

            for row in rows["files"]:
                cursor.execute(
                    """INSERT INTO files
                       (id, workspace_id, name, path, sha, object_key, page_count, metadata, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,COALESCE(%s, now()))
                       ON CONFLICT (id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, name=EXCLUDED.name,
                         path=EXCLUDED.path, sha=EXCLUDED.sha, object_key=EXCLUDED.object_key,
                         page_count=EXCLUDED.page_count, metadata=EXCLUDED.metadata,
                         created_at=EXCLUDED.created_at""",
                    (row["id"], row["workspace_id"], row["name"], row["path"], row.get("sha"),
                     row.get("object_key") or "", row.get("page_count"), _jsonb(row.get("metadata")),
                     row.get("created_at")),
                )
            for row in rows["chunks"]:
                cursor.execute(
                    """INSERT INTO chunks
                       (id,workspace_id,file_id,page,bbox,rotation,crop_path,text,text_source,
                        metadata,business_metadata,metadata_llm,source_trace,chunk_logic,relations,
                        ui_state,indexing,status,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                               %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,
                               COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, file_id=EXCLUDED.file_id,
                         page=EXCLUDED.page, bbox=EXCLUDED.bbox, rotation=EXCLUDED.rotation,
                         crop_path=EXCLUDED.crop_path, text=EXCLUDED.text, text_source=EXCLUDED.text_source,
                         metadata=EXCLUDED.metadata, business_metadata=EXCLUDED.business_metadata,
                         metadata_llm=EXCLUDED.metadata_llm, source_trace=EXCLUDED.source_trace,
                         chunk_logic=EXCLUDED.chunk_logic, relations=EXCLUDED.relations,
                         ui_state=EXCLUDED.ui_state, indexing=EXCLUDED.indexing, status=EXCLUDED.status,
                         updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["workspace_id"], row["file_id"], row["page"], _jsonb(row.get("bbox")),
                     row.get("rotation") or 0, row.get("crop_path"), row.get("text"),
                     row.get("text_source") or "pending", _jsonb(row.get("metadata")),
                     _jsonb(row.get("business_metadata")), _jsonb(row.get("metadata_llm")),
                     _jsonb(row.get("source_trace")), _jsonb(row.get("chunk_logic")),
                     _jsonb(row.get("relations")), _jsonb(row.get("ui_state")), _jsonb(row.get("indexing")),
                     row.get("status") or "pending", row.get("created_at"), row.get("updated_at")),
                )
            for row in rows["document_parses"]:
                cursor.execute(
                    """INSERT INTO document_parses
                       (id,workspace_id,file_id,provider,status,markdown_path,raw_zip_path,
                        markdown_object_key,raw_zip_object_key,result,error,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'','',%s::jsonb,%s,
                               COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, file_id=EXCLUDED.file_id,
                         provider=EXCLUDED.provider, status=EXCLUDED.status,
                         markdown_path=EXCLUDED.markdown_path, raw_zip_path=EXCLUDED.raw_zip_path,
                         result=EXCLUDED.result, error=EXCLUDED.error, updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["workspace_id"], row["file_id"], row["provider"], row["status"],
                     row.get("markdown_path"), row.get("raw_zip_path"), _jsonb(row.get("result")),
                     row.get("error") or "", row.get("created_at"), row.get("updated_at")),
                )
            for row in rows["knowledge_bases"]:
                cursor.execute(
                    """INSERT INTO knowledge_bases
                       (id,workspace_id,name,description,status,is_default,parser_config,
                        retrieval_config,manual_rules,few_shot_rules,default_naming_file_id,
                        created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,
                               COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, name=EXCLUDED.name,
                         description=EXCLUDED.description, status=EXCLUDED.status,
                         is_default=EXCLUDED.is_default, parser_config=EXCLUDED.parser_config,
                         retrieval_config=EXCLUDED.retrieval_config, manual_rules=EXCLUDED.manual_rules,
                         few_shot_rules=EXCLUDED.few_shot_rules,
                         default_naming_file_id=EXCLUDED.default_naming_file_id,
                         updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["workspace_id"], row["name"], row["description"], row["status"],
                     _bool(row["is_default"]), _jsonb(row.get("parser_config")),
                     _jsonb(row.get("retrieval_config")), _jsonb(row.get("manual_rules")),
                     _jsonb(row.get("few_shot_rules")), row.get("default_naming_file_id"),
                     row.get("created_at"), row.get("updated_at")),
                )
            for row in rows["knowledge_base_files"]:
                cursor.execute(
                    """INSERT INTO knowledge_base_files
                       (knowledge_base_id,file_id,workspace_id,role,corpus_kind,enabled,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s, now()))
                       ON CONFLICT (knowledge_base_id,file_id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, role=EXCLUDED.role,
                         corpus_kind=EXCLUDED.corpus_kind, enabled=EXCLUDED.enabled""",
                    (row["knowledge_base_id"], row["file_id"], row["workspace_id"], row["role"],
                     row["corpus_kind"], _bool(row["enabled"]), row.get("created_at")),
                )
            for row in rows["audit_assistants"]:
                cursor.execute(
                    """INSERT INTO audit_assistants
                       (id,workspace_id,name,description,status,active_version_id,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, name=EXCLUDED.name,
                         description=EXCLUDED.description, status=EXCLUDED.status,
                         active_version_id=EXCLUDED.active_version_id, updated_at=EXCLUDED.updated_at""",
                    (row["id"], row["workspace_id"], row["name"], row["description"], row["status"],
                     row.get("active_version_id"), row.get("created_at"), row.get("updated_at")),
                )
            for row in rows["assistant_versions"]:
                cursor.execute(
                    """INSERT INTO assistant_versions
                       (id,assistant_id,version,name,status,model_config,node_prompts,rules,
                        retrieval_config,parameter_schema,category_profile,initialization_provenance,
                        created_at,activated_at)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                               %s::jsonb,%s::jsonb,COALESCE(%s, now()),%s)
                       ON CONFLICT (id) DO UPDATE SET
                         assistant_id=EXCLUDED.assistant_id, version=EXCLUDED.version,
                         name=EXCLUDED.name, status=EXCLUDED.status, model_config=EXCLUDED.model_config,
                         node_prompts=EXCLUDED.node_prompts, rules=EXCLUDED.rules,
                         retrieval_config=EXCLUDED.retrieval_config, parameter_schema=EXCLUDED.parameter_schema,
                         category_profile=EXCLUDED.category_profile,
                         initialization_provenance=EXCLUDED.initialization_provenance,
                         activated_at=EXCLUDED.activated_at""",
                    (row["id"], row["assistant_id"], row["version"], row["name"], row["status"],
                     _jsonb(row.get("model_config")), _jsonb(row.get("node_prompts")), _jsonb(row.get("rules")),
                     _jsonb(row.get("retrieval_config")), _jsonb(row.get("parameter_schema")),
                     _jsonb(row.get("category_profile")), _jsonb(row.get("initialization_provenance")),
                     row.get("created_at"), row.get("activated_at")),
                )
            for row in rows["assistant_knowledge_bases"]:
                cursor.execute(
                    """INSERT INTO assistant_knowledge_bases
                       (assistant_id,knowledge_base_id,workspace_id,priority,enabled)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (assistant_id,knowledge_base_id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, priority=EXCLUDED.priority,
                         enabled=EXCLUDED.enabled""",
                    (row["assistant_id"], row["knowledge_base_id"], row["workspace_id"],
                     row["priority"], _bool(row["enabled"])),
                )
            for row in rows["assistant_init_drafts"]:
                cursor.execute(
                    """INSERT INTO assistant_init_drafts
                       (assistant_id,workspace_id,status,payload,job_id,created_at,updated_at)
                       VALUES (%s,%s,%s,%s::jsonb,%s,COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (assistant_id) DO UPDATE SET
                         workspace_id=EXCLUDED.workspace_id, status=EXCLUDED.status,
                         payload=EXCLUDED.payload, job_id=EXCLUDED.job_id, updated_at=EXCLUDED.updated_at""",
                    (row["assistant_id"], row["workspace_id"], row["status"], _jsonb(row.get("payload")),
                     row.get("job_id"), row.get("created_at"), row.get("updated_at")),
                )
            for row in rows["audit_case_reviews"]:
                cursor.execute(
                    """INSERT INTO audit_case_reviews
                       (report_name,case_id,workspace_id,status,corrected_status,note,reviewer,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, now()),COALESCE(%s, now()))
                       ON CONFLICT (workspace_id,report_name,case_id) DO UPDATE SET
                         status=EXCLUDED.status, corrected_status=EXCLUDED.corrected_status,
                         note=EXCLUDED.note, reviewer=EXCLUDED.reviewer, updated_at=EXCLUDED.updated_at""",
                    (row["report_name"], row["case_id"], row["workspace_id"], row["status"],
                     row["corrected_status"], row["note"], row["reviewer"], row.get("created_at"),
                     row.get("updated_at")),
                )
        conn.commit()


def main() -> int:
    args = _args()
    _configure_sqlite(args.sqlite_path)
    rows = _rows(args.workspace_id)
    report = {
        "sqlite_path": str(args.sqlite_path),
        "workspace_id": args.workspace_id or None,
        "apply": bool(args.apply),
        "counts": _summary(rows),
        "embeddings": "excluded; use migrate_embeddings_to_pgvector.py",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run: no PostgreSQL writes performed")
        return 0
    if not args.dsn:
        raise SystemExit("--dsn or DATABASE_URL is required with --apply")
    _apply(args.dsn, rows)
    print(json.dumps({"applied": report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
