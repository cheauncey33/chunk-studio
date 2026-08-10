"""Knowledge-base ownership and scoped retrieval APIs."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import chunk_schema, current_user, db, retrieval
from ..storage.object_store import get_object_store
from ..storage.repositories import get_content_repository, get_content_write_repository


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    retrieval_config: dict[str, Any] | None = None
    parser_config: dict[str, Any] | None = None
    manual_rules: dict[str, Any] | None = None
    few_shot_rules: dict[str, Any] | None = None
    default_naming_file_id: str | None = None
    clear_default_naming_file: bool = False


class KnowledgeBaseFileUpdate(BaseModel):
    enabled: bool | None = None
    role: Literal["source", "reference"] | None = None
    corpus_kind: Literal["standard", "spec"] | None = None


class RetrievalTestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8192)
    top_k: int = Field(default=10, ge=1, le=50)
    dense_threshold: float = Field(default=0.0, ge=0, le=1)
    rerank_threshold: float | None = Field(default=None, ge=0, le=1)
    # Compatibility for older clients; new responses never expose this name.
    similarity_threshold: float | None = Field(default=None, ge=-1, le=1)
    route_top_k: int = Field(default=30, ge=1, le=100)
    candidates_per_type: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=200)
    file_ids: list[str] | None = None


_NON_CORPUS_ROLES = frozenset({"report", "naming", "sample_report"})


def _workspace_id() -> str:
    return current_user.get_current_user().workspace_id


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _normalize_corpus_kind(value: str | None, role: str | None = None) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"standard", "spec"}:
        return kind
    if str(role or "").strip().lower() == "reference":
        return "spec"
    return "standard"


def _is_non_corpus_metadata(metadata: dict[str, Any]) -> bool:
    role = str(metadata.get("doc_role") or metadata.get("doc_type") or "").strip().lower()
    return role in _NON_CORPUS_ROLES


def _kb_out(row: Any) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    naming_file_id = (
        row["default_naming_file_id"] if "default_naming_file_id" in keys else None
    )
    naming_file_name = None
    if naming_file_id and "default_naming_file_name" in keys:
        naming_file_name = row["default_naming_file_name"]
    elif naming_file_id:
        name_row = db.get_conn().execute(
            "SELECT name FROM files WHERE id=? AND workspace_id=?",
            (naming_file_id, _workspace_id()),
        ).fetchone()
        naming_file_name = name_row["name"] if name_row else None
    assistant_id = None
    if "assistant_id" in keys:
        assistant_id = row["assistant_id"]
    else:
        assistant_id = db.assistant_id_for_knowledge_base(row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "is_default": bool(row["is_default"]),
        "parser_config": _loads(row["parser_config"], {}),
        "retrieval_config": retrieval.normalize_retrieval_config(
            _loads(row["retrieval_config"], {})
        ),
        "manual_rules": _loads(row["manual_rules"] if "manual_rules" in keys else "{}", {}),
        "few_shot_rules": _loads(row["few_shot_rules"] if "few_shot_rules" in keys else "{}", {}),
        "default_naming_file_id": naming_file_id,
        "default_naming_file_name": naming_file_name,
        "assistant_id": assistant_id,
        "file_count": int(row["file_count"]),
        "chunk_count": int(row["chunk_count"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_kb(knowledge_base_id: str) -> Any:
    repository = get_content_repository()
    if repository is not None:
        row = repository.get_knowledge_base(knowledge_base_id)
        if not row:
            raise HTTPException(404, "knowledge base not found")
        return row
    row = db.get_conn().execute(
        """SELECT kb.*,
                  nf.name AS default_naming_file_name,
                  (
                    SELECT akb.assistant_id
                    FROM assistant_knowledge_bases akb
                    WHERE akb.knowledge_base_id=kb.id
                      AND akb.workspace_id=kb.workspace_id AND akb.enabled=1
                    ORDER BY akb.priority ASC, akb.assistant_id ASC
                    LIMIT 1
                  ) AS assistant_id,
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
           LEFT JOIN files nf ON nf.id=kb.default_naming_file_id
                            AND nf.workspace_id=kb.workspace_id
           LEFT JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=kb.id
            AND kbf.workspace_id=kb.workspace_id AND kbf.enabled=1
           LEFT JOIN chunks c ON c.file_id=kbf.file_id
                            AND c.workspace_id=kb.workspace_id
           WHERE kb.id=? AND kb.workspace_id=?
           GROUP BY kb.id""",
        (knowledge_base_id, _workspace_id()),
    ).fetchone()
    if not row:
        raise HTTPException(404, "knowledge base not found")
    return row


@router.get("")
def list_knowledge_bases():
    repository = get_content_repository()
    if repository is not None:
        return [_kb_out(row) for row in repository.list_knowledge_bases()]
    rows = db.get_conn().execute(
        """SELECT kb.*,
                  nf.name AS default_naming_file_name,
                  (
                    SELECT akb.assistant_id
                    FROM assistant_knowledge_bases akb
                    WHERE akb.knowledge_base_id=kb.id
                      AND akb.workspace_id=kb.workspace_id AND akb.enabled=1
                    ORDER BY akb.priority ASC, akb.assistant_id ASC
                    LIMIT 1
                  ) AS assistant_id,
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
           LEFT JOIN files nf ON nf.id=kb.default_naming_file_id
                            AND nf.workspace_id=kb.workspace_id
           LEFT JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=kb.id
            AND kbf.workspace_id=kb.workspace_id AND kbf.enabled=1
           LEFT JOIN chunks c ON c.file_id=kbf.file_id
                            AND c.workspace_id=kb.workspace_id
           WHERE kb.status!='archived' AND kb.workspace_id=?
           GROUP BY kb.id
            ORDER BY kb.is_default DESC, kb.updated_at DESC, kb.name""",
        (_workspace_id(),),
    ).fetchall()
    return [_kb_out(row) for row in rows]


@router.post("")
def create_knowledge_base(body: KnowledgeBaseCreate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    knowledge_base_id = f"kb_{uuid.uuid4().hex}"
    from .. import chunk_pipeline

    content = get_content_write_repository()
    if content is not None:
        try:
            content.create_knowledge_base({
                "id": knowledge_base_id,
                "workspace_id": _workspace_id(),
                "name": body.name.strip(),
                "description": body.description.strip(),
                "parser_config": chunk_pipeline.DEFAULT_PARSER_CONFIG,
                "retrieval_config": {
                    "top_k": 10,
                    "dense_threshold": 0.0,
                    "rerank_threshold": 0.2,
                    "agentic_rag_enabled": True,
                    "agentic_rag_max_rounds": 3,
                    "agentic_rag_max_search_calls": 3,
                    "agentic_rag_timeout_seconds": 30.0,
                    "content_type": "all",
                },
                "manual_rules": {},
                "few_shot_rules": {},
                "created_at": now,
                "updated_at": now,
            })
            content.ensure_assistant_for_knowledge_base(
                knowledge_base_id,
                name=f"{body.name.strip()} audit",
                description=body.description.strip(),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(409, "knowledge base name already exists") from exc
            raise HTTPException(500, f"knowledge base creation failed: {exc}") from exc
        return _kb_out(_get_kb(knowledge_base_id))

    try:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO knowledge_bases
                   (id,workspace_id,name,description,status,is_default,parser_config,
                    retrieval_config,manual_rules,few_shot_rules,
                    default_naming_file_id,created_at,updated_at)
                   VALUES (?,?,?,?,'active',0,?,?,'{}','{}',NULL,?,?)""",
                (
                    knowledge_base_id,
                    _workspace_id(),
                    body.name.strip(),
                    body.description.strip(),
                    json.dumps(chunk_pipeline.DEFAULT_PARSER_CONFIG, ensure_ascii=False),
                    json.dumps(
                        {
                            "top_k": 10,
                            "dense_threshold": 0.0,
                            "rerank_threshold": 0.2,
                            "agentic_rag_enabled": True,
                            "agentic_rag_max_rounds": 3,
                            "agentic_rag_max_search_calls": 3,
                            "agentic_rag_timeout_seconds": 30.0,
                            "content_type": "all",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(409, "knowledge base name already exists") from exc
        raise
    try:
        db.ensure_assistant_for_knowledge_base(
            knowledge_base_id,
            name=f"{body.name.strip()}审查",
            description=(
                body.description.strip()
                or f"绑定知识库「{body.name.strip()}」的审查配置"
            ),
        )
    except Exception as exc:
        raise HTTPException(500, f"knowledge base created but assistant bind failed: {exc}") from exc
    return _kb_out(_get_kb(knowledge_base_id))


@router.get("/{knowledge_base_id}")
def get_knowledge_base(knowledge_base_id: str):
    return _kb_out(_get_kb(knowledge_base_id))


@router.delete("/{knowledge_base_id}")
def delete_knowledge_base(knowledge_base_id: str):
    """Delete a knowledge base and files that only belong to it.

    Default knowledge bases cannot be deleted. Files that are also linked to
    other knowledge bases keep their PDF/chunks and only lose this membership.
    """
    from .. import config

    content = get_content_write_repository()
    if content is not None:
        try:
            deleted = content.delete_knowledge_base(knowledge_base_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "knowledge base not found")

        store = get_object_store()
        for artifact in deleted.get("artifacts") or []:
            for path_value in [artifact.get("path"), *(artifact.get("crop_paths") or [])]:
                path = str(path_value or "").strip()
                if not path:
                    continue
                try:
                    config.from_rel(path).unlink(missing_ok=True)
                except OSError:
                    pass
            for object_key in [
                artifact.get("object_key"),
                *(artifact.get("crop_object_keys") or []),
            ]:
                key = str(object_key or "").strip()
                if not key:
                    continue
                try:
                    store.delete(key)
                except Exception:
                    logger.exception("failed to delete object %s", key)
        return {
            "ok": True,
            "deleted_file_count": int(deleted.get("deleted_file_count") or 0),
            "deleted_chunk_count": int(deleted.get("deleted_chunk_count") or 0),
        }

    row = _get_kb(knowledge_base_id)
    if bool(row["is_default"]):
        raise HTTPException(400, "默认知识库不可删除")

    owned = db.get_conn().execute(
        """SELECT f.id, f.path,
                  (SELECT COUNT(*) FROM knowledge_base_files o
                   WHERE o.file_id=f.id AND o.workspace_id=? AND o.knowledge_base_id!=?) AS other_kbs,
                  (SELECT COUNT(*) FROM chunks c WHERE c.file_id=f.id) AS chunk_count
           FROM knowledge_base_files kbf
           JOIN files f ON f.id=kbf.file_id
           WHERE kbf.knowledge_base_id=? AND kbf.workspace_id=? AND f.workspace_id=?""",
        (_workspace_id(), knowledge_base_id, knowledge_base_id, _workspace_id(), _workspace_id()),
    ).fetchall()

    exclusive = [dict(item) for item in owned if int(item["other_kbs"] or 0) == 0]
    exclusive_ids = [item["id"] for item in exclusive]
    deleted_chunk_count = sum(int(item["chunk_count"] or 0) for item in exclusive)
    deleted_file_count = len(exclusive_ids)
    paired_assistant_id = db.assistant_id_for_knowledge_base(knowledge_base_id)
    # Keep the generic template even if somehow bound.
    if paired_assistant_id == "assistant_audit_template":
        paired_assistant_id = None

    with db.transaction() as conn:
        if exclusive_ids:
            placeholders = ",".join("?" for _ in exclusive_ids)
            conn.execute(
                f"DELETE FROM chunks WHERE workspace_id=? AND file_id IN ({placeholders})",
                [_workspace_id(), *exclusive_ids],
            )
            conn.execute(
                f"DELETE FROM files WHERE workspace_id=? AND id IN ({placeholders})",
                [_workspace_id(), *exclusive_ids],
            )
        conn.execute(
            "DELETE FROM knowledge_bases WHERE id=? AND workspace_id=?",
            (knowledge_base_id, _workspace_id()),
        )
        if paired_assistant_id:
            conn.execute(
                "UPDATE audit_assistants SET active_version_id=NULL WHERE id=? AND workspace_id=?",
                (paired_assistant_id, _workspace_id()),
            )
            conn.execute(
                """DELETE FROM assistant_versions
                   WHERE assistant_id=? AND EXISTS (
                     SELECT 1 FROM audit_assistants a
                      WHERE a.id=assistant_versions.assistant_id
                        AND a.workspace_id=?
                   )""",
                (paired_assistant_id, _workspace_id()),
            )
            conn.execute(
                "DELETE FROM audit_assistants WHERE id=? AND workspace_id=?",
                (paired_assistant_id, _workspace_id()),
            )

    for item in exclusive:
        path = (item.get("path") or "").strip()
        if not path:
            continue
        try:
            config.from_rel(path).unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "ok": True,
        "deleted_file_count": deleted_file_count,
        "deleted_chunk_count": deleted_chunk_count,
    }


@router.patch("/{knowledge_base_id}")
def update_knowledge_base(knowledge_base_id: str, body: KnowledgeBaseUpdate):
    _get_kb(knowledge_base_id)
    content = get_content_write_repository()
    if content is not None:
        values: dict[str, Any] = {}
        for key in ("name", "description"):
            value = getattr(body, key)
            if value is not None:
                values[key] = value.strip()
        for key in ("retrieval_config", "parser_config", "manual_rules", "few_shot_rules"):
            value = getattr(body, key)
            if value is not None:
                values[key] = retrieval.normalize_retrieval_config(value) if key == "retrieval_config" else value
        if body.clear_default_naming_file:
            values["default_naming_file_id"] = None
        elif body.default_naming_file_id is not None:
            file_id = body.default_naming_file_id.strip()
            if file_id and not content.get_file(file_id):
                raise HTTPException(400, "naming-rule file not found")
            values["default_naming_file_id"] = file_id or None
        if values:
            content.update_knowledge_base(knowledge_base_id, values)
        return _kb_out(_get_kb(knowledge_base_id))
    updates: list[str] = []
    params: list[Any] = []
    for key in ("name", "description"):
        value = getattr(body, key)
        if value is not None:
            updates.append(f"{key}=?")
            params.append(value.strip())
    for key in ("retrieval_config", "parser_config", "manual_rules", "few_shot_rules"):
        value = getattr(body, key)
        if value is not None:
            if key == "retrieval_config":
                value = retrieval.normalize_retrieval_config(value)
            updates.append(f"{key}=?")
            params.append(json.dumps(value, ensure_ascii=False))
    naming_file_to_detach: str | None = None
    if body.clear_default_naming_file:
        updates.append("default_naming_file_id=?")
        params.append(None)
    elif body.default_naming_file_id is not None:
        file_id = body.default_naming_file_id.strip()
        if file_id:
            exists = db.get_conn().execute(
                "SELECT 1 FROM files WHERE id=? AND workspace_id=?",
                (file_id, _workspace_id()),
            ).fetchone()
            if not exists:
                raise HTTPException(400, "naming-rule file not found")
            updates.append("default_naming_file_id=?")
            params.append(file_id)
            naming_file_to_detach = file_id
        else:
            updates.append("default_naming_file_id=?")
            params.append(None)
    if updates or naming_file_to_detach:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with db.transaction() as conn:
            if naming_file_to_detach:
                conn.execute(
                    "DELETE FROM knowledge_base_files WHERE file_id=? AND workspace_id=?",
                    (naming_file_to_detach, _workspace_id()),
                )
            if updates:
                updates.append("updated_at=?")
                params.append(now)
                params.extend([knowledge_base_id, _workspace_id()])
                conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(updates)} WHERE id=? AND workspace_id=?",
                    params,
                )
    return _kb_out(_get_kb(knowledge_base_id))


