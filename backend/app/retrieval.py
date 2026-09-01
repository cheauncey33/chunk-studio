"""Production retrieval: constrained query rewrites, typed RRF recall, and reranking."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from http import HTTPStatus
from typing import Any

from . import embeddings, lexical, llm, observability, retrieval_experiments
from .evidence_locator import chunk_text_sha256
from .storage import vector_store


QUERY_REWRITE_MODEL = os.environ.get("RETRIEVAL_QUERY_MODEL", llm.DEFAULT_MODEL)
RERANK_MODEL = os.environ.get("RETRIEVAL_RERANK_MODEL", "qwen3-rerank")
CONTENT_TYPES = ("table", "section")
ROUTE_TOP_K = 30
CANDIDATES_PER_TYPE = 20
LEXICAL_CANDIDATES_PER_TYPE = 20
RRF_K = 60
SPECIAL_ROUTE_RESERVE = 3
DEFAULT_DENSE_THRESHOLD = 0.0
DEFAULT_RERANK_THRESHOLD = 0.2
DEFAULT_AGENTIC_RAG_ENABLED = True
DEFAULT_AGENTIC_RAG_MAX_ROUNDS = 3
DEFAULT_AGENTIC_RAG_MAX_SEARCH_CALLS = 3
DEFAULT_AGENTIC_RAG_TIMEOUT_SECONDS = 30.0
# Legacy equal quota (kept for callers that still pass final_per_type alone).
FINAL_PER_TYPE = 15
# Production judge delivery: prefer more tables than sections.
FINAL_TABLE = 8
FINAL_SECTION = 6
GENERAL_DENSE_ROUTES = ("production", "semantic", "keyword")
SPECIAL_ROUTE_CONTENT_TYPES = {
    "table_target": "table",
    "section_target": "section",
}
MAX_RERANK_DOCUMENT_CHARS = 2400
RERANK_INSTRUCTION = (
    "Given a standards compliance query, retrieve passages that directly provide "
    "the applicable requirement, parameter value, method, or calculation rule."
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_EQ_SPLIT_NUMBER_RE = re.compile(
    r"<eq\b[^>]*>(.*?)(\d)\s*</eq>\s*(\d+)(?=\D|$)",
    re.IGNORECASE | re.DOTALL,
)
_EQ_TAG_RE = re.compile(r"</?eq\b[^>]*>", re.IGNORECASE)
_STANDARD_RE = re.compile(
    r"(?:GB(?:/T)?|JB/T|DL/T|Q/GDW|IEC|ISO)\s*[0-9][0-9A-Z.\-/]*",
    re.IGNORECASE,
)
_CHINESE_ANCHOR_RE = re.compile(r"[\u4e00-\u9fff]{4,}")
_GENERIC_DOMAIN_SUFFIX_RE = re.compile(r"(?:限值|测量|试验|要求|规定|标准|项目)+$")

QueryPlanner = Callable[[str], dict[str, str]]
QueryBatchEmbedder = Callable[..., list[list[float]]]
VectorSearcher = Callable[..., dict[str, Any]]
TextReranker = Callable[..., list[tuple[int, float]]]
LexicalSearcher = Callable[..., dict[str, Any]]


def _bounded_threshold(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < 0 or number > 1:
        return default
    return round(number, 4)


def normalize_retrieval_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize persisted retrieval settings at the configuration boundary.

    ``similarity_threshold`` was ambiguous because it was applied after
    reranking, while ``vector_weight`` / ``keyword_weight`` were persisted but
    never consumed by this production pipeline. Keep a one-way compatibility
    mapping for old saved versions, then expose only the effective settings.
    """
    config = dict(value or {}) if isinstance(value, Mapping) else {}
    legacy_threshold = config.get("similarity_threshold")
    config.pop("similarity_threshold", None)
    config.pop("vector_weight", None)
    config.pop("keyword_weight", None)
    config["dense_threshold"] = _bounded_threshold(
        config.get("dense_threshold", DEFAULT_DENSE_THRESHOLD),
        DEFAULT_DENSE_THRESHOLD,
    )
    config["rerank_threshold"] = _bounded_threshold(
        config.get(
            "rerank_threshold",
            DEFAULT_RERANK_THRESHOLD if legacy_threshold is None else legacy_threshold,
        ),
        DEFAULT_RERANK_THRESHOLD,
    )
    enabled = config.get("agentic_rag_enabled", DEFAULT_AGENTIC_RAG_ENABLED)
    if isinstance(enabled, str):
        enabled = enabled.strip().casefold() not in {"0", "false", "no", "off"}
    config["agentic_rag_enabled"] = bool(enabled)
    try:
        max_rounds = int(config.get("agentic_rag_max_rounds", DEFAULT_AGENTIC_RAG_MAX_ROUNDS))
    except (TypeError, ValueError):
        max_rounds = DEFAULT_AGENTIC_RAG_MAX_ROUNDS
    config["agentic_rag_max_rounds"] = max(1, min(max_rounds, DEFAULT_AGENTIC_RAG_MAX_ROUNDS))
    try:
        max_search_calls = int(
            config.get("agentic_rag_max_search_calls", DEFAULT_AGENTIC_RAG_MAX_SEARCH_CALLS)
        )
    except (TypeError, ValueError):
        max_search_calls = DEFAULT_AGENTIC_RAG_MAX_SEARCH_CALLS
    config["agentic_rag_max_search_calls"] = max(
        1,
        min(max_search_calls, DEFAULT_AGENTIC_RAG_MAX_SEARCH_CALLS),
    )
    try:
        timeout_seconds = float(
            config.get("agentic_rag_timeout_seconds", DEFAULT_AGENTIC_RAG_TIMEOUT_SECONDS)
        )
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_AGENTIC_RAG_TIMEOUT_SECONDS
    config["agentic_rag_timeout_seconds"] = max(1.0, min(timeout_seconds, 120.0))
    return config


