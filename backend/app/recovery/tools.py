from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import uuid
from typing import Any, Callable

from app import current_user, db, llm, retrieval
from app.agent_runtime.models import ToolDefinition
from app.report_context import search_report_markdown
from app.storage.repositories import get_content_repository, get_content_write_repository


CandidateSearch = Callable[..., dict[str, Any]]
ExactSearch = Callable[[list[str], list[str], str | None, int], dict[str, Any]]

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])")
_CLAUSE_REFERENCE_RE = re.compile(
    r"(?:见|按|依据|根据|符合|参照|引用|第|see|follow(?:s|ing)?|according\s+to|clause)\s*"
    r"(?P<reference>\d+(?:\.\d+){1,5})\s*(?:条|款|节)?",
    re.IGNORECASE,
)
_ALLOWED_OUTCOMES = {
    "evidence_found",
    "parameter_found",
    "partially_recovered",
    "exhausted",
    "runtime_failed",
}


def _require_list(arguments: dict[str, Any], key: str, *, limit: int) -> list[str]:
    raw = arguments.get(key)
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be an array")
    values = [str(item).strip() for item in raw if str(item).strip()]
    if not values or len(values) > limit:
        raise ValueError(f"{key} must contain 1-{limit} values")
    return values


def _target_numbers(requirement_text: str) -> set[str]:
    return {match.group(0) for match in _NUMBER_RE.finditer(requirement_text or "")}


def query_uses_report_target_value(query: str, requirement_text: str) -> bool:
    query_numbers = _target_numbers(query)
    return bool(query_numbers & _target_numbers(requirement_text))


def _compact_hit(hit: dict[str, Any], candidate_key: str) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "candidate_key": candidate_key,
        "file_id": hit.get("file_id"),
        "file_name": hit.get("file_name"),
        "page": hit.get("page"),
        "content_type": metadata.get("content_type") or hit.get("content_type"),
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "snippet": str(hit.get("text") or "")[:500],
    }


def _focus_exact_hit(
    hit: dict[str, Any], terms: list[str], *, radius: int = 1200
) -> dict[str, Any]:
    """Return a source-traced excerpt centered on the most specific exact term."""
    text = str(hit.get("text") or "")
    metadata = hit.get("business_metadata") or {}
    if len(text) <= radius * 2 or metadata.get("content_type") == "table":
        return hit
    matches = []
    lower = text.lower()
    for term in terms:
        index = lower.find(term.lower())
        if index >= 0:
            specificity = (bool(re.search(r"\d+(?:\.\d+)+", term)), len(term))
            matches.append((specificity, index, term))
    if not matches:
        return hit
    _, center, matched_term = max(matches, key=lambda item: item[0])
    left = max(0, center - radius)
    right = min(len(text), center + len(matched_term) + radius)
    focused = dict(hit)
    focused["text"] = text[left:right]
    focused["source_trace"] = {
        **(hit.get("source_trace") or {}),
        "recovery_excerpt": {
            "char_start": left,
            "char_end": right,
            "matched_term": matched_term,
        },
    }
    return focused