_KB_FILE_SELECT = """SELECT f.id, f.name, f.page_count, f.metadata, f.created_at,
                              kbf.role, kbf.corpus_kind, kbf.enabled,
                              COUNT(c.id) AS chunk_count,
                              SUM(CASE WHEN c.status='approved' THEN 1 ELSE 0 END)
                                  AS approved_count,
                              (
                                SELECT p.status FROM document_parses p
                                WHERE p.file_id=f.id
                                ORDER BY p.created_at DESC LIMIT 1
                              ) AS parse_status,
                              (
                                SELECT p.error FROM document_parses p
                                WHERE p.file_id=f.id
                                ORDER BY p.created_at DESC LIMIT 1
                              ) AS parse_error,
                              (
                                SELECT p.markdown_path FROM document_parses p
                                WHERE p.file_id=f.id
                                ORDER BY p.created_at DESC LIMIT 1
                              ) AS parse_markdown_path,
                              (
                                SELECT p.result FROM document_parses p
                                WHERE p.file_id=f.id
                                ORDER BY p.created_at DESC LIMIT 1
                              ) AS parse_result
                       FROM knowledge_base_files kbf
                       JOIN files f ON f.id=kbf.file_id
                       LEFT JOIN chunks c ON c.file_id=f.id
                       WHERE kbf.knowledge_base_id=?
                         AND kbf.workspace_id=? AND f.workspace_id=?
                         AND (
                           SELECT kb.default_naming_file_id
                           FROM knowledge_bases kb
                           WHERE kb.id=kbf.knowledge_base_id
                         ) IS NOT f.id
                         AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_role'), ''))
                             NOT IN ('report', 'naming', 'sample_report')
                         AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_type'), ''))
                             NOT IN ('report', 'naming', 'sample_report')"""