class _RerankTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def _legacy_plan_query_rewrites(query: str, *, model: str = QUERY_REWRITE_MODEL) -> dict[str, str]:
    planner_query = _normalize_query_for_rewrite(query)
    if not planner_query:
        raise ValueError("query must not be blank")
    parsed = llm.chat_json(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是电力标准证据检索的查询改写器。只根据用户原始查询改写，不得补充查询中"
                    "没有出现的标准号、型号含义、产品结构、参数值或答案。semantic应改写为寻找"
                    "适用标准证据的问题；keyword应压缩为原查询中已有的标准术语、型号、数值、"
                    "单位和试验简称。两个字段都必须使用中文，并逐字保留原查询中的设备类型、"
                    "试验名称、参数名称及连续中文领域短语；不得翻译、替换或省略这些短语。"
                    "严格返回JSON对象："
                    '{"semantic":"...","keyword":"..."}'
                ),
            },
            {"role": "user", "content": planner_query},
        ],
        model=model,
        temperature=0,
    )
    rewrites = {
        route: _validate_rewrite(planner_query, parsed.get(route), route=route)
        for route in ("semantic", "keyword")
    }
    if len(set(rewrites.values())) != len(rewrites):
        raise ValueError("query rewriter returned duplicate routes")
    return rewrites


def plan_query_rewrites(
    query: str,
    *,
    model: str = QUERY_REWRITE_MODEL,
) -> dict[str, str]:
    """Add validated rewrite routes while keeping the original query intact."""
    planner_query = _normalize_query_for_rewrite(query)
    if not planner_query:
        raise ValueError("query must not be blank")
    parsed = llm.chat_json(
        messages=[
            {
                "role": "system",
                "content": (
                    "Rewrite a standards-evidence retrieval query into two JSON fields. "
                    "Use only terms and facts present in the original query. Never add "
                    "a standard number, product type, parameter value, unit, or answer. "
                    "The semantic field should be a natural-language evidence question. "
                    "The keyword field should preserve exact standard numbers, model names, "
                    "parameter names, values, units, and test abbreviations. Both fields "
                    "must preserve the original domain anchors and numbers. Return exactly "
                    "{\"semantic\":\"...\",\"keyword\":\"...\"}."
                ),
            },
            {"role": "user", "content": planner_query},
        ],
        model=model,
        temperature=0,
    )
    rewrites = {
        route: _validate_rewrite(planner_query, parsed.get(route), route=route)
        for route in ("semantic", "keyword")
    }
    if len(set(rewrites.values())) != len(rewrites):
        raise ValueError("query rewriter returned duplicate routes")
    return rewrites


