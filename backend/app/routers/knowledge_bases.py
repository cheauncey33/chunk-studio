"""Knowledge-base ownership and scoped retrieval APIs."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import chunk_schema, db, retrieval


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
    similarity_threshold: float = Field(default=0.2, ge=-1, le=1)
    route_top_k: int = Field(default=30, ge=1, le=100)
    candidates_per_type: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=200)
    file_ids: list[str] | None = None


_NON_CORPUS_ROLES = frozenset({"report", "naming"})


def _loads(value: str | None, fallback: Any) -> Any:
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
            "SELECT name FROM files WHERE id=?",
            (naming_file_id,),
        ).fetchone()
        naming_file_name = name_row["name"] if name_row else None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "is_default": bool(row["is_default"]),
        "parser_config": _loads(row["parser_config"], {}),
        "retrieval_config": _loads(row["retrieval_config"], {}),
        "manual_rules": _loads(row["manual_rules"] if "manual_rules" in keys else "{}", {}),
        "few_shot_rules": _loads(row["few_shot_rules"] if "few_shot_rules" in keys else "{}", {}),
        "default_naming_file_id": naming_file_id,
        "default_naming_file_name": naming_file_name,
        "file_count": int(row["file_count"]),
        "chunk_count": int(row["chunk_count"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_kb(knowledge_base_id: str) -> Any:
    row = db.get_conn().execute(
        """SELECT kb.*,
                  nf.name AS default_naming_file_name,
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
           LEFT JOIN files nf ON nf.id=kb.default_naming_file_id
           LEFT JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=kb.id AND kbf.enabled=1
           LEFT JOIN chunks c ON c.file_id=kbf.file_id
           WHERE kb.id=?
           GROUP BY kb.id""",
        (knowledge_base_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "knowledge base not found")
    return row


@router.get("")
def list_knowledge_bases():
    rows = db.get_conn().execute(
        """SELECT kb.*,
                  nf.name AS default_naming_file_name,
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
           LEFT JOIN files nf ON nf.id=kb.default_naming_file_id
           LEFT JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=kb.id AND kbf.enabled=1
           LEFT JOIN chunks c ON c.file_id=kbf.file_id
           WHERE kb.status!='archived'
           GROUP BY kb.id
           ORDER BY kb.is_default DESC, kb.updated_at DESC, kb.name"""
    ).fetchall()
    return [_kb_out(row) for row in rows]


@router.post("")
def create_knowledge_base(body: KnowledgeBaseCreate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    knowledge_base_id = f"kb_{uuid.uuid4().hex}"
    from .. import chunk_pipeline

    try:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO knowledge_bases
                   (id,name,description,status,is_default,parser_config,
                    retrieval_config,manual_rules,few_shot_rules,
                    default_naming_file_id,created_at,updated_at)
                   VALUES (?,?,?,'active',0,?,?,'{}','{}',NULL,?,?)""",
                (
                    knowledge_base_id,
                    body.name.strip(),
                    body.description.strip(),
                    json.dumps(chunk_pipeline.DEFAULT_PARSER_CONFIG, ensure_ascii=False),
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
                    now,
                    now,
                ),
            )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(409, "knowledge base name already exists") from exc
        raise
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

    row = _get_kb(knowledge_base_id)
    if bool(row["is_default"]):
        raise HTTPException(400, "默认知识库不可删除")

    owned = db.get_conn().execute(
        """SELECT f.id, f.path,
                  (SELECT COUNT(*) FROM knowledge_base_files o
                   WHERE o.file_id=f.id AND o.knowledge_base_id!=?) AS other_kbs,
                  (SELECT COUNT(*) FROM chunks c WHERE c.file_id=f.id) AS chunk_count
           FROM knowledge_base_files kbf
           JOIN files f ON f.id=kbf.file_id
           WHERE kbf.knowledge_base_id=?""",
        (knowledge_base_id, knowledge_base_id),
    ).fetchall()

    exclusive = [dict(item) for item in owned if int(item["other_kbs"] or 0) == 0]
    exclusive_ids = [item["id"] for item in exclusive]
    deleted_chunk_count = sum(int(item["chunk_count"] or 0) for item in exclusive)
    deleted_file_count = len(exclusive_ids)

    with db.transaction() as conn:
        if exclusive_ids:
            placeholders = ",".join("?" for _ in exclusive_ids)
            conn.execute(
                f"DELETE FROM chunks WHERE file_id IN ({placeholders})",
                exclusive_ids,
            )
            conn.execute(
                f"DELETE FROM files WHERE id IN ({placeholders})",
                exclusive_ids,
            )
        conn.execute(
            "DELETE FROM knowledge_bases WHERE id=?",
            (knowledge_base_id,),
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
                "SELECT 1 FROM files WHERE id=?",
                (file_id,),
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
                    "DELETE FROM knowledge_base_files WHERE file_id=?",
                    (naming_file_to_detach,),
                )
            if updates:
                updates.append("updated_at=?")
                params.append(now)
                params.append(knowledge_base_id)
                conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(updates)} WHERE id=?",
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
                              ) AS parse_markdown_path
                       FROM knowledge_base_files kbf
                       JOIN files f ON f.id=kbf.file_id
                       LEFT JOIN chunks c ON c.file_id=f.id
                       WHERE kbf.knowledge_base_id=?
                         AND (
                           SELECT kb.default_naming_file_id
                           FROM knowledge_bases kb
                           WHERE kb.id=kbf.knowledge_base_id
                         ) IS NOT f.id
                         AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_role'), ''))
                             NOT IN ('report', 'naming')
                         AND LOWER(COALESCE(json_extract(f.metadata, '$.doc_type'), ''))
                             NOT IN ('report', 'naming')"""


def _kb_file_out(row: Any) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
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
            row["parse_status"] == "done" and row["parse_markdown_path"]
        ),
    }


def _get_kb_file(knowledge_base_id: str, file_id: str) -> dict[str, Any]:
    row = db.get_conn().execute(
        f"""{_KB_FILE_SELECT}
            AND kbf.file_id=?
            GROUP BY f.id""",
        (knowledge_base_id, file_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "knowledge base file not found")
    return _kb_file_out(row)


@router.get("/{knowledge_base_id}/files")
def list_knowledge_base_files(knowledge_base_id: str):
    _get_kb(knowledge_base_id)
    rows = db.get_conn().execute(
        f"""{_KB_FILE_SELECT}
            GROUP BY f.id
            ORDER BY f.created_at DESC""",
        (knowledge_base_id,),
    ).fetchall()
    return [_kb_file_out(row) for row in rows]


@router.put("/{knowledge_base_id}/files/{file_id}")
def add_file_to_knowledge_base(knowledge_base_id: str, file_id: str):
    _get_kb(knowledge_base_id)
    file_row = db.get_conn().execute(
        "SELECT metadata FROM files WHERE id=?",
        (file_id,),
    ).fetchone()
    if not file_row:
        raise HTTPException(404, "file not found")
    metadata = _loads(file_row["metadata"], {})
    if _is_non_corpus_metadata(metadata if isinstance(metadata, dict) else {}):
        raise HTTPException(400, "report/naming files cannot join a knowledge base corpus")
    corpus_kind = _normalize_corpus_kind(
        str((metadata or {}).get("corpus_kind") or "") if isinstance(metadata, dict) else "",
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
               VALUES (?,?,'source',?,1,?)
               ON CONFLICT(knowledge_base_id,file_id)
               DO UPDATE SET enabled=1, corpus_kind=excluded.corpus_kind""",
            (knowledge_base_id, file_id, corpus_kind, now),
        )
        conn.execute(
            "UPDATE knowledge_bases SET updated_at=? WHERE id=?",
            (now, knowledge_base_id),
        )
    return {"ok": True}