def _kb_file_out(row: Any) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    parse_markdown_object_key = (
        row["parse_markdown_object_key"] if "parse_markdown_object_key" in keys else ""
    )
    corpus_kind = _normalize_corpus_kind(
        row["corpus_kind"] if "corpus_kind" in keys else None,
        row["role"] if "role" in keys else None,
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "page_count": row["page_count"],
        "metadata": _loads(row["metadata"], {}),
        "created_at": row["created_at"],
        "role": row["role"],
        "corpus_kind": corpus_kind,
        "enabled": bool(row["enabled"]),
        "chunk_count": int(row["chunk_count"] or 0),
        "approved_count": int(row["approved_count"] or 0),
        "parse_status": row["parse_status"],
        "parse_error": row["parse_error"] or "",
        "parse_ready": bool(
            row["parse_status"] == "done"
            and (row["parse_markdown_path"] or parse_markdown_object_key)
        ),
        # Auto-chunk failures don't fail the parse job; expose them here so the
        # files page can prompt a manual re-chunk.
        "auto_chunk_error": _auto_chunk_error_from_result(
            row["parse_result"] if "parse_result" in keys else None
        ),
    }


def _auto_chunk_error_from_result(parse_result: Any) -> str:
    result = _loads(parse_result, {})
    return str(result.get("auto_chunk_error") or "") if isinstance(result, dict) else ""