def _default_exact_search(
    terms: list[str],
    file_ids: list[str],
    standard_no: str | None,
    limit: int,
) -> dict[str, Any]:
    if not file_ids:
        return {"hits": [], "candidate_count": 0, "degraded": []}
    workspace_id = current_user.get_current_user().workspace_id
    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        rows = repository.exact_search(terms, file_ids, standard_no, limit)
    else:
        clauses = [
            "c.workspace_id=?",
            "f.workspace_id=?",
            f"c.file_id IN ({','.join('?' for _ in file_ids)})",
        ]
        params: list[Any] = [workspace_id, workspace_id, *file_ids]
        term_clauses = []
        for term in terms:
            term_clauses.append("(c.text LIKE ? OR c.business_metadata LIKE ?)")
            params.extend((f"%{term}%", f"%{term}%"))
        if term_clauses:
            clauses.append("(" + " OR ".join(term_clauses) + ")")
        if standard_no:
            clauses.append("json_extract(c.business_metadata, '$.standard_no') = ?")
            params.append(standard_no)
        params.append(limit)
        rows = db.get_conn().execute(
            f"""SELECT c.id AS chunk_id, c.file_id, c.page, c.bbox, c.text,
                       c.business_metadata, c.source_trace, f.name AS file_name
                  FROM chunks c
                  JOIN files f ON f.id=c.file_id
                 WHERE {' AND '.join(clauses)}
                 ORDER BY c.file_id, c.page, c.id
                 LIMIT ?""",
            params,
        ).fetchall()
    hits = []
    for rank, row in enumerate(rows, start=1):
        bbox = row["bbox"] if isinstance(row["bbox"], dict) else json.loads(row["bbox"] or "{}")
        business_metadata = (
            row["business_metadata"]
            if isinstance(row["business_metadata"], dict)
            else json.loads(row["business_metadata"] or "{}")
        )
        source_trace = (
            row["source_trace"]
            if isinstance(row["source_trace"], dict)
            else json.loads(row["source_trace"] or "{}")
        )
        hit = {
            "chunk_id": row["chunk_id"],
            "file_id": row["file_id"],
            "file_name": row["file_name"],
            "page": row["page"],
            "bbox": bbox,
            "text": row["text"] or "",
            "business_metadata": business_metadata,
            "source_trace": source_trace,
            "score": 1.0,
            "rerank_score": None,
            "rrf_score": 1 / (retrieval.RRF_K + rank),
            "route_ranks": {"exact": rank},
            "source_ranks": {"exact": rank},
            "retrieval_sources": ["exact"],
        }
        hits.append(_focus_exact_hit(hit, terms))
    return {
        "hits": hits,
        "candidate_count": len(hits),
        "degraded": [],
        "retrieval_mode": "exact_candidate_pool",
    }


@dataclass
class RecoveryToolEnvironment:
    report_markdown: str
    allowed_file_ids: list[str]
    requirement_text: str
    original_query: str
    workspace_id: str | None = None
    model: str = llm.DEFAULT_MODEL
    retrieval_config: dict[str, Any] = field(default_factory=dict)
    candidate_search: CandidateSearch = retrieval.retrieve_candidate_pool
    exact_search: ExactSearch = _default_exact_search
    pool_store: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_store: dict[str, dict[str, Any]] = field(default_factory=dict)

    def store_pool(self, pool: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        pool_id = f"pool_{uuid.uuid4().hex[:10]}"
        stored = dict(pool)
        compact = []
        for rank, hit in enumerate(pool.get("hits") or [], start=1):
            key = f"{pool_id}_c{rank:02d}"
            self.candidate_store[key] = hit
            compact.append(_compact_hit(hit, key))
        self.pool_store[pool_id] = stored
        return pool_id, compact


def _search_report_context(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    return search_report_markdown(
        env.report_markdown,
        arguments.get("terms"),
        max_results=int(arguments.get("max_results") or 8),
    )


def _extract_report_parameters(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    fields = _require_list(arguments, "fields", limit=8)
    evidence = arguments.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence must be a non-empty array from search_report_context")
    bounded = [item for item in evidence[:12] if isinstance(item, dict)]
    parsed = llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "你是检测报告参数提取器。只能从给定证据逐字提取指定字段，不得使用常识补值。"
                    "每个字段返回 status=found|ambiguous|not_found；found 必须给 value 和 evidence_index。"
                    "严格返回JSON对象：{\"parameters\":[...]}。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"fields": fields, "evidence": bounded},
                    ensure_ascii=False,
                ),
            },
        ],
        model=env.model,
        temperature=0,
    )
    parameters = parsed.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError("parameter extractor returned invalid parameters")
    return {
        "summary": f"processed {len(fields)} requested parameters",
        "parameters": parameters,
    }


