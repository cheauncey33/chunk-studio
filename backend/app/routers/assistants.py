"""Audit-assistant configuration APIs (single active config per assistant)."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import chat_agent, chat_sessions, db, jobs, llm, retrieval


router = APIRouter(prefix="/assistants", tags=["assistants"])

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


def _loads(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
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

    retrieval_config = _loads(row["retrieval_config"])
    retrieval_config.setdefault("aggregate_continuation_tables", False)
    retrieval_config.setdefault("expand_references", False)
    retrieval_config.setdefault("final_table", 8)
    retrieval_config.setdefault("final_section", 6)
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
        """SELECT model_config, node_prompts, rules, retrieval_config, parameter_schema
           FROM assistant_versions
           WHERE id='assistant_audit_template_v1'"""
    ).fetchone()
    if not row:
        raise RuntimeError("generic assistant version template is missing")
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
    from ..parameter_schema import resolve_parameter_schema

    if body.knowledge_base_id:
        try:
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
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO audit_assistants
               (id,name,description,status,active_version_id,created_at,updated_at)
               VALUES (?,?,?,'active',NULL,?,?)""",
            (assistant_id, body.name.strip(), body.description.strip(), now, now),
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


@router.get("/{assistant_id}/versions/active")
def get_active_version(assistant_id: str):
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(404, "assistant has no active version")
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
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    provenance = body.initialization_provenance
    if not isinstance(provenance, dict):
        provenance = {}
    source = str(provenance.get("source") or "").strip()
    # Saving from the editor clears "unspecialized template copy" markers so the
    # KB workflow UI stops nudging category init.
    if source in {"", "built_in_seed", "template_snapshot"}:
        provenance = {"source": "manual_config", "saved_at": now}
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
                    json.dumps(body.retrieval_config, ensure_ascii=False),
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
                    json.dumps(body.retrieval_config, ensure_ascii=False),
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
    if unique_ids:
        kb_id = unique_ids[0]
        row = db.get_conn().execute(
            "SELECT id FROM knowledge_bases WHERE id=?",
            (kb_id,),
        ).fetchone()
        if not row:
            raise HTTPException(422, "knowledge base does not exist")
        conflict = db.get_conn().execute(
            """SELECT assistant_id FROM assistant_knowledge_bases
               WHERE knowledge_base_id=? AND enabled=1 AND assistant_id!=?""",
            (kb_id, assistant_id),
        ).fetchone()
        if conflict:
            raise HTTPException(409, "该知识库已绑定其他助手")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM assistant_knowledge_bases WHERE assistant_id=?",
            (assistant_id,),
        )
        if unique_ids:
            kb_id = unique_ids[0]
            # Ensure the KB has no stale binds (disabled rows included).
            conn.execute(
                "DELETE FROM assistant_knowledge_bases WHERE knowledge_base_id=?",
                (kb_id,),
            )
            conn.execute(
                """INSERT INTO assistant_knowledge_bases
                   (assistant_id,knowledge_base_id,priority,enabled)
                   VALUES (?,?,0,1)""",
                (assistant_id, kb_id),
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
    version = db.get_conn().execute(
        "SELECT * FROM assistant_versions WHERE id=?",
        (assistant["active_version_id"],),
    ).fetchone()
    if not version:
        raise HTTPException(404, "active version not found")

    model_config = _loads(version["model_config"])
    retrieval_config = _loads(version["retrieval_config"])
    file_ids = db.assistant_scoped_file_ids(assistant_id)
    if not file_ids:
        raise HTTPException(400, "请先绑定知识库，并确保库内有已启用的文件")

    top_k = int(retrieval_config.get("top_k") or 10)
    route_top_k = int(retrieval_config.get("route_top_k") or 30)
    candidates_per_type = int(retrieval_config.get("candidate_count_per_type") or 20)
    similarity_threshold = retrieval_config.get("similarity_threshold")
    threshold = float(similarity_threshold) if similarity_threshold is not None else 0.2
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
            similarity_threshold=threshold,
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
            "similarity_threshold": threshold,
            "aggregate_continuation_tables": aggregate_continuation_tables,
            "expand_references": expand_references,
            "degraded": search.get("degraded") or [],
        },
    }