def _get_kb_file(knowledge_base_id: str, file_id: str) -> dict[str, Any]:
    repository = get_content_repository()
    if repository is not None:
        rows = repository.list_knowledge_base_files(
            knowledge_base_id,
            file_id=file_id,
            limit=1,
        )
        if not rows:
            raise HTTPException(404, "knowledge base file not found")
        return _kb_file_out(rows[0])
    row = db.get_conn().execute(
        f"""{_KB_FILE_SELECT}
            AND kbf.file_id=?
            GROUP BY f.id""",
        (knowledge_base_id, _workspace_id(), _workspace_id(), file_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "knowledge base file not found")
    return _kb_file_out(row)


@router.get("/{knowledge_base_id}/files")
def list_knowledge_base_files(knowledge_base_id: str):
    _get_kb(knowledge_base_id)
    repository = get_content_repository()
    if repository is not None:
        return [
            _kb_file_out(row)
            for row in repository.list_knowledge_base_files(knowledge_base_id)
        ]
    rows = db.get_conn().execute(
        f"""{_KB_FILE_SELECT}
            GROUP BY f.id
            ORDER BY f.created_at DESC""",
        (knowledge_base_id, _workspace_id(), _workspace_id()),
    ).fetchall()
    return [_kb_file_out(row) for row in rows]


@router.put("/{knowledge_base_id}/files/{file_id}")
def add_file_to_knowledge_base(knowledge_base_id: str, file_id: str):
    _get_kb(knowledge_base_id)
    content = get_content_write_repository()
    file_row = (
        content.get_file(file_id)
        if content is not None
        else db.get_conn().execute(
            "SELECT metadata FROM files WHERE id=? AND workspace_id=?",
            (file_id, _workspace_id()),
        ).fetchone()
    )
    if not file_row:
        raise HTTPException(404, "file not found")
    metadata = _loads(file_row["metadata"], {})
    if _is_non_corpus_metadata(metadata if isinstance(metadata, dict) else {}):
        raise HTTPException(400, "report/naming/sample_report files cannot join a knowledge base corpus")
    corpus_kind = _normalize_corpus_kind(
        str((metadata or {}).get("corpus_kind") or "") if isinstance(metadata, dict) else "",
    )
    if content is not None:
        content.attach_file_to_knowledge_base(
            knowledge_base_id,
            file_id,
            role="source",
            corpus_kind=corpus_kind,
        )
        return {"ok": True}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,workspace_id,role,corpus_kind,enabled,created_at)
               VALUES (?,?,?,'source',?,1,?)
               ON CONFLICT(knowledge_base_id,file_id)
               DO UPDATE SET enabled=1, corpus_kind=excluded.corpus_kind""",
            (knowledge_base_id, file_id, _workspace_id(), corpus_kind, now),
        )
        conn.execute(
            "UPDATE knowledge_bases SET updated_at=? WHERE id=? AND workspace_id=?",
            (now, knowledge_base_id, _workspace_id()),
        )
    return {"ok": True}


@router.patch("/{knowledge_base_id}/files/{file_id}")
def update_knowledge_base_file(
    knowledge_base_id: str,
    file_id: str,
    body: KnowledgeBaseFileUpdate,
):
    _get_kb(knowledge_base_id)
    content = get_content_write_repository()
    if content is not None:
        values: dict[str, Any] = {}
        if body.enabled is not None:
            values["enabled"] = body.enabled
        if body.corpus_kind is not None:
            values["corpus_kind"] = body.corpus_kind
        elif body.role is not None:
            values["role"] = body.role
            values["corpus_kind"] = "spec" if body.role == "reference" else "standard"
        if not content.update_knowledge_base_file(knowledge_base_id, file_id, values):
            raise HTTPException(404, "knowledge base file not found")
        return _get_kb_file(knowledge_base_id, file_id)
    if not db.get_conn().execute(
        """SELECT 1 FROM knowledge_base_files
           WHERE knowledge_base_id=? AND file_id=? AND workspace_id=?""",
        (knowledge_base_id, file_id, _workspace_id()),
    ).fetchone():
        raise HTTPException(404, "knowledge base file not found")
    updates: list[str] = []
    params: list[Any] = []
    if body.enabled is not None:
        updates.append("enabled=?")
        params.append(1 if body.enabled else 0)
    if body.corpus_kind is not None:
        updates.append("corpus_kind=?")
        params.append(body.corpus_kind)
    elif body.role is not None:
        # Legacy: reference → spec, source → standard
        updates.append("corpus_kind=?")
        params.append("spec" if body.role == "reference" else "standard")
        updates.append("role=?")
        params.append(body.role)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        if updates:
            params.extend([knowledge_base_id, file_id, _workspace_id()])
            conn.execute(
                f"""UPDATE knowledge_base_files
                    SET {', '.join(updates)}
                    WHERE knowledge_base_id=? AND file_id=? AND workspace_id=?""",
                params,
            )
        conn.execute(
            "UPDATE knowledge_bases SET updated_at=? WHERE id=? AND workspace_id=?",
            (now, knowledge_base_id, _workspace_id()),
        )
    return _get_kb_file(knowledge_base_id, file_id)


@router.delete("/{knowledge_base_id}/files/{file_id}")
def remove_file_from_knowledge_base(knowledge_base_id: str, file_id: str):
    kb = _get_kb(knowledge_base_id)
    if kb["is_default"]:
        raise HTTPException(409, "move the file to another knowledge base before removing it")
    content = get_content_write_repository()
    if content is not None:
        if not content.remove_file_from_knowledge_base(knowledge_base_id, file_id):
            raise HTTPException(404, "knowledge base file not found")
        return {"ok": True}
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM knowledge_base_files WHERE knowledge_base_id=? AND file_id=? AND workspace_id=?",
            (knowledge_base_id, file_id, _workspace_id()),
        )
        fallback = conn.execute(
            "SELECT id FROM knowledge_bases WHERE is_default=1 AND workspace_id=? LIMIT 1",
            (_workspace_id(),),
        ).fetchone()
        if fallback and not conn.execute(
            "SELECT 1 FROM knowledge_base_files WHERE file_id=? AND workspace_id=?",
            (file_id, _workspace_id()),
        ).fetchone():
            conn.execute(
                """INSERT INTO knowledge_base_files
                   (knowledge_base_id,file_id,workspace_id,role,corpus_kind,enabled,created_at)
                   VALUES (?,?,?,'source','standard',1,?)""",
                (fallback["id"], file_id, _workspace_id(), time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
    return {"ok": True}


@router.get("/{knowledge_base_id}/chunks")
def list_knowledge_base_chunks(
    knowledge_base_id: str,
    limit: int = 100,
    offset: int = 0,
):
    _get_kb(knowledge_base_id)
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    repository = get_content_repository()
    if repository is not None:
        rows = repository.list_knowledge_base_chunks(
            knowledge_base_id,
            limit=limit,
            offset=offset,
        )
        return [
            {
                "id": row["id"],
                "file_id": row["file_id"],
                "file_name": row["file_name"],
                "page": row["page"],
                "text": row["text"] or "",
                "status": row["status"],
                "business_metadata": chunk_schema.parse_json_object(row["business_metadata"]),
                "source_trace": chunk_schema.parse_json_object(row["source_trace"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    rows = db.get_conn().execute(
        """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.text,
                  c.status, c.business_metadata, c.source_trace, c.updated_at
           FROM knowledge_base_files kbf
           JOIN chunks c ON c.file_id=kbf.file_id
           JOIN files f ON f.id=c.file_id
           WHERE kbf.knowledge_base_id=? AND kbf.workspace_id=?
             AND c.workspace_id=? AND f.workspace_id=? AND kbf.enabled=1
           ORDER BY c.updated_at DESC, c.id
           LIMIT ? OFFSET ?""",
        (knowledge_base_id, _workspace_id(), _workspace_id(), _workspace_id(), limit, offset),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "page": row["page"],
            "text": row["text"] or "",
            "status": row["status"],
            "business_metadata": chunk_schema.parse_json_object(row["business_metadata"]),
            "source_trace": chunk_schema.parse_json_object(row["source_trace"]),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


@router.post("/{knowledge_base_id}/retrieval-test")
def retrieval_test(knowledge_base_id: str, body: RetrievalTestRequest):
    _get_kb(knowledge_base_id)
    membership_rows = db.get_conn().execute(
        """SELECT file_id, enabled FROM knowledge_base_files
           WHERE knowledge_base_id=? AND workspace_id=?""",
        (knowledge_base_id, _workspace_id()),
    ).fetchall()
    membership = {row["file_id"]: bool(row["enabled"]) for row in membership_rows}
    if body.file_ids is None:
        file_ids = [file_id for file_id, enabled in membership.items() if enabled]
    else:
        unknown = [file_id for file_id in body.file_ids if file_id not in membership]
        if unknown:
            raise HTTPException(
                400,
                f"file_ids not in this knowledge base: {', '.join(unknown[:5])}",
            )
        # Preserve request order while de-duplicating.
        seen: set[str] = set()
        file_ids = []
        for file_id in body.file_ids:
            if file_id in seen:
                continue
            seen.add(file_id)
            file_ids.append(file_id)
    rerank_threshold = (
        body.rerank_threshold
        if body.rerank_threshold is not None
        else (
            body.similarity_threshold
            if body.similarity_threshold is not None and body.similarity_threshold >= 0
            else retrieval.DEFAULT_RERANK_THRESHOLD
        )
    )
    try:
        result = retrieval.hybrid_search(
            body.query,
            top_k=body.top_k,
            route_top_k=body.route_top_k,
            candidates_per_type=body.candidates_per_type,
            rrf_k=body.rrf_k,
            dense_threshold=body.dense_threshold,
            rerank_threshold=rerank_threshold,
            file_ids=file_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    result["knowledge_base_id"] = knowledge_base_id
    result["scoped_file_count"] = len(file_ids)
    result["scoped_file_ids"] = file_ids
    result["retrieval_params"] = {
        "top_k": body.top_k,
        "route_top_k": body.route_top_k,
        "candidates_per_type": body.candidates_per_type,
        "rrf_k": body.rrf_k,
        "dense_threshold": body.dense_threshold,
        "rerank_threshold": rerank_threshold,
    }
    return result