@router.patch("/{knowledge_base_id}/files/{file_id}")
def update_knowledge_base_file(
    knowledge_base_id: str,
    file_id: str,
    body: KnowledgeBaseFileUpdate,
):
    _get_kb(knowledge_base_id)
    if not db.get_conn().execute(
        """SELECT 1 FROM knowledge_base_files
           WHERE knowledge_base_id=? AND file_id=?""",
        (knowledge_base_id, file_id),
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
            params.extend([knowledge_base_id, file_id])
            conn.execute(
                f"""UPDATE knowledge_base_files
                    SET {', '.join(updates)}
                    WHERE knowledge_base_id=? AND file_id=?""",
                params,
            )
        conn.execute(
            "UPDATE knowledge_bases SET updated_at=? WHERE id=?",
            (now, knowledge_base_id),
        )
    return _get_kb_file(knowledge_base_id, file_id)


@router.delete("/{knowledge_base_id}/files/{file_id}")
def remove_file_from_knowledge_base(knowledge_base_id: str, file_id: str):
    kb = _get_kb(knowledge_base_id)
    if kb["is_default"]:
        raise HTTPException(409, "move the file to another knowledge base before removing it")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM knowledge_base_files WHERE knowledge_base_id=? AND file_id=?",
            (knowledge_base_id, file_id),
        )
        fallback = conn.execute(
            "SELECT id FROM knowledge_bases WHERE is_default=1 LIMIT 1"
        ).fetchone()
        if fallback and not conn.execute(
            "SELECT 1 FROM knowledge_base_files WHERE file_id=?", (file_id,)
        ).fetchone():
            conn.execute(
                """INSERT INTO knowledge_base_files
                   (knowledge_base_id,file_id,role,corpus_kind,enabled,created_at)
                   VALUES (?,?,'source','standard',1,?)""",
                (fallback["id"], file_id, time.strftime("%Y-%m-%dT%H:%M:%S")),
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
    rows = db.get_conn().execute(
        """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.text,
                  c.status, c.business_metadata, c.source_trace, c.updated_at
           FROM knowledge_base_files kbf
           JOIN chunks c ON c.file_id=kbf.file_id
           JOIN files f ON f.id=c.file_id
           WHERE kbf.knowledge_base_id=? AND kbf.enabled=1
           ORDER BY c.updated_at DESC, c.id
           LIMIT ? OFFSET ?""",
        (knowledge_base_id, limit, offset),
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
           WHERE knowledge_base_id=?""",
        (knowledge_base_id,),
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
    try:
        result = retrieval.hybrid_search(
            body.query,
            top_k=body.top_k,
            route_top_k=body.route_top_k,
            candidates_per_type=body.candidates_per_type,
            rrf_k=body.rrf_k,
            similarity_threshold=body.similarity_threshold,
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
        "similarity_threshold": body.similarity_threshold,
    }
    return result
