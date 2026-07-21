"""Versioned audit-assistant configuration APIs."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, jobs


router = APIRouter(prefix="/assistants", tags=["assistants"])


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class AssistantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = None


class AssistantVersionCreate(BaseModel):
    model_settings: dict[str, Any] = Field(alias="model_config")
    node_prompts: dict[str, Any]
    rules: dict[str, Any] = Field(default_factory=dict)
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    activate: bool = True


class KnowledgeBaseSelection(BaseModel):
    knowledge_base_ids: list[str]


class AssistantRunRequest(BaseModel):
    report_file_id: str = Field(min_length=1)
    naming_rule_file_id: str | None = None
    report_id: str = Field(default="HBJC", min_length=1, max_length=64)


def _loads(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _assistant_row(assistant_id: str):
    row = db.get_conn().execute(
        """SELECT a.*, v.version AS active_version
           FROM audit_assistants a
           LEFT JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.id=?""",
        (assistant_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "assistant not found")
    return row


def _assistant_out(row: Any) -> dict[str, Any]:
    kb_rows = db.get_conn().execute(
        """SELECT kb.id, kb.name
           FROM assistant_knowledge_bases akb
           JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
           WHERE akb.assistant_id=? AND akb.enabled=1
           ORDER BY akb.priority, kb.name""",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "active_version_id": row["active_version_id"],
        "active_version": row["active_version"],
        "knowledge_bases": [dict(item) for item in kb_rows],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _initial_version_template() -> Any:
    row = db.get_conn().execute(
        """SELECT model_config, node_prompts, rules, retrieval_config
           FROM assistant_versions
           WHERE id='assistant_oil_transformer_audit_v1'"""
    ).fetchone()
    if not row:
        raise RuntimeError("default assistant version template is missing")
    return row


@router.get("")
def list_assistants():
    rows = db.get_conn().execute(
        """SELECT a.*, v.version AS active_version
           FROM audit_assistants a
           LEFT JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.status!='archived'
           ORDER BY a.updated_at DESC, a.name"""
    ).fetchall()
    return [_assistant_out(row) for row in rows]


@router.post("")
def create_assistant(body: AssistantCreate):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    assistant_id = f"assistant_{uuid.uuid4().hex}"
    version_id = f"{assistant_id}_v1"
    template = _initial_version_template()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO audit_assistants
               (id,name,description,status,active_version_id,created_at,updated_at)
               VALUES (?,?,?,'active',NULL,?,?)""",
            (assistant_id, body.name.strip(), body.description.strip(), now, now),
        )
        conn.execute(
            """INSERT INTO assistant_versions
               (id,assistant_id,version,status,model_config,node_prompts,rules,
                retrieval_config,created_at,activated_at)
               VALUES (?,?,1,'active',?,?,?,?,?,?)""",
            (
                version_id,
                assistant_id,
                template["model_config"],
                template["node_prompts"],
                template["rules"],
                template["retrieval_config"],
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE audit_assistants SET active_version_id=? WHERE id=?",
            (version_id, assistant_id),
        )
    return _assistant_out(_assistant_row(assistant_id))


@router.get("/{assistant_id}")
def get_assistant(assistant_id: str):
    return _assistant_out(_assistant_row(assistant_id))


@router.patch("/{assistant_id}")
def update_assistant(assistant_id: str, body: AssistantUpdate):
    _assistant_row(assistant_id)
    updates: list[str] = []
    params: list[Any] = []
    for key in ("name", "description", "status"):
        value = getattr(body, key)
        if value is not None:
            updates.append(f"{key}=?")
            params.append(value.strip() if isinstance(value, str) else value)
    if updates:
        updates.append("updated_at=?")
        params.extend([time.strftime("%Y-%m-%dT%H:%M:%S"), assistant_id])
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE audit_assistants SET {', '.join(updates)} WHERE id=?",
                params,
            )
    return _assistant_out(_assistant_row(assistant_id))


