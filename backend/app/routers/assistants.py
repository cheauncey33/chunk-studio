"""Audit-assistant configuration APIs (single active config per assistant)."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import (
    chat_agent,
    chat_sessions,
    config,
    current_user,
    db,
    jobs,
    llm,
    observability,
    retrieval,
    runtime,
)
from ..storage.repositories import get_content_repository, get_content_write_repository


router = APIRouter(prefix="/assistants", tags=["assistants"])
logger = logging.getLogger(__name__)

_CHAT_SYSTEM = """你是知识库问答助手。只能依据下方「检索证据」回答用户问题。
要求：
1. 证据充足时，用简洁中文直接回答，可引用标准号、条款或表号。
2. 证据不足或不相关时，明确说「知识库中未找到足够依据」，不要编造。
3. 不要输出审查判定流程，不要假装在做合规审查工作流。"""


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    knowledge_base_id: str | None = None


class AssistantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = None


class AssistantVersionConfigUpdate(BaseModel):
    """Overwrite the single active assistant config row."""

    model_settings: dict[str, Any] = Field(alias="model_config")
    node_prompts: dict[str, Any]
    rules: dict[str, Any] = Field(default_factory=dict)
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    # Accepted for API compatibility; always persisted as {}.
    category_profile: dict[str, Any] = Field(default_factory=dict)
    initialization_provenance: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseSelection(BaseModel):
    knowledge_base_ids: list[str]


class AssistantRunRequest(BaseModel):
    report_file_id: str = Field(min_length=1)
    naming_rule_file_id: str | None = None


class AssistantRouteRequest(BaseModel):
    report_file_id: str = Field(min_length=1)
    model: str | None = None


class AssistantRouteAndRunRequest(BaseModel):
    report_file_id: str = Field(min_length=1)
    naming_rule_file_id: str | None = None
    model: str | None = None


class AssistantInitRequest(BaseModel):
    sample_report_file_ids: list[str] = Field(default_factory=list, max_length=3)
    model: str | None = None


class AssistantInitDraftUpdate(BaseModel):
    parameter_schema: dict[str, Any] | None = None
    report_parameters_prompt: str | None = None


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _version_name(row: Any) -> str:
    try:
        return str(row["name"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _version_out(row: Any) -> dict[str, Any]:
    from ..parameter_schema import resolve_parameter_schema
    from ..query_planner_routes import (
        looks_like_full_query_planner_prompt,
        resolve_query_planner_routes,
    )

    retrieval_config = retrieval.normalize_retrieval_config(
        _loads(row["retrieval_config"])
    )
    retrieval_config.setdefault("aggregate_continuation_tables", False)
    retrieval_config.setdefault("expand_references", False)
    retrieval_config["query_planner_routes"] = resolve_query_planner_routes(
        retrieval_config.get("query_planner_routes"),
    )
    from ..audit_judge_notes import looks_like_full_audit_judge_prompt

    node_prompts = _loads(row["node_prompts"])
    if isinstance(node_prompts, dict):
        planner = node_prompts.get("query_planner")
        if isinstance(planner, dict) and looks_like_full_query_planner_prompt(
            str(planner.get("content") or "")
        ):
            # Do not surface nested full Planner as editable "品类约束".
            node_prompts = {
                **node_prompts,
                "query_planner": {**planner, "content": ""},
            }
        judge = node_prompts.get("audit_judge")
        if isinstance(judge, dict) and looks_like_full_audit_judge_prompt(
            str(judge.get("content") or "")
        ):
            node_prompts = {
                **node_prompts,
                "audit_judge": {**judge, "content": ""},
            }
    version_no = int(row["version"])
    name = _version_name(row)
    return {
        **dict(row),
        "name": name,
        "label": name or f"v{version_no}",
        "model_config": _loads(row["model_config"]),
        "node_prompts": node_prompts,
        "rules": _loads(row["rules"]),
        "retrieval_config": retrieval_config,
        "parameter_schema": resolve_parameter_schema(_loads(row["parameter_schema"])),
        "category_profile": _loads(row["category_profile"]),
        "initialization_provenance": _loads(row["initialization_provenance"]),
    }


def _assistant_row(assistant_id: str):
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        row = repository.get_assistant(assistant_id)
        if not row:
            raise HTTPException(404, "assistant not found")
        return row
    row = db.get_conn().execute(
        """SELECT a.*, v.version AS active_version
           FROM audit_assistants a
           LEFT JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.id=? AND a.workspace_id=?""",
        (assistant_id, current_user.get_current_user().workspace_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "assistant not found")
    return row


def _assistant_out(row: Any) -> dict[str, Any]:
    repository = get_content_repository() or get_content_write_repository()
    kb_rows = (
        repository.assistant_bound_knowledge_bases(row["id"])
        if repository is not None
        else db.get_conn().execute(
        """SELECT kb.id, kb.name
           FROM assistant_knowledge_bases akb
           JOIN knowledge_bases kb ON kb.id=akb.knowledge_base_id
           WHERE akb.assistant_id=?
             AND akb.workspace_id=? AND kb.workspace_id=? AND akb.enabled=1
           ORDER BY akb.priority, kb.name""",
        (row["id"], current_user.get_current_user().workspace_id,
         current_user.get_current_user().workspace_id),
        ).fetchall()
    )
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
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        row = repository.get_assistant_template()
        if not row:
            raise RuntimeError("generic assistant version template is missing")
        return row
    row = db.get_conn().execute(
        """SELECT model_config, node_prompts, rules, retrieval_config, parameter_schema
           FROM assistant_versions
           WHERE id='assistant_audit_template_v1'"""
    ).fetchone()
    if not row:
        raise RuntimeError("generic assistant version template is missing")
    return row


@router.get("")
def list_assistants():
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        return [_assistant_out(row) for row in repository.list_assistants()]
    rows = db.get_conn().execute(
        """SELECT a.*, v.version AS active_version
           FROM audit_assistants a
           LEFT JOIN assistant_versions v ON v.id=a.active_version_id
           WHERE a.status!='archived' AND a.workspace_id=?
           ORDER BY a.updated_at DESC, a.name""",
        (current_user.get_current_user().workspace_id,),
    ).fetchall()
    return [_assistant_out(row) for row in rows]


@router.post("")
def create_assistant(body: AssistantCreate):
    from ..parameter_schema import resolve_parameter_schema

    repository = get_content_write_repository()
    if body.knowledge_base_id:
        try:
            if repository is not None:
                created = repository.ensure_assistant_for_knowledge_base(
                    body.knowledge_base_id.strip(),
                    name=body.name.strip(),
                    description=body.description.strip(),
                )
            else:
                created = db.ensure_assistant_for_knowledge_base(
                    body.knowledge_base_id.strip(),
                    name=body.name.strip(),
                    description=body.description.strip(),
                )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        # If ensure returned an existing bind, optionally rename when caller provided a name.
        existing = _assistant_out(_assistant_row(created["id"]))
        return existing

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    assistant_id = f"assistant_{uuid.uuid4().hex}"
    version_id = f"{assistant_id}_v1"
    template = _initial_version_template()
    parameter_schema = resolve_parameter_schema(_loads(template["parameter_schema"]))
    if repository is not None:
        try:
            created = repository.create_assistant(
                assistant_id=assistant_id,
                name=body.name.strip(),
                description=body.description.strip(),
                template=dict(template),
                parameter_schema=parameter_schema,
                created_at=now,
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(409, "assistant name already exists") from exc
            raise HTTPException(500, f"assistant creation failed: {exc}") from exc
        return _assistant_out(created)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO audit_assistants
               (id,workspace_id,name,description,status,active_version_id,created_at,updated_at)
               VALUES (?,?,?,?,'active',NULL,?,?)""",
            (
                assistant_id,
                current_user.get_current_user().workspace_id,
                body.name.strip(),
                body.description.strip(),
                now,
                now,
            ),
        )
        snapshot_provenance = json.dumps(
            {
                "source": "template_snapshot",
                "template_version_id": "assistant_audit_template_v1",
                "copied_at": now,
            },
            ensure_ascii=False,
        )
        conn.execute(
            """INSERT INTO assistant_versions
               (id,assistant_id,version,name,status,model_config,node_prompts,rules,
                retrieval_config,parameter_schema,category_profile,
                initialization_provenance,created_at,activated_at)
               VALUES (?,?,1,'','active',?,?,?,?,?, '{}',?,?,?)""",
            (
                version_id,
                assistant_id,
                template["model_config"],
                template["node_prompts"],
                template["rules"],
                template["retrieval_config"],
                json.dumps(parameter_schema, ensure_ascii=False),
                snapshot_provenance,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE audit_assistants SET active_version_id=? WHERE id=?",
            (version_id, assistant_id),
        )
    return _assistant_out(_assistant_row(assistant_id))


@router.post("/route")
def route_assistant(body: AssistantRouteRequest):
    from .. import assistant_router

    try:
        return assistant_router.route_report_to_assistant(
            report_file_id=body.report_file_id,
            model=body.model,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/route-and-run")
def route_and_run_assistant(body: AssistantRouteAndRunRequest):
    from .. import assistant_router

    try:
        route = assistant_router.route_report_to_assistant(
            report_file_id=body.report_file_id,
            model=body.model,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        job = jobs.enqueue_assistant_audit(
            route["assistant_id"],
            report_file_id=body.report_file_id,
            naming_rule_file_id=body.naming_rule_file_id,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"route": route, "job": job}


@router.get("/{assistant_id}")
def get_assistant(assistant_id: str):
    return _assistant_out(_assistant_row(assistant_id))


@router.patch("/{assistant_id}")
def update_assistant(assistant_id: str, body: AssistantUpdate):
    _assistant_row(assistant_id)
    repository = get_content_write_repository()
    if repository is not None:
        values: dict[str, Any] = {}
        for key in ("name", "description", "status"):
            value = getattr(body, key)
            if value is not None:
                values[key] = value.strip() if isinstance(value, str) else value
        if values:
            repository.update_assistant(assistant_id, values)
        return _assistant_out(_assistant_row(assistant_id))
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
                f"UPDATE audit_assistants SET {', '.join(updates)} WHERE id=? AND workspace_id=?",
                [*params, current_user.get_current_user().workspace_id],
            )
    return _assistant_out(_assistant_row(assistant_id))


@router.get("/{assistant_id}/versions/active")
def get_active_version(assistant_id: str):
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(404, "assistant has no active version")
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        row = repository.get_active_assistant_version(assistant_id)
        if not row:
            raise HTTPException(404, "assistant has no active version")
        return _version_out(row)
    row = db.get_conn().execute(
        "SELECT * FROM assistant_versions WHERE id=?",
        (assistant["active_version_id"],),
    ).fetchone()
    return _version_out(row)


@router.put("/{assistant_id}/versions/active")
def update_active_version(assistant_id: str, body: AssistantVersionConfigUpdate):
    """Overwrite the single active config row (no new version insert)."""
    from ..parameter_schema import resolve_parameter_schema

    assistant = _assistant_row(assistant_id)
    provider = str(body.model_settings.get("provider") or "").lower()
    if provider != "deepseek":
        raise HTTPException(422, "only DeepSeek assistant versions are supported")
    parameter_schema = resolve_parameter_schema(body.parameter_schema)
    normalized_retrieval_config = retrieval.normalize_retrieval_config(
        body.retrieval_config
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    provenance = body.initialization_provenance
    if not isinstance(provenance, dict):
        provenance = {}
    source = str(provenance.get("source") or "").strip()
    # Saving from the editor clears "unspecialized template copy" markers so the
    # KB workflow UI stops nudging category init.
    if source in {"", "built_in_seed", "template_snapshot"}:
        provenance = {"source": "manual_config", "saved_at": now}
    repository = get_content_write_repository()
    if repository is not None:
        row = repository.update_active_assistant_version(
            assistant_id,
            model_config=body.model_settings,
            node_prompts=body.node_prompts,
            rules=body.rules,
            retrieval_config=normalized_retrieval_config,
            parameter_schema=parameter_schema,
            initialization_provenance=provenance,
            updated_at=now,
        )
        return _version_out(row)
    with db.transaction() as conn:
        version_id = assistant["active_version_id"]
        if version_id:
            row = conn.execute(
                "SELECT id FROM assistant_versions WHERE id=? AND assistant_id=?",
                (version_id, assistant_id),
            ).fetchone()
            if not row:
                version_id = None
        if not version_id:
            latest = conn.execute(
                """SELECT id FROM assistant_versions
                   WHERE assistant_id=? ORDER BY version DESC LIMIT 1""",
                (assistant_id,),
            ).fetchone()
            version_id = latest["id"] if latest else None
        if version_id:
            conn.execute(
                """UPDATE assistant_versions
                   SET status='active',
                       model_config=?, node_prompts=?, rules=?,
                       retrieval_config=?, parameter_schema=?,
                       category_profile='{}',
                       initialization_provenance=?,
                       activated_at=COALESCE(activated_at, ?)
                   WHERE id=? AND assistant_id=?""",
                (
                    json.dumps(body.model_settings, ensure_ascii=False),
                    json.dumps(body.node_prompts, ensure_ascii=False),
                    json.dumps(body.rules, ensure_ascii=False),
                    json.dumps(normalized_retrieval_config, ensure_ascii=False),
                    json.dumps(parameter_schema, ensure_ascii=False),
                    json.dumps(provenance, ensure_ascii=False),
                    now,
                    version_id,
                    assistant_id,
                ),
            )
            conn.execute(
                "DELETE FROM assistant_versions WHERE assistant_id=? AND id!=?",
                (assistant_id, version_id),
            )
        else:
            version_id = f"{assistant_id}_v1_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT INTO assistant_versions
                   (id,assistant_id,version,name,status,model_config,node_prompts,rules,
                    retrieval_config,parameter_schema,category_profile,
                    initialization_provenance,created_at,activated_at)
                   VALUES (?,?,1,'','active',?,?,?,?,?,'{}',?,?,?)""",
                (
                    version_id,
                    assistant_id,
                    json.dumps(body.model_settings, ensure_ascii=False),
                    json.dumps(body.node_prompts, ensure_ascii=False),
                    json.dumps(body.rules, ensure_ascii=False),
                    json.dumps(normalized_retrieval_config, ensure_ascii=False),
                    json.dumps(parameter_schema, ensure_ascii=False),
                    json.dumps(provenance, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        conn.execute(
            """UPDATE audit_assistants
               SET active_version_id=?, status='active', updated_at=?
               WHERE id=?""",
            (version_id, now, assistant_id),
        )
    return get_active_version(assistant_id)


@router.put("/{assistant_id}/knowledge-bases")
def set_knowledge_bases(assistant_id: str, body: KnowledgeBaseSelection):
    """Bind exactly one knowledge base (1:1). Empty list unbinds."""
    if assistant_id == "assistant_audit_template":
        raise HTTPException(400, "通用审查模板不绑定知识库")
    _assistant_row(assistant_id)
    unique_ids = list(dict.fromkeys(body.knowledge_base_ids))
    if len(unique_ids) > 1:
        raise HTTPException(422, "每个助手只能绑定一个知识库")
    repository = get_content_write_repository()
    if repository is not None:
        try:
            repository.set_assistant_knowledge_bases(assistant_id, unique_ids)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            status = 409 if "already bound" in message else 422
            raise HTTPException(status, message) from exc
        return _assistant_out(_assistant_row(assistant_id))
    if unique_ids:
        kb_id = unique_ids[0]
        row = db.get_conn().execute(
            "SELECT id FROM knowledge_bases WHERE id=? AND workspace_id=?",
            (kb_id, current_user.get_current_user().workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(422, "knowledge base does not exist")
        conflict = db.get_conn().execute(
            """SELECT assistant_id FROM assistant_knowledge_bases
               WHERE knowledge_base_id=? AND workspace_id=? AND enabled=1 AND assistant_id!=?""",
            (kb_id, current_user.get_current_user().workspace_id, assistant_id),
        ).fetchone()
        if conflict:
            raise HTTPException(409, "该知识库已绑定其他助手")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM assistant_knowledge_bases WHERE assistant_id=? AND workspace_id=?",
            (assistant_id, current_user.get_current_user().workspace_id),
        )
        if unique_ids:
            kb_id = unique_ids[0]
            # Ensure the KB has no stale binds (disabled rows included).
            conn.execute(
                "DELETE FROM assistant_knowledge_bases WHERE knowledge_base_id=? AND workspace_id=?",
                (kb_id, current_user.get_current_user().workspace_id),
            )
            conn.execute(
                """INSERT INTO assistant_knowledge_bases
                   (assistant_id,knowledge_base_id,workspace_id,priority,enabled)
                   VALUES (?,?,?,0,1)""",
                (assistant_id, kb_id, current_user.get_current_user().workspace_id),
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
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return job


@router.post("/{assistant_id}/init")
def start_assistant_init(assistant_id: str, body: AssistantInitRequest):
    _assistant_row(assistant_id)
    try:
        job = jobs.enqueue_assistant_init(
            assistant_id,
            sample_report_file_ids=body.sample_report_file_ids,
            model=body.model,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from .. import assistant_init

    return {
        "job": job,
        "draft": assistant_init.get_init_draft(assistant_id),
    }


@router.get("/{assistant_id}/init-draft")
def get_assistant_init_draft(assistant_id: str):
    _assistant_row(assistant_id)
    from .. import assistant_init

    draft = assistant_init.get_init_draft(assistant_id)
    if not draft:
        raise HTTPException(404, "init draft not found")
    return draft


@router.put("/{assistant_id}/init-draft")
def update_assistant_init_draft(assistant_id: str, body: AssistantInitDraftUpdate):
    _assistant_row(assistant_id)
    from .. import assistant_init

    patch: dict[str, Any] = {}
    if body.parameter_schema is not None:
        patch["parameter_schema"] = body.parameter_schema
    if body.report_parameters_prompt is not None:
        patch["report_parameters_prompt"] = body.report_parameters_prompt
    if not patch:
        raise HTTPException(422, "no draft fields to update")
    try:
        return assistant_init.update_init_draft_payload(assistant_id, patch)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{assistant_id}/init-draft/apply")
def apply_assistant_init_draft(assistant_id: str):
    _assistant_row(assistant_id)
    from .. import assistant_init

    try:
        applied = assistant_init.apply_init_draft(assistant_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **applied,
        "assistant": _assistant_out(_assistant_row(assistant_id)),
        "active_version": get_active_version(assistant_id),
    }


@router.post("/{assistant_id}/init-draft/discard")
def discard_assistant_init_draft(assistant_id: str):
    _assistant_row(assistant_id)
    from .. import assistant_init

    draft = assistant_init.get_init_draft(assistant_id)
    if not draft:
        raise HTTPException(404, "init draft not found")
    if draft["status"] == "generating":
        raise HTTPException(400, "draft is still generating")
    return assistant_init.upsert_init_draft(
        assistant_id,
        status="discarded",
        payload=draft.get("payload") or {},
    )


def _format_evidence(hits: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        name = str(hit.get("file_name") or hit.get("doc_id") or "未知文件")
        page = hit.get("page")
        page_label = f" p.{page}" if page not in (None, "") else ""
        text = str(hit.get("text") or hit.get("content") or "").strip()
        if len(text) > 1200:
            text = text[:1200] + "…"
        blocks.append(f"[{index}] {name}{page_label}\n{text}")
    return "\n\n".join(blocks) if blocks else "（无检索命中）"


def _hit_score(hit: dict[str, Any]) -> float:
    value = hit.get("rerank_score")
    if value is None:
        value = hit.get("score")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _citations_from_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One citation per file+page (keep highest-scoring chunk)."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for hit in hits:
        file_key = str(hit.get("file_id") or hit.get("doc_id") or hit.get("file_name") or "")
        page_key = "" if hit.get("page") in (None, "") else str(hit.get("page"))
        key = (file_key, page_key)
        citation = {
            "chunk_id": hit.get("chunk_id"),
            "file_id": hit.get("file_id") or hit.get("doc_id"),
            "file_name": hit.get("file_name") or hit.get("doc_id"),
            "page": hit.get("page"),
            "score": hit.get("rerank_score") if hit.get("rerank_score") is not None else hit.get("score"),
            "snippet": str(hit.get("text") or hit.get("content") or "")[:240],
        }
        current = best.get(key)
        if current is None:
            best[key] = citation
            order.append(key)
            continue
        if _hit_score(hit) > _hit_score(current):
            best[key] = citation
    return [best[key] for key in order]


@router.post("/{assistant_id}/chat")
def assistant_chat(assistant_id: str, body: AssistantChatRequest):
    """Simple RAG Q&A — not the audit workflow."""
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(400, "assistant has no active version")
    repository = get_content_repository() or get_content_write_repository()
    version = (
        repository.get_active_assistant_version(assistant_id)
        if repository is not None
        else db.get_conn().execute(
            "SELECT * FROM assistant_versions WHERE id=?",
            (assistant["active_version_id"],),
        ).fetchone()
    )
    if not version:
        raise HTTPException(404, "active version not found")

    model_config = _loads(version["model_config"])
    retrieval_config = retrieval.normalize_retrieval_config(
        _loads(version["retrieval_config"])
    )
    file_ids = (
        repository.assistant_scoped_file_ids(assistant_id)
        if repository is not None
        else db.assistant_scoped_file_ids(assistant_id)
    )
    if not file_ids:
        raise HTTPException(400, "请先绑定知识库，并确保库内有已启用的文件")

    top_k = int(retrieval_config.get("top_k") or 10)
    route_top_k = int(retrieval_config.get("route_top_k") or 30)
    candidates_per_type = int(retrieval_config.get("candidate_count_per_type") or 20)
    dense_threshold = float(retrieval_config.get("dense_threshold") or 0.0)
    rerank_threshold = float(
        retrieval_config.get("rerank_threshold")
        if retrieval_config.get("rerank_threshold") is not None
        else retrieval.DEFAULT_RERANK_THRESHOLD
    )
    aggregate_continuation_tables = bool(
        retrieval_config.get("aggregate_continuation_tables", False)
    )
    expand_references = bool(retrieval_config.get("expand_references", False))

    try:
        search = retrieval.hybrid_search(
            body.message.strip(),
            top_k=max(1, min(top_k, 50)),
            route_top_k=max(1, min(route_top_k, 100)),
            candidates_per_type=max(1, min(candidates_per_type, 100)),
            dense_threshold=dense_threshold,
            rerank_threshold=rerank_threshold,
            aggregate_continuation_tables=aggregate_continuation_tables,
            expand_references=expand_references,
            file_ids=file_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    hits = list(search.get("hits") or [])
    evidence = _format_evidence(hits)
    model = str(model_config.get("model") or llm.DEFAULT_MODEL)
    temperature = float(model_config.get("temperature") or 0)

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": f"{_CHAT_SYSTEM}\n\n【检索证据】\n{evidence}",
        },
    ]
    for item in body.history[-12:]:
        messages.append({"role": item.role, "content": item.content.strip()})
    messages.append({"role": "user", "content": body.message.strip()})

    try:
        answer = llm.chat_text(messages, model=model, temperature=temperature)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    citations = _citations_from_hits(hits)

    return {
        "answer": answer,
        "citations": citations,
        "model": model,
        "retrieval": {
            "hit_count": len(hits),
            "scoped_file_count": len(file_ids),
            "top_k": top_k,
            "dense_threshold": dense_threshold,
            "rerank_threshold": rerank_threshold,
            "aggregate_continuation_tables": aggregate_continuation_tables,
            "expand_references": expand_references,
            "degraded": search.get("degraded") or [],
        },
    }


@router.get("/{assistant_id}/conversations")
def list_agent_conversations(
    assistant_id: str,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    _assistant_row(assistant_id)
    identity = _request_current_user(request)
    return {"items": chat_sessions.list_conversations(
        assistant_id,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
    )}


@router.get("/{assistant_id}/conversations/{conversation_id}")
def get_agent_conversation(
    assistant_id: str,
    conversation_id: str,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    _assistant_row(assistant_id)
    identity = _request_current_user(request)
    conversation = chat_sessions.get_conversation(
        conversation_id,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
    )
    if not conversation or conversation["assistant_id"] != assistant_id:
        raise HTTPException(404, "conversation not found")
    return {
        **conversation,
        "events": chat_sessions.list_events(conversation_id),
    }


def _request_current_user(request: Request | None) -> current_user.CurrentUser:
    if request is None:
        return current_user.local_current_user()
    try:
        return current_user.current_user_for_headers(request.headers)
    except current_user.CurrentUserError as exc:
        raise HTTPException(401, str(exc)) from exc


def _prepare_agent_context(
    assistant_id: str,
    body: AgentChatRequest,
    *,
    identity: current_user.CurrentUser | None = None,
) -> dict[str, Any]:
    identity = identity or current_user.local_current_user()
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(400, "assistant has no active version")
    repository = get_content_repository() or get_content_write_repository()
    version = (
        repository.get_active_assistant_version(assistant_id)
        if repository is not None
        else db.get_conn().execute(
            "SELECT * FROM assistant_versions WHERE id=?",
            (assistant["active_version_id"],),
        ).fetchone()
    )
    if not version:
        raise HTTPException(404, "active version not found")

    # Business-only Agent turns are valid without enabled KB files. In that
    # case chat_agent omits the document-search tool and explains the limit.
    current_file_ids = (
        repository.assistant_scoped_file_ids(assistant_id)
        if repository is not None
        else db.assistant_scoped_file_ids(assistant_id)
    )
    current_model_config = _loads(version["model_config"])
    current_retrieval_config = retrieval.normalize_retrieval_config(
        _loads(version["retrieval_config"])
    )
    current_snapshot = {
        "assistant_version_id": str(version["id"]),
        "workspace_id": identity.workspace_id,
        "user_id": identity.user_id,
        "model_config": current_model_config,
        "retrieval_config": current_retrieval_config,
        # Freeze the corpus membership together with the tuning parameters so
        # a later KB edit cannot silently change an existing conversation.
        "file_ids": current_file_ids,
    }

    conversation = (
        chat_sessions.get_conversation(
            body.conversation_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
        )
        if body.conversation_id
        else None
    )
    if body.conversation_id and (
        not conversation or conversation["assistant_id"] != assistant_id
    ):
        raise HTTPException(404, "conversation not found")
    if conversation is None:
        conversation = chat_sessions.create_conversation(
            assistant_id,
            title=body.message.strip()[:120],
            config_snapshot=current_snapshot,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
        )
    else:
        stored_snapshot = _loads(conversation.get("config_snapshot"))
        if stored_snapshot:
            current_snapshot = stored_snapshot
        else:
            # Conversations created before snapshot support keep their
            # original behavior on first reuse, then become reproducible.
            chat_sessions.set_config_snapshot_if_empty(
                str(conversation["id"]),
                current_snapshot,
            )
    conversation_id = str(conversation["id"])

    snapshot_model_config = current_snapshot.get("model_config")
    model_config = (
        snapshot_model_config
        if isinstance(snapshot_model_config, dict)
        else current_model_config
    )
    snapshot_retrieval_config = current_snapshot.get("retrieval_config")
    retrieval_config = retrieval.normalize_retrieval_config(
        snapshot_retrieval_config
        if isinstance(snapshot_retrieval_config, dict)
        else current_retrieval_config
    )
    snapshot_file_ids = current_snapshot.get("file_ids")
    file_ids = (
        [str(file_id) for file_id in snapshot_file_ids]
        if isinstance(snapshot_file_ids, list)
        else current_file_ids
    )

    chat_sessions.append_events(
        conversation_id,
        [("user_message", {"content": body.message.strip()})],
    )
    model = str(model_config.get("model") or llm.DEFAULT_MODEL)
    temperature = float(model_config.get("temperature") or 0)
    chat_sessions.compact_conversation(
        conversation_id,
        summarize=lambda existing, messages: chat_agent.summarize_context(
            existing,
            messages,
            model=model,
        ),
    )
    return {
        "conversation_id": conversation_id,
        "workspace_id": identity.workspace_id,
        "user_id": identity.user_id,
        "file_ids": file_ids,
        "retrieval_config": retrieval_config,
        "config_snapshot": current_snapshot,
        "model": model,
        "temperature": temperature,
    }


def _persist_agent_result(conversation_id: str, result: dict[str, Any]) -> None:
    persisted_events: list[tuple[str, dict[str, Any]]] = []
    for message in result.get("new_messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            stored_message = dict(message)
            if not stored_message.get("tool_calls"):
                stored_message.pop("tool_calls", None)
            payload: dict[str, Any] = {"message": stored_message}
            if not message.get("tool_calls"):
                payload["citations"] = result.get("citations") or []
                payload["charts"] = result.get("charts") or []
            persisted_events.append(("assistant_message", payload))
        elif message.get("role") == "tool":
            persisted_events.append((
                "tool_result",
                {
                    "tool_call_id": message.get("tool_call_id"),
                    "name": message.get("name"),
                    "content": message.get("content"),
                },
            ))
    chat_sessions.append_events(conversation_id, persisted_events)


def _agent_response(conversation_id: str, model: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "answer": result["answer"],
        "citations": result.get("citations") or [],
        "charts": result.get("charts") or [],
        "model": model,
        "stop_reason": result.get("stop_reason"),
        "turns": result.get("turns", 0),
        "tool_calls": result.get("tool_calls", 0),
        "performance": result.get("performance") or {},
    }


def _runtime_scope(identity: current_user.CurrentUser, assistant_id: str) -> str:
    return f"{identity.workspace_id}:{identity.user_id}:{assistant_id}"


def _stream_name(identity: current_user.CurrentUser, conversation_id: str) -> str:
    return f"{identity.workspace_id}:{identity.user_id}:{conversation_id}"


def _claim_idempotency(
    request: Request | None,
    identity: current_user.CurrentUser,
    assistant_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Reserve an optional request key and return a completed replay if present."""
    if request is None:
        return None, None
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return None, None
    if len(idempotency_key) > 200:
        raise HTTPException(400, "Idempotency-Key is too long")
    scope = _runtime_scope(identity, assistant_id)
    services = runtime.get_runtime()
    if services.idempotency.reserve(scope, idempotency_key):
        return idempotency_key, None
    replay = services.idempotency.get_response(scope, idempotency_key)
    if replay is not None:
        return idempotency_key, replay
    raise HTTPException(409, "request with this Idempotency-Key is still processing")


def _release_idempotency(
    identity: current_user.CurrentUser,
    assistant_id: str,
    idempotency_key: str | None,
) -> None:
    if idempotency_key:
        runtime.get_runtime().idempotency.delete(
            _runtime_scope(identity, assistant_id),
            idempotency_key,
        )


def _conversation_lock(
    identity: current_user.CurrentUser,
    assistant_id: str,
    conversation_id: str | None,
) -> runtime.ConversationLock:
    # Existing conversations must serialize writes. New conversations do not
    # share state and must not all contend on one global "new" lock.
    suffix = conversation_id or f"new:{uuid.uuid4().hex}"
    return runtime.get_runtime().conversation_lock(
        f"{identity.workspace_id}:{identity.user_id}:{assistant_id}:{suffix}",
        blocking_timeout=0,
    )


def _admit_agent(
    identity: current_user.CurrentUser,
    assistant_id: str,
) -> tuple[runtime.RuntimeServices, str]:
    services = runtime.get_runtime()
    user_key = f"{identity.workspace_id}:{identity.user_id}"
    allowed, _count = services.rate_limits.allow(
        user_key,
        limit=config.USER_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(429, "user request rate limit exceeded")
    agent_key = f"{user_key}:{assistant_id}"
    if not services.agent_concurrency.acquire(
        agent_key,
        limit=config.AGENT_CONCURRENCY_LIMIT,
        lease_seconds=config.AGENT_LEASE_SECONDS,
    ):
        raise HTTPException(429, "agent concurrency limit exceeded")
    return services, agent_key


@router.post("/{assistant_id}/agent-chat")
def assistant_agent_chat(
    assistant_id: str,
    body: AgentChatRequest,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Persistent Agent chat using native model tool calls."""
    identity = _request_current_user(request)
    idempotency_key, replay = _claim_idempotency(request, identity, assistant_id)
    if replay is not None:
        return replay
    services, agent_key = _admit_agent(identity, assistant_id)
    lock = _conversation_lock(identity, assistant_id, body.conversation_id)
    try:
        with lock:
            context = _prepare_agent_context(
                assistant_id,
                body,
                identity=identity,
            )
            try:
                result = chat_agent.run_chat_agent(
                    assistant_id=assistant_id,
                    messages=chat_sessions.load_messages(context["conversation_id"]),
                    file_ids=context["file_ids"],
                    retrieval_config=context["retrieval_config"],
                    model=context["model"],
                    temperature=context["temperature"],
                    workspace_id=context["workspace_id"],
                )
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
            _persist_agent_result(context["conversation_id"], result)
            response = _agent_response(context["conversation_id"], context["model"], result)
    except runtime.ConversationBusy as exc:
        services.agent_concurrency.release(agent_key)
        _release_idempotency(identity, assistant_id, idempotency_key)
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        services.agent_concurrency.release(agent_key)
        _release_idempotency(identity, assistant_id, idempotency_key)
        raise
    services.agent_concurrency.release(agent_key)
    if idempotency_key:
        runtime.get_runtime().idempotency.save_response(
            _runtime_scope(identity, assistant_id),
            idempotency_key,
            response,
        )
    return response


def _sse(event: str, payload: dict[str, Any], event_id: str | None = None) -> str:
    event_id_line = f"id: {event_id}\n" if event_id else ""
    return (
        event_id_line +
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.get("/{assistant_id}/conversations/{conversation_id}/events/stream")
def replay_agent_events(
    assistant_id: str,
    conversation_id: str,
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """Replay durable SSE events after a client reconnects."""
    _assistant_row(assistant_id)
    identity = _request_current_user(request)
    conversation = chat_sessions.get_conversation(
        conversation_id,
        workspace_id=identity.workspace_id,
        user_id=identity.user_id,
    )
    if not conversation or conversation["assistant_id"] != assistant_id:
        raise HTTPException(404, "conversation not found")
    last_event_id = (
        str(request.headers.get("Last-Event-ID") or "0-0")
        if request else "0-0"
    )
    items = runtime.get_runtime().events.read_since(
        _stream_name(identity, conversation_id),
        last_event_id,
    )

    def body_iter():
        for item in items:
            event = item.get("payload") or {}
            event_name = str(event.get("event") or "agent")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            yield _sse(event_name, payload, str(item["id"]))
        yield _sse("done", {})

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{assistant_id}/agent-chat/stream")
def assistant_agent_chat_stream(
    assistant_id: str,
    body: AgentChatRequest,
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """Stream Agent lifecycle events while keeping the final result durable."""
    identity = _request_current_user(request)
    idempotency_key, replay = _claim_idempotency(request, identity, assistant_id)
    if replay is not None:
        def replay_iter():
            yield _sse("replayed", replay)
            yield _sse("final", replay)
            yield _sse("done", {})
        return StreamingResponse(replay_iter(), media_type="text/event-stream")

    services, agent_key = _admit_agent(identity, assistant_id)
    lock = _conversation_lock(identity, assistant_id, body.conversation_id)
    try:
        if not lock.acquire():
            raise runtime.ConversationBusy("conversation is already being processed")
        context = _prepare_agent_context(
            assistant_id,
            body,
            identity=identity,
        )
    except runtime.ConversationBusy as exc:
        services.agent_concurrency.release(agent_key)
        _release_idempotency(identity, assistant_id, idempotency_key)
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        services.agent_concurrency.release(agent_key)
        lock.release()
        _release_idempotency(identity, assistant_id, idempotency_key)
        raise

    stream_name = _stream_name(identity, context["conversation_id"])
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def publish(kind: str, payload: dict[str, Any]) -> None:
        event_id = runtime.get_runtime().events.append(
            stream_name,
            {"event": kind, "payload": payload},
        )
        events.put({"kind": kind, "payload": payload, "id": event_id})

    publish("conversation", {"conversation_id": context["conversation_id"]})

    def worker() -> None:
        try:
            result = chat_agent.run_chat_agent(
                assistant_id=assistant_id,
                messages=chat_sessions.load_messages(context["conversation_id"]),
                file_ids=context["file_ids"],
                retrieval_config=context["retrieval_config"],
                model=context["model"],
                temperature=context["temperature"],
                workspace_id=context["workspace_id"],
                event_sink=lambda event: publish("agent", event),
                stream_tokens=True,
            )
            _persist_agent_result(context["conversation_id"], result)
            final_response = _agent_response(
                context["conversation_id"],
                context["model"],
                result,
            )
            if idempotency_key:
                runtime.get_runtime().idempotency.save_response(
                    _runtime_scope(identity, assistant_id),
                    idempotency_key,
                    final_response,
                )
            publish("final", final_response)
        except Exception as exc:
            observability.metrics.increment(
                "chunk_studio_agent_stream_failures_total",
                error_type=type(exc).__name__,
            )
            publish("error", {"error_type": type(exc).__name__, "error": str(exc)})
            _release_idempotency(identity, assistant_id, idempotency_key)
        finally:
            try:
                services.agent_concurrency.release(agent_key)
            except Exception as exc:
                logger.exception("failed to release Agent concurrency lease")
                observability.metrics.increment(
                    "chunk_studio_agent_cleanup_failures_total",
                    resource="concurrency_lease",
                    error_type=type(exc).__name__,
                )
            try:
                lock.release()
            except Exception as exc:
                logger.exception("failed to release Agent conversation lock")
                observability.metrics.increment(
                    "chunk_studio_agent_cleanup_failures_total",
                    resource="conversation_lock",
                    error_type=type(exc).__name__,
                )
            finally:
                # Cleanup failures must never leave the response generator blocked.
                events.put(None)

    threading.Thread(target=worker, name="agent-chat-stream", daemon=True).start()

    def body_iter():
        while True:
            item = events.get()
            if item is None:
                break
            yield _sse(str(item["kind"]), item["payload"], str(item["id"]))
        yield _sse("done", {})

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
