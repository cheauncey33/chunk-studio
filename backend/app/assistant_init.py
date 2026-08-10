"""Category init workflow: induce schema + specialize report_parameters prompt."""
from __future__ import annotations

import json
import time
from typing import Any

from . import audit_run, config, db, llm
from .parameter_schema import resolve_parameter_schema
from .storage.repositories import get_content_repository, get_content_write_repository

SCHEMA_INDUCTION_PROMPT = (
    config.PROJECT_ROOT
    / "evaluation"
    / "prompts"
    / "generic"
    / "category_schema_induction_v1.md"
)
SPECIALIZE_PROMPT = (
    config.PROJECT_ROOT
    / "evaluation"
    / "prompts"
    / "generic"
    / "report_parameters_specialize_v1.md"
)

MAX_STANDARD_FILES = 8
MAX_SAMPLE_FILES = 3
MAX_CHARS_PER_FILE = 6000
MAX_TOTAL_STANDARD_CHARS = 24000
MAX_TOTAL_SAMPLE_CHARS = 18000


def _content_repository():
    return get_content_repository() or get_content_write_repository()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_prompt(path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"prompt missing: {path}")


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _file_excerpt(file_id: str, *, limit: int = MAX_CHARS_PER_FILE) -> dict[str, str]:
    repository = _content_repository()
    row = repository.get_file(file_id) if repository is not None else db.get_conn().execute(
        "SELECT id, name FROM files WHERE id=?", (file_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"file not found: {file_id}")
    path = audit_run.resolve_markdown_path(file_id)
    text = path.read_text(encoding="utf-8")
    if len(text) > limit:
        text = text[:limit] + "\n…(截断)"
    return {"file_id": file_id, "name": row["name"], "excerpt": text}


def list_standard_corpus_file_ids(assistant_id: str) -> list[str]:
    """Enabled KB corpus files with completed markdown parses (prefer standard)."""
    repository = _content_repository()
    if repository is not None:
        return repository.list_standard_corpus_file_ids(assistant_id)
    rows = db.get_conn().execute(
        """SELECT kbf.file_id, kbf.corpus_kind, f.name
           FROM assistant_knowledge_bases akb
           JOIN knowledge_base_files kbf
             ON kbf.knowledge_base_id=akb.knowledge_base_id AND kbf.enabled=1
           JOIN files f ON f.id=kbf.file_id
           WHERE akb.assistant_id=? AND akb.enabled=1
             AND EXISTS (
               SELECT 1 FROM document_parses dp
               WHERE dp.file_id=f.id AND dp.status='done'
                 AND dp.markdown_path IS NOT NULL AND TRIM(dp.markdown_path)!=''
             )
           ORDER BY
             CASE WHEN LOWER(COALESCE(kbf.corpus_kind,''))='standard' THEN 0 ELSE 1 END,
             f.name ASC""",
        (assistant_id,),
    ).fetchall()
    return [str(row["file_id"]) for row in rows]


def get_init_draft(assistant_id: str) -> dict[str, Any] | None:
    repository = _content_repository()
    if repository is not None:
        row = repository.get_init_draft(assistant_id)
        if not row:
            return None
        return {
            "assistant_id": row["assistant_id"],
            "status": row["status"],
            "payload": _loads(row.get("payload"), {}),
            "job_id": row.get("job_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    row = db.get_conn().execute(
        "SELECT * FROM assistant_init_drafts WHERE assistant_id=?",
        (assistant_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "assistant_id": row["assistant_id"],
        "status": row["status"],
        "payload": _loads(row["payload"], {}),
        "job_id": row["job_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_init_draft(
    assistant_id: str,
    *,
    status: str,
    payload: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    now = _now()
    existing = get_init_draft(assistant_id)
    body = payload if payload is not None else (existing or {}).get("payload") or {}
    repository = _content_repository()
    if repository is not None:
        row = repository.upsert_init_draft(
            assistant_id,
            status=status,
            payload=body,
            job_id=job_id,
        )
        return {
            "assistant_id": row["assistant_id"],
            "status": row["status"],
            "payload": _loads(row.get("payload"), {}),
            "job_id": row.get("job_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    with db.transaction() as conn:
        if existing:
            conn.execute(
                """UPDATE assistant_init_drafts
                   SET status=?, payload=?, job_id=COALESCE(?, job_id), updated_at=?
                   WHERE assistant_id=?""",
                (
                    status,
                    json.dumps(body, ensure_ascii=False),
                    job_id,
                    now,
                    assistant_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO assistant_init_drafts
                   (assistant_id, status, payload, job_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    assistant_id,
                    status,
                    json.dumps(body, ensure_ascii=False),
                    job_id,
                    now,
                    now,
                ),
            )
    draft = get_init_draft(assistant_id)
    assert draft is not None
    return draft


def update_init_draft_payload(assistant_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    draft = get_init_draft(assistant_id)
    if not draft:
        raise KeyError("init draft not found")
    if draft["status"] not in {"ready", "failed", "discarded"}:
        if draft["status"] == "generating":
            raise ValueError("draft is still generating")
        if draft["status"] == "applied":
            raise ValueError("draft already applied; start a new init to edit")
    payload = dict(draft["payload"] or {})
    if "parameter_schema" in patch:
        payload["parameter_schema"] = resolve_parameter_schema(patch["parameter_schema"])
    if "report_parameters_prompt" in patch:
        # Optional notes only; runnable brief is built from parameter_schema at runtime.
        payload["report_parameters_prompt"] = str(patch["report_parameters_prompt"] or "").strip()
    status = "ready" if draft["status"] in {"ready", "failed", "discarded"} else draft["status"]
    return upsert_init_draft(assistant_id, status=status, payload=payload)


def _build_excerpts(file_ids: list[str], *, total_limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    used = 0
    for file_id in file_ids:
        remaining = total_limit - used
        if remaining <= 200:
            break
        item = _file_excerpt(file_id, limit=min(MAX_CHARS_PER_FILE, remaining))
        used += len(item["excerpt"])
        out.append(item)
    return out


def _active_version_row(assistant_id: str) -> Any:
    repository = _content_repository()
    if repository is not None:
        row = repository.get_active_assistant_version(assistant_id)
        if not row:
            raise ValueError("assistant has no active version")
        return row
    assistant = db.get_conn().execute(
        "SELECT active_version_id FROM audit_assistants WHERE id=?",
        (assistant_id,),
    ).fetchone()
    if not assistant or not assistant["active_version_id"]:
        raise ValueError("assistant has no active version")
    row = db.get_conn().execute(
        "SELECT * FROM assistant_versions WHERE id=?",
        (assistant["active_version_id"],),
    ).fetchone()
    if not row:
        raise ValueError("active version missing")
    return row


def generate_init_draft(
    assistant_id: str,
    *,
    sample_report_file_ids: list[str],
    model: str | None = None,
) -> dict[str, Any]:
    """Run LLM induction + specialization; return ready payload."""
    unique_samples = list(dict.fromkeys(sample_report_file_ids))[:MAX_SAMPLE_FILES]
    standard_ids = list_standard_corpus_file_ids(assistant_id)[:MAX_STANDARD_FILES]
    if not standard_ids and not unique_samples:
        raise ValueError("需要至少一个已解析的标准语料或样例报告")

    for file_id in unique_samples:
        audit_run.resolve_markdown_path(file_id)

    version = _active_version_row(assistant_id)
    base_schema = resolve_parameter_schema(_loads(version["parameter_schema"], {}))
    node_prompts = _loads(version["node_prompts"], {})
    base_prompt = ""
    if isinstance(node_prompts.get("report_parameters"), dict):
        base_prompt = str(node_prompts["report_parameters"].get("content") or "")
    if not base_prompt.strip():
        generic = (
            config.PROJECT_ROOT
            / "evaluation"
            / "prompts"
            / "generic"
            / "report_parameter_extraction_generic_v1.md"
        )
        base_prompt = generic.read_text(encoding="utf-8") if generic.is_file() else ""

    standard_excerpts = _build_excerpts(standard_ids, total_limit=MAX_TOTAL_STANDARD_CHARS)
    sample_excerpts = _build_excerpts(unique_samples, total_limit=MAX_TOTAL_SAMPLE_CHARS)

    induction = llm.chat_json(
        [
            {"role": "system", "content": _read_prompt(SCHEMA_INDUCTION_PROMPT)},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "base_parameter_schema": base_schema,
                        "standard_excerpts": standard_excerpts,
                        "sample_excerpts": sample_excerpts,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=model,
    )
    schema = resolve_parameter_schema(induction.get("parameter_schema"))

    specialized = llm.chat_text(
        [
            {"role": "system", "content": _read_prompt(SPECIALIZE_PROMPT)},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "parameter_schema": schema,
                        "base_prompt": base_prompt,
                        "sample_excerpts": sample_excerpts,
                        "standard_excerpts": standard_excerpts,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=model,
    )
    prompt_text = specialized.strip()
    if prompt_text.startswith("```"):
        # Strip accidental fences.
        lines = prompt_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        prompt_text = "\n".join(lines).strip()
    # Treat "no notes" markers as empty optional notes.
    if prompt_text in {"（无）", "(无)", "无", "无补充", "（无补充）", "N/A", "n/a"}:
        prompt_text = ""

    return {
        "parameter_schema": schema,
        "report_parameters_prompt": prompt_text,
        "source_file_ids": {
            "standard": standard_ids,
            "sample_reports": unique_samples,
        },
        "model": model or "",
        "generated_at": _now(),
        "error": "",
    }


def _sanitize_copied_node_prompt(step_id: str, node: Any) -> dict[str, Any]:
    """Keep path; blank content when it looks like a full legacy system prompt."""
    from .audit_judge_notes import looks_like_full_audit_judge_prompt
    from .model_decode_notes import looks_like_full_model_decode_prompt
    from .query_planner_routes import looks_like_full_query_planner_prompt
    from .report_parameters_prompt import looks_like_full_extraction_prompt
    from .test_items_notes import looks_like_full_test_items_prompt

    if not isinstance(node, dict):
        return {"path": "", "content": ""}
    path = str(node.get("path") or "")
    content = str(node.get("content") or "").strip()
    if step_id == "query_planner" and looks_like_full_query_planner_prompt(content):
        content = ""
    elif step_id == "audit_judge" and looks_like_full_audit_judge_prompt(content):
        content = ""
    elif step_id == "report_parameters" and looks_like_full_extraction_prompt(content):
        content = ""
    elif step_id == "test_items" and looks_like_full_test_items_prompt(content):
        content = ""
    elif step_id == "model_decode" and looks_like_full_model_decode_prompt(content):
        content = ""
    return {"path": path, "content": content}


def apply_init_draft(assistant_id: str) -> dict[str, Any]:
    """Overwrite the active assistant config from the ready draft.

    Init variable package is only ``parameter_schema`` + optional report_parameters
    notes. Other steps copy prior config; full legacy prompts in notes are stripped.
    """
    from .parameter_schema import resolve_parameter_schema as resolve_schema
    from .report_parameters_prompt import looks_like_full_extraction_prompt

    draft = get_init_draft(assistant_id)
    if not draft:
        raise KeyError("init draft not found")
    if draft["status"] != "ready":
        raise ValueError(f"draft status must be ready, got {draft['status']}")
    payload = draft["payload"] or {}
    schema = resolve_schema(payload.get("parameter_schema"))
    if "report_parameters_prompt" not in payload:
        raise ValueError("draft missing report_parameters_prompt")
    prompt_text = str(payload.get("report_parameters_prompt") or "").strip()
    if looks_like_full_extraction_prompt(prompt_text):
        prompt_text = ""

    try:
        version = _active_version_row(assistant_id)
    except ValueError:
        version = None
    model_config = _loads(version["model_config"], {}) if version else {}
    node_prompts = _loads(version["node_prompts"], {}) if version else {}
    rules = _loads(version["rules"], {}) if version else {}
    retrieval_config = _loads(version["retrieval_config"], {}) if version else {}
    source_file_ids = payload.get("source_file_ids")
    if not isinstance(source_file_ids, dict):
        source_file_ids = {}
    initialization_provenance = {
        "source": "assistant_init_draft",
        "standard_file_ids": list(source_file_ids.get("standard") or []),
        "sample_report_file_ids": list(source_file_ids.get("sample_reports") or []),
        "model": str(payload.get("model") or ""),
        "generated_at": str(payload.get("generated_at") or draft["updated_at"]),
        "applied_at": _now(),
        "draft_job_id": draft.get("job_id"),
    }
    if not isinstance(node_prompts, dict):
        node_prompts = {}
    sanitized: dict[str, Any] = {}
    for key, value in node_prompts.items():
        if key in {
            "query_planner",
            "audit_judge",
            "report_parameters",
            "test_items",
            "model_decode",
        }:
            sanitized[key] = _sanitize_copied_node_prompt(key, value)
        else:
            sanitized[key] = value
    existing_rp = sanitized.get("report_parameters")
    path = ""
    if isinstance(existing_rp, dict):
        path = str(existing_rp.get("path") or "")
    node_prompts = {
        **sanitized,
        "report_parameters": {
            "path": path or "unified/extraction_brief_v1",
            "content": prompt_text,
        },
    }

    now = _now()
    repository = _content_repository()
    if repository is not None:
        applied = repository.apply_init_draft(
            assistant_id,
            model_config=model_config,
            node_prompts=node_prompts,
            rules=rules,
            retrieval_config=retrieval_config,
            parameter_schema=schema,
            initialization_provenance=initialization_provenance,
            applied_at=now,
        )
        return {
            "assistant_id": assistant_id,
            "version_id": applied["version_id"],
            "version": applied["version"],
            "parameter_schema": schema,
            "initialization_provenance": initialization_provenance,
        }
    with db.transaction() as conn:
        version_id = version["id"] if version else None
        version_no = int(version["version"]) if version else 1
        if version_id:
            conn.execute(
                """UPDATE assistant_versions
                   SET status='active',
                       model_config=?, node_prompts=?, rules=?,
                       retrieval_config=?, parameter_schema=?,
                       category_profile='{}',
                       initialization_provenance=?,
                       activated_at=?
                   WHERE id=? AND assistant_id=?""",
                (
                    json.dumps(model_config, ensure_ascii=False),
                    json.dumps(node_prompts, ensure_ascii=False),
                    json.dumps(rules, ensure_ascii=False),
                    json.dumps(retrieval_config, ensure_ascii=False),
                    json.dumps(schema, ensure_ascii=False),
                    json.dumps(initialization_provenance, ensure_ascii=False),
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
            version_id = f"{assistant_id}_v1_{__import__('uuid').uuid4().hex[:8]}"
            version_no = 1
            conn.execute(
                """INSERT INTO assistant_versions
                   (id,assistant_id,version,name,status,model_config,node_prompts,rules,
                    retrieval_config,parameter_schema,category_profile,
                    initialization_provenance,created_at,activated_at)
                   VALUES (?,?,1,'','active',?,?,?,?,?,'{}',?,?,?)""",
                (
                    version_id,
                    assistant_id,
                    json.dumps(model_config, ensure_ascii=False),
                    json.dumps(node_prompts, ensure_ascii=False),
                    json.dumps(rules, ensure_ascii=False),
                    json.dumps(retrieval_config, ensure_ascii=False),
                    json.dumps(schema, ensure_ascii=False),
                    json.dumps(initialization_provenance, ensure_ascii=False),
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
        conn.execute(
            """UPDATE assistant_init_drafts
               SET status='applied', updated_at=?
               WHERE assistant_id=?""",
            (now, assistant_id),
        )

    return {
        "assistant_id": assistant_id,
        "version_id": version_id,
        "version": version_no,
        "parameter_schema": schema,
        "initialization_provenance": initialization_provenance,
    }
