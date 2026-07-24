"""Production retrieval: constrained query rewrites, typed RRF recall, and reranking."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from http import HTTPStatus
from typing import Any

from . import embeddings, lexical, llm, retrieval_experiments


QUERY_REWRITE_MODEL = os.environ.get("RETRIEVAL_QUERY_MODEL", llm.DEFAULT_MODEL)
RERANK_MODEL = os.environ.get("RETRIEVAL_RERANK_MODEL", "qwen3-rerank")
CONTENT_TYPES = ("table", "section")
ROUTE_TOP_K = 30
CANDIDATES_PER_TYPE = 20
LEXICAL_CANDIDATES_PER_TYPE = 20
RRF_K = 60
SPECIAL_ROUTE_RESERVE = 3
FINAL_PER_TYPE = 15
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


class _RerankTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def plan_query_rewrites(query: str, *, model: str = QUERY_REWRITE_MODEL) -> dict[str, str]:
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
    similarity_threshold: float | None = None,
    query_routes: dict[str, str] | None = None,
    special_route_reserve: int = 0,
    final_per_type: int | None = None,
    aggregate_continuation_tables: bool = False,
    expand_references: bool = False,
    planner: QueryPlanner | None = None,
    batch_embedder: QueryBatchEmbedder | None = None,
    vector_searcher: VectorSearcher | None = None,
    reranker: TextReranker | None = None,
    lexical_searcher: LexicalSearcher | None = None,
    lexical_enabled: bool | None = None,
    file_ids: list[str] | None = None,
) -> dict[str, Any]:
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
    if not 0 <= special_route_reserve <= 20:
        raise ValueError("special_route_reserve must be between 0 and 20")
    if final_per_type is not None and not 1 <= final_per_type <= 50:
        raise ValueError("final_per_type must be between 1 and 50")
    lexical_pool_size = (
        LEXICAL_CANDIDATES_PER_TYPE
        if lexical_candidates_per_type is None
        else lexical_candidates_per_type
    )
    if not 1 <= lexical_pool_size <= 100:
        raise ValueError("lexical_candidates_per_type must be between 1 and 100")

    planner = planner or plan_query_rewrites
    batch_embedder = batch_embedder or embeddings.embed_queries_with_dashscope
    vector_searcher = vector_searcher or embeddings.vector_search_by_vector
    reranker = reranker or rerank_documents
    lexical_searcher = lexical_searcher or lexical.search
    if lexical_enabled is None:
        lexical_enabled = lexical.production_enabled()

    degraded: list[str] = []
    routes, routes_injected = _resolve_query_routes(
        query,
        query_routes=query_routes,
        planner=planner,
        degraded=degraded,
    )

    route_names = list(routes)
    route_queries = list(routes.values())
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

    route_vector_by_name = dict(zip(route_names, route_vectors, strict=True))
    dense_by_type: dict[str, list[dict[str, Any]]] = {}
    total_by_type: dict[str, int] = {}
    reserved_by_type: dict[str, list[dict[str, Any]]] = {kind: [] for kind in CONTENT_TYPES}

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
            result = vector_searcher(
                route_query,
                route_vector_by_name[route],
                **vector_kwargs,
            )
            total_by_type.setdefault(content_type, int(result["total_candidates"]))
            for rank, hit in enumerate(result["hits"], start=1):
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

    lexical_by_type: dict[str, list[dict[str, Any]]] = {}
    dual_active = False
    if lexical_enabled:
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
    mode_prefix = "dual" if dual_active else "dense"
    if not candidate_pool:
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
        )

    documents = [_rerank_document(candidate) for candidate in candidate_pool]
    select_n = (
        candidate_count
        if final_per_type is not None
        else min(top_k, candidate_count)
    )
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

    if final_per_type is not None:
        selected = _slice_final_per_type(ordered, final_per_type=final_per_type)
    else:
        selected = ordered[:top_k]

    if similarity_threshold is not None:
        selected = [
            hit
            for hit in selected
            if float(
                hit["rerank_score"]
                if hit.get("rerank_score") is not None
                else hit.get("score") or 0
            )
            >= similarity_threshold
        ]

    if expand_references or aggregate_continuation_tables:
        selected = _enrich_evidence_hits(
            selected,
            expand_references=expand_references,
            aggregate_continuations=aggregate_continuation_tables,
        )

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
    )


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
        if len(routes) != 3:
            raise ValueError("query rewriter omitted or duplicated a route")
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


def _slice_final_per_type(
    ordered: list[dict[str, Any]],
    *,
    final_per_type: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts = {kind: 0 for kind in CONTENT_TYPES}
    for hit in ordered:
        content_type = str((hit.get("business_metadata") or {}).get("content_type") or "")
        if content_type not in counts:
            # Keep unknown types only if both quotas still have room via fallback bucket.
            content_type = "section" if counts["section"] <= counts["table"] else "table"
        if counts[content_type] >= final_per_type:
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
        "rerank_model": rerank_model,
        "degraded": degraded,
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
        if len(anchor) >= 4 and anchor not in anchors:
            anchors.append(anchor)
    return anchors