@router.get("/{assistant_id}/versions")
def list_versions(assistant_id: str):
    _assistant_row(assistant_id)
    rows = db.get_conn().execute(
        """SELECT * FROM assistant_versions
           WHERE assistant_id=? ORDER BY version DESC""",
        (assistant_id,),
    ).fetchall()
    return [
        {
            **dict(row),
            "model_config": _loads(row["model_config"]),
            "node_prompts": _loads(row["node_prompts"]),
            "rules": _loads(row["rules"]),
            "retrieval_config": _loads(row["retrieval_config"]),
        }
        for row in rows
    ]


@router.get("/{assistant_id}/versions/active")
def get_active_version(assistant_id: str):
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(404, "assistant has no active version")
    row = db.get_conn().execute(
        "SELECT * FROM assistant_versions WHERE id=?",
        (assistant["active_version_id"],),
    ).fetchone()
    return {
        **dict(row),
        "model_config": _loads(row["model_config"]),
        "node_prompts": _loads(row["node_prompts"]),
        "rules": _loads(row["rules"]),
        "retrieval_config": _loads(row["retrieval_config"]),
    }


@router.post("/{assistant_id}/versions")
def create_version(assistant_id: str, body: AssistantVersionCreate):
    _assistant_row(assistant_id)
    provider = str(body.model_settings.get("provider") or "").lower()
    if provider != "deepseek":
        raise HTTPException(422, "only DeepSeek assistant versions are supported")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM assistant_versions WHERE assistant_id=?",
            (assistant_id,),
        ).fetchone()
        version = int(row["next_version"])
        version_id = f"{assistant_id}_v{version}_{uuid.uuid4().hex[:8]}"
        if body.activate:
            conn.execute(
                "UPDATE assistant_versions SET status='retired' WHERE assistant_id=? AND status='active'",
                (assistant_id,),
            )
        conn.execute(
            """INSERT INTO assistant_versions
               (id,assistant_id,version,status,model_config,node_prompts,rules,
                retrieval_config,created_at,activated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                version_id,
                assistant_id,
                version,
                "active" if body.activate else "draft",
                json.dumps(body.model_settings, ensure_ascii=False),
                json.dumps(body.node_prompts, ensure_ascii=False),
                json.dumps(body.rules, ensure_ascii=False),
                json.dumps(body.retrieval_config, ensure_ascii=False),
                now,
                now if body.activate else None,
            ),
        )
        if body.activate:
            conn.execute(
                """UPDATE audit_assistants
                   SET active_version_id=?, status='active', updated_at=?
                   WHERE id=?""",
                (version_id, now, assistant_id),
            )
    return get_active_version(assistant_id) if body.activate else {"id": version_id, "version": version}


@router.put("/{assistant_id}/knowledge-bases")
def set_knowledge_bases(assistant_id: str, body: KnowledgeBaseSelection):
    _assistant_row(assistant_id)
    unique_ids = list(dict.fromkeys(body.knowledge_base_ids))
    if unique_ids:
        placeholders = ",".join("?" for _ in unique_ids)
        count = db.get_conn().execute(
            f"SELECT COUNT(*) AS n FROM knowledge_bases WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchone()["n"]
        if count != len(unique_ids):
            raise HTTPException(422, "one or more knowledge bases do not exist")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM assistant_knowledge_bases WHERE assistant_id=?",
            (assistant_id,),
        )
        conn.executemany(
            """INSERT INTO assistant_knowledge_bases
               (assistant_id,knowledge_base_id,priority,enabled)
               VALUES (?,?,?,1)""",
            [(assistant_id, kb_id, index) for index, kb_id in enumerate(unique_ids)],
        )
    return _assistant_out(_assistant_row(assistant_id))


@router.post("/{assistant_id}/runs")
def start_assistant_run(assistant_id: str, body: AssistantRunRequest):
    _assistant_row(assistant_id)
    try:
        job = jobs.enqueue_assistant_audit(
            assistant_id,
            report_file_id=body.report_file_id,
            naming_rule_file_id=body.naming_rule_file_id,
            report_id=body.report_id,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return job