def _search_kb_candidates(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query must not be blank")
    if query_uses_report_target_value(query, env.requirement_text):
        raise ValueError("query contains a report target value")
    routes = arguments.get("query_routes")
    if routes is not None and not isinstance(routes, dict):
        raise ValueError("query_routes must be an object")
    config = retrieval.normalize_retrieval_config(env.retrieval_config)
    search_kwargs: dict[str, Any] = {
        "query_routes": routes,
        "route_top_k": int(config.get("route_top_k") or retrieval.ROUTE_TOP_K),
        "candidates_per_type": int(
            config.get("candidate_count_per_type") or retrieval.CANDIDATES_PER_TYPE
        ),
        "rrf_k": int(config.get("rrf_k") or retrieval.RRF_K),
        "dense_threshold": float(config.get("dense_threshold") or 0.0),
        "special_route_reserve": int(config.get("special_route_reserve") or 0),
        "file_ids": env.allowed_file_ids,
    }
    if env.workspace_id:
        search_kwargs["workspace_id"] = env.workspace_id
    pool = env.candidate_search(
        query,
        **search_kwargs,
    )
    pool_id, compact = env.store_pool(pool)
    return {
        "summary": f"retrieved {len(compact)} candidates",
        "pool_id": pool_id,
        "query": query,
        "candidates": compact[:12],
        "candidate_count": len(compact),
        "degraded": pool.get("degraded") or [],
    }


def _search_kb_exact(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    terms = _require_list(arguments, "terms", limit=8)
    if any(query_uses_report_target_value(term, env.requirement_text) for term in terms):
        raise ValueError("exact terms contain a report target value")
    standard_no = str(arguments.get("standard_no") or "").strip() or None
    limit = max(1, min(int(arguments.get("max_results") or 20), 40))
    pool = env.exact_search(terms, env.allowed_file_ids, standard_no, limit)
    pool_id, compact = env.store_pool(pool)
    return {
        "summary": f"found {len(compact)} exact candidates",
        "pool_id": pool_id,
        "terms": terms,
        "candidates": compact[:12],
        "candidate_count": len(compact),
        "degraded": pool.get("degraded") or [],
    }


def _expand_evidence_context(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    keys = _require_list(arguments, "candidate_keys", limit=6)
    missing = [key for key in keys if key not in env.candidate_store]
    if missing:
        raise ValueError(f"unknown candidate keys: {', '.join(missing)}")
    hits = [env.candidate_store[key] for key in keys]
    expanded = retrieval._enrich_evidence_hits(
        hits,
        expand_references=True,
        aggregate_continuations=True,
    )
    pool = {
        "hits": expanded,
        "candidate_count": len(expanded),
        "degraded": [],
        "retrieval_mode": "context_expansion_pool",
    }
    pool_id, compact = env.store_pool(pool)
    return {
        "summary": f"expanded {len(keys)} candidates into {len(compact)} evidence units",
        "pool_id": pool_id,
        "candidates": compact[:12],
        "candidate_count": len(compact),
    }


def _clause_references(hit: dict[str, Any]) -> list[str]:
    """Extract explicit clause references, excluding unqualified decimal values."""
    text = str(hit.get("text") or "")
    return list(
        dict.fromkeys(
            match.group("reference").strip()
            for match in _CLAUSE_REFERENCE_RE.finditer(text)
        )
    )


def _follow_evidence_references(
    env: RecoveryToolEnvironment,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Follow source-declared clause references within the same file/standard."""
    keys = _require_list(arguments, "candidate_keys", limit=6)
    missing = [key for key in keys if key not in env.candidate_store]
    if missing:
        raise ValueError(f"unknown candidate keys: {', '.join(missing)}")

    grouped: dict[tuple[str, str], list[str]] = {}
    references_by_candidate: dict[str, list[str]] = {}
    for key in keys:
        hit = env.candidate_store[key]
        references = _clause_references(hit)[:6]
        references_by_candidate[key] = references
        file_id = str(hit.get("file_id") or "").strip()
        metadata = hit.get("business_metadata") or {}
        standard_no = str(metadata.get("standard_no") or "").strip()
        if file_id and references:
            bucket = grouped.setdefault((file_id, standard_no), [])
            bucket.extend(reference for reference in references if reference not in bucket)

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for (file_id, standard_no), references in grouped.items():
        pool = env.exact_search(references[:8], [file_id], standard_no or None, 20)
        for hit in pool.get("hits") or []:
            identity = (
                str(hit.get("file_id") or ""),
                str(hit.get("chunk_id") or hit.get("text") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            hits.append({**hit, "added_by": "clause_reference_follow"})

    pool = {
        "hits": hits,
        "candidate_count": len(hits),
        "degraded": [],
        "retrieval_mode": "clause_reference_pool",
    }
    pool_id, compact = env.store_pool(pool)
    return {
        "summary": f"followed {sum(map(len, references_by_candidate.values()))} explicit clause references into {len(compact)} candidates",
        "pool_id": pool_id,
        "references_by_candidate": references_by_candidate,
        "candidates": compact[:12],
        "candidate_count": len(compact),
    }


def _locate_standard_clause(env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    standard_no = str(arguments.get("standard_no") or "").strip()
    if not standard_no:
        raise ValueError("standard_no must not be blank")
    terms = _require_list(arguments, "terms", limit=8)
    return _search_kb_exact(
        env,
        {"terms": terms, "standard_no": standard_no, "max_results": arguments.get("max_results")},
    )


def _finish_recovery(_env: RecoveryToolEnvironment, arguments: dict[str, Any]) -> dict[str, Any]:
    outcome = str(arguments.get("outcome") or "").strip()
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError("invalid recovery outcome")
    return {
        "outcome": outcome,
        "recovered_parameters": arguments.get("recovered_parameters") or [],
        "candidate_keys": arguments.get("candidate_keys") or [],
        "remaining_gaps": arguments.get("remaining_gaps") or [],
        "attempted_queries": arguments.get("attempted_queries") or [],
        "exhaustion_reason": arguments.get("exhaustion_reason"),
    }


def build_recovery_tools(env: RecoveryToolEnvironment) -> list[ToolDefinition]:
    object_schema = {"type": "object", "additionalProperties": False}
    short_string_array = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "maxItems": 8,
    }
    candidate_key_array = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "maxItems": 6,
    }
    return [
        ToolDefinition(
            name="search_report_context",
            description="Search literal terms only inside the current report.",
            input_schema={**object_schema, "properties": {"terms": short_string_array, "max_results": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["terms"]},
            execute=lambda args: _search_report_context(env, args),
            category="search",
        ),
        ToolDefinition(
            name="extract_report_parameters",
            description="Extract requested report fields only from supplied report-search evidence.",
            input_schema={**object_schema, "properties": {"fields": short_string_array, "evidence": {"type": "array", "minItems": 1, "maxItems": 12}}, "required": ["fields", "evidence"]},
            execute=lambda args: _extract_report_parameters(env, args),
        ),
        ToolDefinition(
            name="search_kb_candidates",
            description="Retrieve an unrereanked value-free candidate pool inside allowed knowledge files.",
            input_schema={**object_schema, "properties": {"query": {"type": "string", "minLength": 1}, "query_routes": {"type": "object"}}, "required": ["query"]},
            execute=lambda args: _search_kb_candidates(env, args),
            category="search",
        ),
        ToolDefinition(
            name="search_kb_exact",
            description="Literal lookup for standard, clause, table, symbol, or domain terms in allowed knowledge files.",
            input_schema={**object_schema, "properties": {"terms": short_string_array, "standard_no": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 40}}, "required": ["terms"]},
            execute=lambda args: _search_kb_exact(env, args),
            category="search",
        ),
        ToolDefinition(
            name="expand_evidence_context",
            description="Expand candidates through adjacent chunks and continuation/table relations.",
            input_schema={**object_schema, "properties": {"candidate_keys": candidate_key_array}, "required": ["candidate_keys"]},
            execute=lambda args: _expand_evidence_context(env, args),
        ),
        ToolDefinition(
            name="follow_evidence_references",
            description="Deterministically follow explicit clause references found in candidates, constrained to each source file and standard.",
            input_schema={**object_schema, "properties": {"candidate_keys": candidate_key_array}, "required": ["candidate_keys"]},
            execute=lambda args: _follow_evidence_references(env, args),
            category="search",
        ),
        ToolDefinition(
            name="locate_standard_clause",
            description="Locate clause/table terms inside one allowed standard; follow an explicit reference such as 3.22 by passing it as a term.",
            input_schema={**object_schema, "properties": {"standard_no": {"type": "string", "minLength": 1}, "terms": short_string_array, "max_results": {"type": "integer", "minimum": 1, "maximum": 40}}, "required": ["standard_no", "terms"]},
            execute=lambda args: _locate_standard_clause(env, args),
            category="search",
        ),
        ToolDefinition(
            name="finish_recovery",
            description="Finish recovery without issuing an audit verdict.",
            input_schema={**object_schema, "properties": {"outcome": {"type": "string"}, "recovered_parameters": {"type": "array"}, "candidate_keys": {"type": "array"}, "remaining_gaps": {"type": "array"}, "attempted_queries": {"type": "array"}, "exhaustion_reason": {"type": ["string", "null"]}}, "required": ["outcome"]},
            execute=lambda args: _finish_recovery(env, args),
            category="finish",
        ),
    ]