def rerank_documents(
    query: str,
    documents: list[str],
    top_n: int,
    *,
    model: str = RERANK_MODEL,
) -> list[tuple[int, float]]:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured for Qwen reranking")
    if not documents:
        return []

    from dashscope import TextReRank

    expected = min(top_n, len(documents))
    response = TextReRank.call(
        api_key=api_key,
        model=model,
        query=query,
        documents=documents,
        top_n=expected,
        instruct=RERANK_INSTRUCTION,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"rerank failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )

    ranked: list[tuple[int, float]] = []
    seen: set[int] = set()
    try:
        for item in response.output.get("results", []):
            index = int(item["index"])
            if index in seen or not 0 <= index < len(documents):
                raise RuntimeError("rerank returned an invalid document index")
            seen.add(index)
            ranked.append((index, float(item["relevance_score"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("rerank returned an invalid result payload") from exc
    if len(ranked) != expected:
        raise RuntimeError("rerank returned an unexpected result count")
    return ranked


def hybrid_search(
    query: str,
    *,
    top_k: int = 10,
    route_top_k: int = ROUTE_TOP_K,
    candidates_per_type: int = CANDIDATES_PER_TYPE,
    lexical_candidates_per_type: int | None = None,
    rrf_k: int = RRF_K,
    dense_threshold: float | None = None,
    rerank_threshold: float | None = None,
    # Backward-compatible request argument. Persisted configurations must use
    # dense_threshold / rerank_threshold instead.
    similarity_threshold: float | None = None,
    query_routes: dict[str, str] | None = None,
    special_route_reserve: int = 0,
    final_per_type: int | None = None,
    final_table: int | None = None,
    final_section: int | None = None,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
    planner: QueryPlanner | None = None,
    batch_embedder: QueryBatchEmbedder | None = None,
    vector_searcher: VectorSearcher | None = None,
    reranker: TextReranker | None = None,
    lexical_searcher: LexicalSearcher | None = None,
    lexical_enabled: bool | None = None,
    file_ids: list[str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    timings_ms: dict[str, float] = {}

    def finish_stage(stage: str, stage_started: float) -> None:
        elapsed_seconds = time.perf_counter() - stage_started
        timings_ms[stage] = round(elapsed_seconds * 1000, 3)
        observability.metrics.observe(
            "chunk_studio_stage_duration_seconds",
            elapsed_seconds,
            component="retrieval",
            stage=stage,
            status="ok",
        )

    query = query.strip()
    if not query:
        raise ValueError("query must not be blank")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    if not 1 <= route_top_k <= 100:
        raise ValueError("route_top_k must be between 1 and 100")
    if not 1 <= candidates_per_type <= 100:
        raise ValueError("candidates_per_type must be between 1 and 100")
    if not 1 <= rrf_k <= 200:
        raise ValueError("rrf_k must be between 1 and 200")
    legacy_similarity_threshold = (
        similarity_threshold is not None and rerank_threshold is None
    )
    if legacy_similarity_threshold:
        legacy_value = float(similarity_threshold)
        rerank_threshold = None if legacy_value < 0 else legacy_value
    if dense_threshold is not None and not 0 <= float(dense_threshold) <= 1:
        raise ValueError("dense_threshold must be between 0 and 1")
    if rerank_threshold is not None and not 0 <= float(rerank_threshold) <= 1:
        raise ValueError("rerank_threshold must be between 0 and 1")
    if not 0 <= special_route_reserve <= 20:
        raise ValueError("special_route_reserve must be between 0 and 20")
    if final_per_type is not None and not 1 <= final_per_type <= 50:
        raise ValueError("final_per_type must be between 1 and 50")
    if final_table is not None and not 1 <= final_table <= 50:
        raise ValueError("final_table must be between 1 and 50")
    if final_section is not None and not 1 <= final_section <= 50:
        raise ValueError("final_section must be between 1 and 50")
    final_quotas = _resolve_final_type_quotas(
        final_per_type=final_per_type,
        final_table=final_table,
        final_section=final_section,
    )
    lexical_pool_size = (
        LEXICAL_CANDIDATES_PER_TYPE
        if lexical_candidates_per_type is None
        else lexical_candidates_per_type
    )
    if not 1 <= lexical_pool_size <= 100:
        raise ValueError("lexical_candidates_per_type must be between 1 and 100")

    planner = planner or plan_query_rewrites
    batch_embedder = batch_embedder or embeddings.embed_queries_with_dashscope
    vector_searcher = vector_searcher or vector_store.get_vector_store().search_by_vector
    reranker = reranker or rerank_documents
    lexical_searcher = lexical_searcher or lexical.search
    if lexical_enabled is None:
        lexical_enabled = lexical.production_enabled()

    degraded: list[str] = []
    stage_started = time.perf_counter()
    routes, routes_injected = _resolve_query_routes(
        query,
        query_routes=query_routes,
        planner=planner,
        degraded=degraded,
    )
    finish_stage("query_planning", stage_started)

    route_names = list(routes)
    route_queries = list(routes.values())
    stage_started = time.perf_counter()
    try:
        route_vectors = batch_embedder(
            route_queries,
            model=embeddings.DEFAULT_MODEL,
            dimension=embeddings.DEFAULT_DIMENSION,
        )
    except RuntimeError:
        if len(routes) == 1:
            raise
        degraded.append("rewrite_embedding_failed")
        routes = {"production": query}
        route_names = ["production"]
        route_queries = [query]
        route_vectors = batch_embedder(
            route_queries,
            model=embeddings.DEFAULT_MODEL,
            dimension=embeddings.DEFAULT_DIMENSION,
        )
    if len(route_vectors) != len(routes):
        raise RuntimeError("query embedder returned an unexpected vector count")
    finish_stage("query_embedding", stage_started)

    route_vector_by_name = dict(zip(route_names, route_vectors, strict=True))
    dense_by_type: dict[str, list[dict[str, Any]]] = {}
    total_by_type: dict[str, int] = {}
    reserved_by_type: dict[str, list[dict[str, Any]]] = {kind: [] for kind in CONTENT_TYPES}

    stage_started = time.perf_counter()
    for content_type in CONTENT_TYPES:
        merged: dict[str, dict[str, Any]] = {}
        for route, route_query in routes.items():
            if not _route_applies_to_content_type(route, content_type):
                continue
            vector_kwargs = {
                "top_k": route_top_k,
                "content_type": content_type,
                "model": embeddings.DEFAULT_MODEL,
                "dimension": embeddings.DEFAULT_DIMENSION,
            }
            if file_ids is not None:
                vector_kwargs["file_ids"] = file_ids
            if workspace_id is not None:
                vector_kwargs["workspace_id"] = workspace_id
            result = vector_searcher(
                route_query,
                route_vector_by_name[route],
                **vector_kwargs,
            )
            total_by_type.setdefault(content_type, int(result["total_candidates"]))
            for rank, hit in enumerate(result["hits"], start=1):
                if (
                    dense_threshold is not None
                    and float(dense_threshold) > 0
                    and float(hit.get("score") or 0.0) < float(dense_threshold)
                ):
                    continue
                candidate = merged.setdefault(
                    hit["chunk_id"],
                    _candidate(hit, content_type=content_type, source="dense"),
                )
                candidate["route_ranks"][route] = rank
                candidate["route_scores"][route] = float(hit["score"])
                candidate["source_ranks"][f"dense:{route}"] = rank
                candidate["source_scores"][f"dense:{route}"] = float(hit["score"])
                candidate["rrf_score"] += 1 / (rrf_k + rank)

        ranked = _rank_candidates(merged.values())
        dense_by_type[content_type] = ranked[:candidates_per_type]
        if special_route_reserve > 0:
            reserved_by_type[content_type] = _special_route_reserves(
                ranked,
                content_type=content_type,
                reserve=special_route_reserve,
            )
    finish_stage("dense_retrieval", stage_started)

    lexical_by_type: dict[str, list[dict[str, Any]]] = {}
    dual_active = False
    if lexical_enabled:
        stage_started = time.perf_counter()
        sync_index = True
        try:
            lexical_routes = [
                (route, routes[route])
                for route in ("production", "keyword")
                if route in routes
            ]
            for content_type in CONTENT_TYPES:
                merged = {}
                for route, route_query in lexical_routes:
                    lexical_kwargs = {
                        "content_type": content_type,
                        "top_k": route_top_k,
                        "sync": sync_index,
                    }
                    if file_ids is not None:
                        lexical_kwargs["file_ids"] = file_ids
                    result = lexical_searcher(
                        route_query,
                        **lexical_kwargs,
                    )
                    sync_index = False
                    for rank, hit in enumerate(result["hits"], start=1):
                        candidate = merged.setdefault(
                            hit["chunk_id"],
                            _candidate(hit, content_type=content_type, source="lexical"),
                        )
                        candidate["route_ranks"][route] = rank
                        candidate["route_scores"][route] = float(hit["score"])
                        candidate["source_ranks"][f"lexical:{route}"] = rank
                        candidate["source_scores"][f"lexical:{route}"] = float(hit["score"])
                        candidate["rrf_score"] += 1 / (rrf_k + rank)
                lexical_by_type[content_type] = _rank_candidates(merged.values())[
                    :lexical_pool_size
                ]
            dual_active = True
        except (RuntimeError, sqlite3.Error):
            degraded.append("lexical_retrieval_failed")
            lexical_by_type = {}
        finish_stage("lexical_retrieval", stage_started)

    stage_started = time.perf_counter()
    candidate_pool: list[dict[str, Any]] = []
    for content_type in CONTENT_TYPES:
        merged_type = _merge_candidate_lists(
            dense_by_type[content_type],
            lexical_by_type.get(content_type, []),
        )
        if reserved_by_type[content_type]:
            merged_type = _merge_candidate_lists(
                merged_type,
                reserved_by_type[content_type],
            )
        candidate_pool.extend(merged_type)

    candidate_pool = _rank_candidates(candidate_pool)
    candidate_count = len(candidate_pool)
    finish_stage("candidate_merge", stage_started)
    mode_prefix = "dual" if dual_active else "dense"
    if not candidate_pool:
        timings_ms["total"] = round((time.perf_counter() - total_started) * 1000, 3)
        return _response(
            query,
            routes,
            total_by_type,
            [],
            candidate_count=0,
            retrieval_mode=f"{mode_prefix}_rerank",
            rerank_model=RERANK_MODEL,
            degraded=degraded,
            routes_injected=routes_injected,
            special_route_reserve=special_route_reserve,
            final_per_type=final_per_type,
            final_table=final_quotas["table"] if final_quotas else None,
            final_section=final_quotas["section"] if final_quotas else None,
            dense_threshold=dense_threshold,
            rerank_threshold=rerank_threshold,
            timings_ms=timings_ms,
        )

    documents = [_rerank_document(candidate) for candidate in candidate_pool]
    select_n = (
        candidate_count
        if final_quotas is not None
        else min(top_k, candidate_count)
    )
    stage_started = time.perf_counter()
    try:
        reranked = reranker(query, documents, select_n)
        ordered = [
            _result_hit(candidate_pool[index], rerank_score=score)
            for index, score in reranked
        ]
        retrieval_mode = f"{mode_prefix}_rerank"
        rerank_model: str | None = RERANK_MODEL
    except RuntimeError:
        degraded.append("rerank_failed")
        ordered = [
            _result_hit(candidate, rerank_score=None)
            for candidate in candidate_pool[:select_n]
        ]
        retrieval_mode = f"{mode_prefix}_rrf_fallback"
        rerank_model = None
    finish_stage("rerank", stage_started)

    if final_quotas is not None:
        selected = _slice_final_per_type(ordered, final_quotas=final_quotas)
    else:
        selected = ordered[:top_k]

    if rerank_threshold is not None and float(rerank_threshold) > 0:
        if rerank_model is not None:
            selected = [
                hit
                for hit in selected
                if hit.get("rerank_score") is not None
                and float(hit["rerank_score"]) >= float(rerank_threshold)
            ]
        elif legacy_similarity_threshold:
            selected = [
                hit
                for hit in selected
                if float(hit.get("score") or 0.0) >= float(rerank_threshold)
            ]
    elif legacy_similarity_threshold and rerank_threshold is None:
        # Preserve the old fallback behavior only for direct legacy callers.
        selected = [
            hit for hit in selected if float(hit.get("score") or 0.0) >= float(similarity_threshold)
        ]

    if expand_references or aggregate_continuation_tables:
        stage_started = time.perf_counter()
        selected = _enrich_evidence_hits(
            selected,
            expand_references=expand_references,
            aggregate_continuations=aggregate_continuation_tables,
        )
        finish_stage("evidence_enrichment", stage_started)

    timings_ms["total"] = round((time.perf_counter() - total_started) * 1000, 3)
    return _response(
        query,
        routes,
        total_by_type,
        selected,
        candidate_count=candidate_count,
        retrieval_mode=retrieval_mode,
        rerank_model=rerank_model,
        degraded=degraded,
        routes_injected=routes_injected,
        special_route_reserve=special_route_reserve,
        final_per_type=final_per_type,
        final_table=final_quotas["table"] if final_quotas else None,
        final_section=final_quotas["section"] if final_quotas else None,
        dense_threshold=dense_threshold,
        rerank_threshold=rerank_threshold,
        timings_ms=timings_ms,
    )


def _candidate_pool_order(
    _query: str,
    documents: list[str],
    top_n: int,
) -> list[tuple[int, float]]:
    """Keep internal RRF order without making an external rerank call."""
    return [(index, 0.0) for index in range(min(top_n, len(documents)))]


def retrieve_candidate_pool(
    query: str,
    *,
    query_routes: dict[str, str] | None = None,
    route_top_k: int = ROUTE_TOP_K,
    candidates_per_type: int = CANDIDATES_PER_TYPE,
    lexical_candidates_per_type: int | None = None,
    rrf_k: int = RRF_K,
    dense_threshold: float | None = None,
    special_route_reserve: int = 0,
    planner: QueryPlanner | None = None,
    batch_embedder: QueryBatchEmbedder | None = None,
    vector_searcher: VectorSearcher | None = None,
    lexical_searcher: LexicalSearcher | None = None,
    lexical_enabled: bool | None = None,
    file_ids: list[str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Return the bounded hybrid candidate pool before external reranking."""
    result = hybrid_search(
        query,
        top_k=50,
        route_top_k=route_top_k,
        candidates_per_type=candidates_per_type,
        lexical_candidates_per_type=lexical_candidates_per_type,
        rrf_k=rrf_k,
        dense_threshold=dense_threshold,
        similarity_threshold=None,
        query_routes=query_routes,
        special_route_reserve=special_route_reserve,
        aggregate_continuation_tables=False,
        expand_references=False,
        planner=planner,
        batch_embedder=batch_embedder,
        vector_searcher=vector_searcher,
        reranker=_candidate_pool_order,
        lexical_searcher=lexical_searcher,
        lexical_enabled=lexical_enabled,
        file_ids=file_ids,
        workspace_id=workspace_id,
    )
    for hit in result["hits"]:
        hit["rerank_score"] = None
    prefix = str(result.get("retrieval_mode") or "hybrid").split("_", 1)[0]
    result["retrieval_mode"] = f"{prefix}_candidate_pool"
    result["rerank_model"] = None
    return result


def merge_and_rerank_candidate_pools(
    query: str,
    pools: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = RRF_K,
    final_table: int | None = None,
    final_section: int | None = None,
    rerank_threshold: float | None = None,
    # Backward-compatible argument for recovery callers created before the
    # threshold split.
    similarity_threshold: float | None = None,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
    reranker: TextReranker | None = None,
) -> dict[str, Any]:
    """Hash-deduplicate candidate pools, apply one RRF, then one rerank."""
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    if not pools:
        return {
            "query": query,
            "candidate_count": 0,
            "retrieval_mode": "recovery_empty",
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    legacy_similarity_threshold = (
        similarity_threshold is not None and rerank_threshold is None
    )
    if legacy_similarity_threshold:
        legacy_value = float(similarity_threshold)
        rerank_threshold = None if legacy_value < 0 else legacy_value
    if rerank_threshold is not None and not 0 <= float(rerank_threshold) <= 1:
        raise ValueError("rerank_threshold must be between 0 and 1")

    merged: dict[str, dict[str, Any]] = {}
    degraded: list[str] = []
    for pool_index, pool in enumerate(pools, start=1):
        degraded.extend(str(item) for item in pool.get("degraded") or [])
        for pool_rank, raw_hit in enumerate(pool.get("hits") or [], start=1):
            hit = dict(raw_hit)
            text_hash = chunk_text_sha256(hit.get("text"))
            key = text_hash or str(hit.get("chunk_id") or f"pool-{pool_index}-{pool_rank}")
            current = merged.get(key)
            if current is None:
                current = {
                    "hit": hit,
                    "rrf_score": 0.0,
                    "source_ranks": {},
                    "route_ranks": {},
                    "retrieval_sources": [],
                }
                merged[key] = current

            source_ranks = hit.get("source_ranks")
            if not isinstance(source_ranks, dict) or not source_ranks:
                source_ranks = {"pool": pool_rank}
            for source, rank_value in source_ranks.items():
                rank = int(rank_value)
                source_key = f"p{pool_index}:{source}"
                if source_key in current["source_ranks"]:
                    continue
                current["source_ranks"][source_key] = rank
                current["rrf_score"] += 1 / (rrf_k + rank)

            for route, rank_value in (hit.get("route_ranks") or {}).items():
                current["route_ranks"][f"p{pool_index}:{route}"] = int(rank_value)
            for source in hit.get("retrieval_sources") or []:
                source_name = f"p{pool_index}:{source}"
                if source_name not in current["retrieval_sources"]:
                    current["retrieval_sources"].append(source_name)

    candidates = sorted(
        merged.values(),
        key=lambda item: (
            float(item["rrf_score"]),
            str(item["hit"].get("chunk_id") or ""),
        ),
        reverse=True,
    )
    documents = [_rerank_document({"hit": item["hit"]}) for item in candidates]
    active_reranker = reranker or rerank_documents
    final_quotas = _resolve_final_type_quotas(
        final_per_type=None,
        final_table=final_table,
        final_section=final_section,
    )
    select_n = len(candidates) if final_quotas is not None else min(top_k, len(candidates))
    try:
        ranked = active_reranker(query, documents, select_n)
        ordered = []
        for index, score in ranked:
            item = candidates[index]
            hit = dict(item["hit"])
            hit["rrf_score"] = item["rrf_score"]
            hit["rerank_score"] = float(score)
            hit["source_ranks"] = item["source_ranks"]
            hit["route_ranks"] = item["route_ranks"]
            hit["retrieval_sources"] = item["retrieval_sources"]
            ordered.append(hit)
        rerank_model: str | None = RERANK_MODEL
        mode = "recovery_unified_rerank"
    except RuntimeError:
        degraded.append("rerank_failed")
        ordered = []
        for item in candidates[:select_n]:
            hit = dict(item["hit"])
            hit["rrf_score"] = item["rrf_score"]
            hit["rerank_score"] = None
            hit["source_ranks"] = item["source_ranks"]
            hit["route_ranks"] = item["route_ranks"]
            hit["retrieval_sources"] = item["retrieval_sources"]
            ordered.append(hit)
        rerank_model = None
        mode = "recovery_rrf_fallback"

    if final_quotas is not None:
        selected = _slice_final_per_type(ordered, final_quotas=final_quotas)
    else:
        selected = ordered[:top_k]
    if rerank_threshold is not None and float(rerank_threshold) > 0:
        if rerank_model is not None:
            selected = [
                hit
                for hit in selected
                if hit.get("rerank_score") is not None
                and float(hit["rerank_score"]) >= float(rerank_threshold)
            ]
        elif legacy_similarity_threshold:
            selected = [
                hit
                for hit in selected
                if float(hit.get("score") or 0.0) >= float(rerank_threshold)
            ]
    elif legacy_similarity_threshold and rerank_threshold is None:
        selected = [
            hit for hit in selected if float(hit.get("score") or 0.0) >= float(similarity_threshold)
        ]
    if expand_references or aggregate_continuation_tables:
        selected = _enrich_evidence_hits(
            selected,
            expand_references=expand_references,
            aggregate_continuations=aggregate_continuation_tables,
        )
    return {
        "query": query,
        "candidate_count": len(candidates),
        "dedup_strategy": "chunk_text_sha256",
        "retrieval_mode": mode,
        "rerank_model": rerank_model,
        "degraded": list(dict.fromkeys(degraded)),
        "hits": selected,
    }


def _enrich_evidence_hits(
    hits: list[dict[str, Any]],
    *,
    expand_references: bool = True,
    aggregate_continuations: bool = True,
) -> list[dict[str, Any]]:
    """Post-rerank enrichment: C (见表 N) then B (续表聚合/补全)."""
    if not hits:
        return hits
    candidates = [_as_evidence_candidate(hit) for hit in hits]
    if expand_references:
        candidates = retrieval_experiments.expand_table_references(
            candidates,
            fetch_table_chunks=_safe_fetch_table_chunks,
        )
    if aggregate_continuations:
        candidates = retrieval_experiments.aggregate_continuation_tables(
            candidates,
            complete_groups=_safe_table_group_members,
        )
    return [_normalize_evidence_hit(candidate) for candidate in candidates]


def _safe_fetch_table_chunks(file_id: str, table_no: str) -> list[dict[str, Any]]:
    try:
        return retrieval_experiments.db_fetch_table_chunks(file_id, table_no)
    except (sqlite3.Error, OSError, RuntimeError):
        return []


def _safe_table_group_members(
    file_id: str, standard_no: str, table_no: str
) -> list[dict[str, Any]]:
    try:
        return retrieval_experiments.db_table_group_members(file_id, standard_no, table_no)
    except (sqlite3.Error, OSError, RuntimeError):
        return []


def _as_evidence_candidate(hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    candidate = dict(hit)
    candidate.setdefault(
        "content_type",
        hit.get("content_type") or metadata.get("content_type") or "",
    )
    return candidate


def _normalize_evidence_hit(candidate: dict[str, Any]) -> dict[str, Any]:
    hit = dict(candidate)
    metadata = hit.get("business_metadata") or {}
    hit.setdefault("content_type", metadata.get("content_type") or hit.get("content_type") or "")
    hit.setdefault("file_name", hit.get("file_name") or "")
    hit.setdefault("crop_url", hit.get("crop_url"))
    hit.setdefault("score", float(hit.get("score") or 0.0))
    hit.setdefault("rrf_score", hit.get("rrf_score"))
    hit.setdefault("rerank_score", hit.get("rerank_score"))
    hit.setdefault("route_ranks", hit.get("route_ranks") or {})
    hit.setdefault("retrieval_sources", hit.get("retrieval_sources") or [])
    hit.setdefault("source_ranks", hit.get("source_ranks") or {})
    if hit.get("added_by") and "experiment" not in hit["retrieval_sources"]:
        hit["retrieval_sources"] = [*hit["retrieval_sources"], "experiment"]
    return hit


def _resolve_query_routes(
    query: str,
    *,
    query_routes: dict[str, str] | None,
    planner: QueryPlanner,
    degraded: list[str],
) -> tuple[dict[str, str], bool]:
    if query_routes is not None:
        routes = _normalize_injected_routes(query, query_routes)
        return routes, True

    routes = {"production": query}
    try:
        planned = planner(query)
        for route in ("semantic", "keyword"):
            value = str(planned.get(route) or "").strip()
            if value and value not in routes.values():
                routes[route] = value
        # A rewrite may legitimately be identical to the raw query. Keep the
        # production route and any distinct validated additions instead of
        # degrading the whole planner result.
    except (RuntimeError, ValueError, json.JSONDecodeError, KeyError, TypeError):
        degraded.append("query_rewrite_failed")
        routes = {"production": query}
    return routes, False


def _normalize_injected_routes(query: str, query_routes: dict[str, str]) -> dict[str, str]:
    routes: dict[str, str] = {"production": query}
    production = str(query_routes.get("production") or "").strip()
    if production:
        routes["production"] = production
    allowed = (*GENERAL_DENSE_ROUTES, *SPECIAL_ROUTE_CONTENT_TYPES)
    for route in allowed:
        if route == "production":
            continue
        value = str(query_routes.get(route) or "").strip()
        if not value:
            continue
        if value in routes.values() and route not in SPECIAL_ROUTE_CONTENT_TYPES:
            continue
        routes[route] = value
    if "production" not in routes or not routes["production"]:
        raise ValueError("query_routes must include a production query")
    return routes


def _route_applies_to_content_type(route: str, content_type: str) -> bool:
    special = SPECIAL_ROUTE_CONTENT_TYPES.get(route)
    if special is not None:
        return special == content_type
    return route in GENERAL_DENSE_ROUTES or route == "production"


def _special_route_reserves(
    ranked: list[dict[str, Any]],
    *,
    content_type: str,
    reserve: int,
) -> list[dict[str, Any]]:
    special_route = next(
        (
            route
            for route, target in SPECIAL_ROUTE_CONTENT_TYPES.items()
            if target == content_type
        ),
        None,
    )
    if not special_route or reserve <= 0:
        return []
    keyed = [
        candidate
        for candidate in ranked
        if special_route in candidate.get("route_ranks", {})
    ]
    keyed.sort(
        key=lambda item: (
            item["route_ranks"].get(special_route, 10**9),
            -float(item["route_scores"].get(special_route, 0.0)),
            item["hit"]["chunk_id"],
        )
    )
    return keyed[:reserve]


def _resolve_final_type_quotas(
    *,
    final_per_type: int | None,
    final_table: int | None,
    final_section: int | None,
) -> dict[str, int] | None:
    """Resolve typed delivery quotas after rerank.

    Prefer explicit final_table / final_section. Legacy final_per_type alone
    still means equal quota for both types.
    """
    if final_table is None and final_section is None and final_per_type is None:
        return None
    if final_table is not None or final_section is not None:
        return {
            "table": int(
                FINAL_TABLE if final_table is None else final_table
            ),
            "section": int(
                FINAL_SECTION if final_section is None else final_section
            ),
        }
    assert final_per_type is not None
    return {"table": int(final_per_type), "section": int(final_per_type)}


def _slice_final_per_type(
    ordered: list[dict[str, Any]],
    *,
    final_quotas: dict[str, int] | None = None,
    final_per_type: int | None = None,
) -> list[dict[str, Any]]:
    quotas = final_quotas or {
        kind: int(final_per_type or 0) for kind in CONTENT_TYPES
    }
    selected: list[dict[str, Any]] = []
    counts = {kind: 0 for kind in CONTENT_TYPES}
    for hit in ordered:
        content_type = str((hit.get("business_metadata") or {}).get("content_type") or "")
        if content_type not in counts:
            # Keep unknown types only if both quotas still have room via fallback bucket.
            content_type = "section" if counts["section"] <= counts["table"] else "table"
        if counts[content_type] >= int(quotas.get(content_type, 0)):
            continue
        counts[content_type] += 1
        selected.append(hit)
    return selected


def _response(
    query: str,
    routes: dict[str, str],
    total_by_type: dict[str, int],
    hits: list[dict[str, Any]],
    *,
    candidate_count: int,
    retrieval_mode: str,
    rerank_model: str | None,
    degraded: list[str],
    routes_injected: bool = False,
    special_route_reserve: int = 0,
    final_per_type: int | None = None,
    final_table: int | None = None,
    final_section: int | None = None,
    dense_threshold: float | None = None,
    rerank_threshold: float | None = None,
    timings_ms: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "query": query,
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "total_candidates": sum(total_by_type.values()),
        "candidate_count": candidate_count,
        "retrieval_mode": retrieval_mode,
        "query_routes": routes,
        "routes_injected": routes_injected,
        "special_route_reserve": special_route_reserve,
        "final_per_type": final_per_type,
        "final_table": final_table,
        "final_section": final_section,
        "dense_threshold": dense_threshold,
        "rerank_threshold": rerank_threshold,
        "rerank_model": rerank_model,
        "degraded": degraded,
        "timings_ms": timings_ms or {},
        "hits": hits,
    }


def _result_hit(candidate: dict[str, Any], *, rerank_score: float | None) -> dict[str, Any]:
    hit = dict(candidate["hit"])
    dense_scores = [
        score for route, score in candidate["source_scores"].items()
        if route.startswith("dense:")
    ]
    hit["score"] = max(dense_scores or candidate["source_scores"].values())
    hit["rrf_score"] = candidate["rrf_score"]
    hit["rerank_score"] = rerank_score
    hit["route_ranks"] = candidate["route_ranks"]
    hit["retrieval_sources"] = candidate["sources"]
    hit["source_ranks"] = candidate["source_ranks"]
    return hit


def _candidate(hit: dict[str, Any], *, content_type: str, source: str) -> dict[str, Any]:
    return {
        "hit": hit,
        "content_type": content_type,
        "sources": [source],
        "route_ranks": {},
        "route_scores": {},
        "source_ranks": {},
        "source_scores": {},
        "rrf_score": 0.0,
    }


def _rank_candidates(candidates: Any) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            item["rrf_score"],
            max(item["source_scores"].values(), default=0.0),
            item["hit"]["chunk_id"],
        ),
        reverse=True,
    )


def _merge_candidate_lists(
    dense_candidates: list[dict[str, Any]],
    lexical_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for candidate in [*dense_candidates, *lexical_candidates]:
        chunk_id = candidate["hit"]["chunk_id"]
        if chunk_id not in merged:
            merged[chunk_id] = {
                **candidate,
                "sources": list(candidate["sources"]),
                "route_ranks": dict(candidate["route_ranks"]),
                "route_scores": dict(candidate["route_scores"]),
                "source_ranks": dict(candidate["source_ranks"]),
                "source_scores": dict(candidate["source_scores"]),
            }
            continue
        current = merged[chunk_id]
        current["sources"] = list(dict.fromkeys([*current["sources"], *candidate["sources"]]))
        current["source_ranks"].update(candidate["source_ranks"])
        current["source_scores"].update(candidate["source_scores"])
        current["rrf_score"] += candidate["rrf_score"]
    return list(merged.values())


def _rerank_document(candidate: dict[str, Any]) -> str:
    hit = candidate["hit"]
    metadata = hit.get("business_metadata") or {}
    columns = metadata.get("table_columns")
    if isinstance(columns, list):
        columns = "、".join(str(item) for item in columns)
    labels = [
        metadata.get("standard_no"),
        metadata.get("section"),
        metadata.get("section_title"),
        metadata.get("table_no"),
        metadata.get("table_title"),
        columns,
    ]
    prefix = " | ".join(str(value).strip() for value in labels if value)
    body = _plain_text(str(hit.get("text") or ""))
    document = f"{prefix}\n{body}" if prefix and body else prefix or body
    return document[:MAX_RERANK_DOCUMENT_CHARS]


def _plain_text(value: str) -> str:
    parser = _RerankTextExtractor()
    parser.feed(value)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _normalize_query_for_rewrite(value: str) -> str:
    text = unescape(str(value or ""))
    text = _EQ_SPLIT_NUMBER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}",
        text,
    )
    text = _EQ_TAG_RE.sub(" ", text)
    replacements = (
        (r"\\leq(?:slant)?\b", "≤"),
        (r"\\le\b", "≤"),
        (r"\\geq(?:slant)?\b", "≥"),
        (r"\\ge\b", "≥"),
        (r"\\pm\b", "±"),
        (r"\\times\b", "×"),
        (r"\\cdot\b", "·"),
        (r"\\%", "%"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def _validate_rewrite(query: str, value: Any, *, route: str) -> str:
    rewrite = re.sub(r"\s+", " ", str(value or "")).strip()
    if not rewrite:
        raise ValueError(f"query rewriter omitted {route}")
    if len(rewrite) > 1024:
        raise ValueError(f"query rewriter returned an oversized {route} route")

    query_standards = {_normalize_reference(item) for item in _STANDARD_RE.findall(query)}
    rewrite_standards = {_normalize_reference(item) for item in _STANDARD_RE.findall(rewrite)}
    if rewrite_standards - query_standards:
        raise ValueError(f"query rewriter introduced a standard reference in {route}")

    query_numbers = {Decimal(item) for item in _NUMBER_RE.findall(query)}
    extra_numbers = {Decimal(item) for item in _NUMBER_RE.findall(rewrite)} - query_numbers
    if extra_numbers:
        raise ValueError(f"query rewriter introduced numbers in {route}: {sorted(extra_numbers)}")
    missing_anchors = [anchor for anchor in _query_domain_anchors(query) if anchor not in rewrite]
    if missing_anchors:
        raise ValueError(
            f"query rewriter omitted domain terms in {route}: {missing_anchors}"
        )
    return rewrite


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _query_domain_anchors(query: str) -> list[str]:
    anchors = []
    for value in _CHINESE_ANCHOR_RE.findall(query):
        anchor = _GENERIC_DOMAIN_SUFFIX_RE.sub("", value)
        for phrase in (
            "请根据",
            "根据",
            "已绑定知识库",
            "知识库",
            "说明",
            "介绍",
            "解释",
            "请给出",
            "给出",
            "页码引用",
            "引用",
            "是什么",
            "定义",
        ):
            anchor = anchor.replace(phrase, "")
        anchor = re.sub(r"[的之与及和并]", "", anchor)
        if len(anchor) >= 4 and anchor not in anchors:
            anchors.append(anchor)
    return anchors
