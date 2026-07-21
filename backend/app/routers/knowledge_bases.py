"""Knowledge-base ownership and scoped retrieval APIs."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

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


class RetrievalTestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8192)
    top_k: int = Field(default=10, ge=1, le=50)
    similarity_threshold: float = Field(default=0.2, ge=-1, le=1)
    route_top_k: int = Field(default=30, ge=1, le=100)
    candidates_per_type: int = Field(default=20, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=200)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _kb_out(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "is_default": bool(row["is_default"]),
        "parser_config": _loads(row["parser_config"], {}),
        "retrieval_config": _loads(row["retrieval_config"], {}),
        "file_count": int(row["file_count"]),
        "chunk_count": int(row["chunk_count"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_kb(knowledge_base_id: str) -> Any:
    row = db.get_conn().execute(
        """SELECT kb.*,
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
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
                  COUNT(DISTINCT kbf.file_id) AS file_count,
                  COUNT(DISTINCT c.id) AS chunk_count
           FROM knowledge_bases kb
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
    try:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO knowledge_bases
                   (id,name,description,status,is_default,parser_config,
                    retrieval_config,created_at,updated_at)
                   VALUES (?,?,?,'active',0,'{}',?, ?,?)""",
                (
                    knowledge_base_id,
                    body.name.strip(),
                    body.description.strip(),
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
    if body.retrieval_config is not None:
        updates.append("retrieval_config=?")
        params.append(json.dumps(body.retrieval_config, ensure_ascii=False))
    if updates:
        updates.append("updated_at=?")
        params.append(time.strftime("%Y-%m-%dT%H:%M:%S"))
        params.append(knowledge_base_id)
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE knowledge_bases SET {', '.join(updates)} WHERE id=?",
                params,
            )
    return _kb_out(_get_kb(knowledge_base_id))


@router.get("/{knowledge_base_id}/files")
def list_knowledge_base_files(knowledge_base_id: str):
    _get_kb(knowledge_base_id)
    rows = db.get_conn().execute(
        """SELECT f.id, f.name, f.page_count, f.metadata, f.created_at,
                  kbf.role, kbf.enabled,
                  COUNT(c.id) AS chunk_count,
                  SUM(CASE WHEN c.status='approved' THEN 1 ELSE 0 END) AS approved_count,
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
           GROUP BY f.id
           ORDER BY f.created_at DESC""",
        (knowledge_base_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "page_count": row["page_count"],
            "metadata": _loads(row["metadata"], {}),
            "created_at": row["created_at"],
            "role": row["role"],
            "enabled": bool(row["enabled"]),
            "chunk_count": int(row["chunk_count"] or 0),
            "approved_count": int(row["approved_count"] or 0),
            "parse_status": row["parse_status"],
            "parse_error": row["parse_error"] or "",
            "parse_ready": bool(
                row["parse_status"] == "done" and row["parse_markdown_path"]
            ),
        }
        for row in rows
    ]


@router.put("/{knowledge_base_id}/files/{file_id}")
def add_file_to_knowledge_base(knowledge_base_id: str, file_id: str):
    _get_kb(knowledge_base_id)
    if not db.get_conn().execute("SELECT 1 FROM files WHERE id=?", (file_id,)).fetchone():
        raise HTTPException(404, "file not found")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,role,enabled,created_at)
               VALUES (?,?,'source',1,?)
               ON CONFLICT(knowledge_base_id,file_id)
               DO UPDATE SET enabled=1""",
            (knowledge_base_id, file_id, now),
        )
        conn.execute(
            "UPDATE knowledge_bases SET updated_at=? WHERE id=?",
            (now, knowledge_base_id),
        )
    return {"ok": True}


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
                   (knowledge_base_id,file_id,role,enabled,created_at)
                   VALUES (?,?,'source',1,?)""",
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
    file_rows = db.get_conn().execute(
        """SELECT file_id FROM knowledge_base_files
           WHERE knowledge_base_id=? AND enabled=1""",
        (knowledge_base_id,),
    ).fetchall()
    file_ids = [row["file_id"] for row in file_rows]
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
    result["retrieval_params"] = {
        "top_k": body.top_k,
        "route_top_k": body.route_top_k,
        "candidates_per_type": body.candidates_per_type,
        "rrf_k": body.rrf_k,
        "similarity_threshold": body.similarity_threshold,
    }
    return result