@router.get("/{assistant_id}/conversations")
def list_agent_conversations(assistant_id: str) -> dict[str, Any]:
    _assistant_row(assistant_id)
    return {"items": chat_sessions.list_conversations(assistant_id)}


@router.get("/{assistant_id}/conversations/{conversation_id}")
def get_agent_conversation(assistant_id: str, conversation_id: str) -> dict[str, Any]:
    _assistant_row(assistant_id)
    conversation = chat_sessions.get_conversation(conversation_id)
    if not conversation or conversation["assistant_id"] != assistant_id:
        raise HTTPException(404, "conversation not found")
    return {
        **conversation,
        "events": chat_sessions.list_events(conversation_id),
    }


def _prepare_agent_context(assistant_id: str, body: AgentChatRequest) -> dict[str, Any]:
    assistant = _assistant_row(assistant_id)
    if not assistant["active_version_id"]:
        raise HTTPException(400, "assistant has no active version")
    version = db.get_conn().execute(
        "SELECT * FROM assistant_versions WHERE id=?",
        (assistant["active_version_id"],),
    ).fetchone()
    if not version:
        raise HTTPException(404, "active version not found")

    file_ids = db.assistant_scoped_file_ids(assistant_id)
    if not file_ids:
        raise HTTPException(400, "请先绑定知识库，并确保库内有已启用的文件")

    conversation = (
        chat_sessions.get_conversation(body.conversation_id)
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
        )
    conversation_id = str(conversation["id"])

    chat_sessions.append_events(
        conversation_id,
        [("user_message", {"content": body.message.strip()})],
    )
    model_config = _loads(version["model_config"])
    retrieval_config = _loads(version["retrieval_config"])
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
        "file_ids": file_ids,
        "retrieval_config": retrieval_config,
        "model": model,
        "temperature": temperature,
    }


def _persist_agent_result(conversation_id: str, result: dict[str, Any]) -> None:
    persisted_events: list[tuple[str, dict[str, Any]]] = []
    for message in result.get("new_messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            payload: dict[str, Any] = {"message": message}
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
    }


@router.post("/{assistant_id}/agent-chat")
def assistant_agent_chat(assistant_id: str, body: AgentChatRequest) -> dict[str, Any]:
    """Persistent Agent chat using native model tool calls."""
    context = _prepare_agent_context(assistant_id, body)
    try:
        result = chat_agent.run_chat_agent(
            assistant_id=assistant_id,
            messages=chat_sessions.load_messages(context["conversation_id"]),
            file_ids=context["file_ids"],
            retrieval_config=context["retrieval_config"],
            model=context["model"],
            temperature=context["temperature"],
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    _persist_agent_result(context["conversation_id"], result)
    return _agent_response(context["conversation_id"], context["model"], result)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post("/{assistant_id}/agent-chat/stream")
def assistant_agent_chat_stream(assistant_id: str, body: AgentChatRequest) -> StreamingResponse:
    """Stream Agent lifecycle events while keeping the final result durable."""
    context = _prepare_agent_context(assistant_id, body)
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        try:
            result = chat_agent.run_chat_agent(
                assistant_id=assistant_id,
                messages=chat_sessions.load_messages(context["conversation_id"]),
                file_ids=context["file_ids"],
                retrieval_config=context["retrieval_config"],
                model=context["model"],
                temperature=context["temperature"],
                event_sink=lambda event: events.put({"kind": "agent", "payload": event}),
                stream_tokens=True,
            )
            _persist_agent_result(context["conversation_id"], result)
            events.put({
                "kind": "final",
                "payload": _agent_response(
                    context["conversation_id"],
                    context["model"],
                    result,
                ),
            })
        except Exception as exc:
            events.put({
                "kind": "error",
                "payload": {"error_type": type(exc).__name__, "error": str(exc)},
            })
        finally:
            events.put(None)

    threading.Thread(target=worker, name="agent-chat-stream", daemon=True).start()

    def body_iter():
        yield _sse("conversation", {"conversation_id": context["conversation_id"]})
        while True:
            item = events.get()
            if item is None:
                break
            yield _sse(str(item["kind"]), item["payload"])
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
