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

from . import db, embeddings, lexical


QUERY_REWRITE_MODEL = os.environ.get("RETRIEVAL_QUERY_MODEL", "qwen-flash")
RERANK_MODEL = os.environ.get("RETRIEVAL_RERANK_MODEL", "qwen3-rerank")
CONTENT_TYPES = ("table", "section")
ROUTE_TOP_K = 30
CANDIDATES_PER_TYPE = 20
LEXICAL_CANDIDATES_PER_TYPE = 20
RRF_K = 60
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
    api_key = os.environ.get("DASHSCOPE_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DashScope API key is not configured")

    from dashscope import Generation

    response = Generation.call(
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是电力标准证据检索的查询改写器。只根据用户原始查询改写，不得补充查询中"
                    "没有出现的标准号、型号含义、产品结构、参数值或答案。semantic应改写为寻找"
                    "适用标准证据的问题；keyword应压缩为原查询中已有的标准术语、型号、数值、"
                    "单位和试验简称。严格返回JSON对象："
                    '{"semantic":"...","keyword":"..."}'
                ),
            },
            {"role": "user", "content": planner_query},
        ],
        result_format="message",
        response_format={"type": "json_object"},
        temperature=0,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"query rewrite failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )
    content = response.output["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
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
    api_key = os.environ.get("DASHSCOPE_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DashScope API key is not configured")
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
    planner: QueryPlanner | None = None,
    batch_embedder: QueryBatchEmbedder | None = None,
    vector_searcher: VectorSearcher | None = None,
    reranker: TextReranker | None = None,
    lexical_searcher: LexicalSearcher | None = None,
    lexical_enabled: bool | None = None,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("query must not be blank")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")

    planner = planner or plan_query_rewrites
    batch_embedder = batch_embedder or embeddings.embed_queries_with_dashscope
    vector_searcher = vector_searcher or embeddings.vector_search_by_vector
    reranker = reranker or rerank_documents
    lexical_searcher = lexical_searcher or lexical.search
    if lexical_enabled is None:
        lexical_enabled = lexical.production_enabled()

    degraded: list[str] = []
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

    dense_by_type: dict[str, list[dict[str, Any]]] = {}
    total_by_type: dict[str, int] = {}
    for content_type in CONTENT_TYPES:
        merged: dict[str, dict[str, Any]] = {}
        for route, route_query, route_vector in zip(
            route_names, route_queries, route_vectors, strict=True
        ):
            result = vector_searcher(
                route_query,
                route_vector,
                top_k=ROUTE_TOP_K,
                content_type=content_type,
                model=embeddings.DEFAULT_MODEL,
                dimension=embeddings.DEFAULT_DIMENSION,
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
                candidate["rrf_score"] += 1 / (RRF_K + rank)

        dense_by_type[content_type] = _rank_candidates(merged.values())[:CANDIDATES_PER_TYPE]

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
                    result = lexical_searcher(
                        route_query,
                        content_type=content_type,
                        top_k=ROUTE_TOP_K,
                        sync=sync_index,
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
                        candidate["rrf_score"] += 1 / (RRF_K + rank)
                lexical_by_type[content_type] = _rank_candidates(merged.values())[
                    :LEXICAL_CANDIDATES_PER_TYPE
                ]
            dual_active = True
        except (RuntimeError, sqlite3.Error):
            degraded.append("lexical_retrieval_failed")
            lexical_by_type = {}

    candidate_pool: list[dict[str, Any]] = []
    for content_type in CONTENT_TYPES:
        candidate_pool.extend(
            _merge_candidate_lists(
                dense_by_type[content_type],
                lexical_by_type.get(content_type, []),
            )
        )

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
        )

    documents = [_rerank_document(candidate) for candidate in candidate_pool]
    try:
        reranked = reranker(query, documents, min(top_k, candidate_count))
        selected = [
            _result_hit(candidate_pool[index], rerank_score=score)
            for index, score in reranked
        ]
        retrieval_mode = f"{mode_prefix}_rerank"
        rerank_model: str | None = RERANK_MODEL
    except RuntimeError:
        degraded.append("rerank_failed")
        selected = [
            _result_hit(candidate, rerank_score=None)
            for candidate in candidate_pool[:top_k]
        ]
        retrieval_mode = f"{mode_prefix}_rrf_fallback"
        rerank_model = None

    return _response(
        query,
        routes,
        total_by_type,
        selected,
        candidate_count=candidate_count,
        retrieval_mode=retrieval_mode,
        rerank_model=rerank_model,
        degraded=degraded,
    )


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
) -> dict[str, Any]:
    return {
        "query": query,
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "total_candidates": sum(total_by_type.values()),
        "candidate_count": candidate_count,
        "retrieval_mode": retrieval_mode,
        "query_routes": routes,
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
    return rewrite


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("query rewriter did not return a JSON object")
    return parsed
