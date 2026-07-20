"""Evaluate fixed-budget dense and dense-plus-lexical retrieval against v2 evidence.

This is an offline ablation. It uses frozen production/semantic/keyword queries,
excludes target/oracle routes, keeps candidate budgets comparable, and does not
write an index or modify production retrieval.

Run from the repository root:
    uv run --with jieba python scripts/evaluate_dual_retrieval_v2.py
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app import embeddings, retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from demo_lexical_retrieval import (  # noqa: E402
    ChunkDoc,
    DEFAULT_DB,
    DEFAULT_TERMS,
    active_weights,
    load_docs,
    load_domain_terms,
    tokens_for_search,
)


DEFAULT_GROUND_TRUTH = ROOT / "evaluation" / "retrieval_ground_truth_v2_draft.json"
DEFAULT_QUERIES = (
    ROOT
    / "evaluation"
    / "frozen"
    / "retrieval_eval_v1_candidate_2026-07-13"
    / "reports"
    / "retrieval_queries_40_v1.json"
)
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "dual_retrieval_v2_2026-07-15.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "dual_retrieval_v2_2026-07-15.md"
CONTENT_TYPES = ("table", "section")
DENSE_ROUTES = ("production", "semantic", "keyword")
LEXICAL_ROUTES = ("production", "keyword")
DUAL_POLICIES = (
    "dual_dense30_lexical10_per_type",
    "dual_dense20_lexical20_per_type",
)
RERANK_POLICIES = ("dense_40_per_type", *DUAL_POLICIES)
CANDIDATE_POLICIES = ("dense_20_per_type", *RERANK_POLICIES)
TOP_K_VALUES = (1, 3, 5, 10, 20, 40)
ROUTE_TOP_K = 30
RRF_K = 60
SMALL_QUOTA = 20
CONTROL_QUOTA = 40
BM25_K1 = 1.2
BM25_B = 0.75


@dataclass(frozen=True)
class PreparedLexicalDoc:
    doc: ChunkDoc
    field_counts: dict[str, dict[str, int]]
    field_lengths: dict[str, int]
    text_sha256: str


@dataclass(frozen=True)
class LexicalIndex:
    docs: list[PreparedLexicalDoc]
    idf: dict[str, float]
    average_field_lengths: dict[str, float]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def token_counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for token in tokens:
        counts[token] += 1
    return dict(counts)


def build_lexical_index(docs: list[ChunkDoc]) -> LexicalIndex:
    fields = active_weights(include_llm_suggestions=False)
    prepared = []
    doc_frequency: dict[str, int] = defaultdict(int)
    total_lengths: dict[str, int] = defaultdict(int)
    for doc in docs:
        field_counts = {}
        field_lengths = {}
        seen: set[str] = set()
        for field in fields:
            counts = token_counts(tokens_for_search(doc.fields.get(field, "")))
            field_counts[field] = counts
            field_lengths[field] = sum(counts.values())
            total_lengths[field] += field_lengths[field]
            seen.update(counts)
        for token in seen:
            doc_frequency[token] += 1
        prepared.append(
            PreparedLexicalDoc(
                doc=doc,
                field_counts=field_counts,
                field_lengths=field_lengths,
                text_sha256=chunk_text_sha256(doc.fields.get("text", "")),
            )
        )
    total = len(prepared)
    idf = {
        token: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
        for token, frequency in doc_frequency.items()
    }
    average_lengths = {
        field: total_lengths[field] / total if total else 0.0 for field in fields
    }
    return LexicalIndex(prepared, idf, average_lengths)


def score_bm25(index: LexicalIndex, query: str) -> list[tuple[float, PreparedLexicalDoc]]:
    query_tokens = list(dict.fromkeys(tokens_for_search(query)))
    weights = active_weights(include_llm_suggestions=False)
    scored = []
    for prepared in index.docs:
        score = 0.0
        for field, weight in weights.items():
            counts = prepared.field_counts[field]
            length = prepared.field_lengths[field]
            average_length = index.average_field_lengths[field]
            length_ratio = length / average_length if average_length else 0.0
            for token in query_tokens:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio)
                score += weight * index.idf.get(token, 0.0) * tf * (BM25_K1 + 1) / denominator
        if score > 0:
            scored.append((score, prepared))
    scored.sort(key=lambda item: (-item[0], item[1].doc.chunk_id))
    return scored


def embed_queries(queries: list[str]) -> dict[str, list[float]]:
    vectors = []
    for start in range(0, len(queries), embeddings.MAX_BATCH_SIZE):
        batch = queries[start:start + embeddings.MAX_BATCH_SIZE]
        print(f"embedding queries {start + 1}-{start + len(batch)}/{len(queries)}", flush=True)
        vectors.extend(
            embeddings.embed_queries_with_dashscope(
                batch,
                model=embeddings.DEFAULT_MODEL,
                dimension=embeddings.DEFAULT_DIMENSION,
            )
        )
    return dict(zip(queries, vectors, strict=True))


def dense_ranking(
    routes: dict[str, str],
    query_vectors: dict[str, list[float]],
    content_type: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route in DENSE_ROUTES:
        query = routes[route]
        result = embeddings.vector_search_by_vector(
            query,
            query_vectors[query],
            top_k=ROUTE_TOP_K,
            content_type=content_type,
            model=embeddings.DEFAULT_MODEL,
            dimension=embeddings.DEFAULT_DIMENSION,
        )
        for rank, hit in enumerate(result["hits"], start=1):
            candidate = merged.setdefault(
                hit["chunk_id"],
                candidate_from_hit(hit, content_type=content_type, source="dense"),
            )
            candidate["source_ranks"][f"dense:{route}"] = rank
            candidate["source_scores"][f"dense:{route}"] = float(hit["score"])
            candidate["prior_score"] += 1 / (RRF_K + rank)
    return sort_candidates(merged.values())


def lexical_ranking(
    routes: dict[str, str],
    index: LexicalIndex,
    content_type: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route in LEXICAL_ROUTES:
        for rank, (score, prepared) in enumerate(
            score_bm25(index, routes[route])[:ROUTE_TOP_K], start=1
        ):
            doc = prepared.doc
            candidate = merged.setdefault(
                doc.chunk_id,
                candidate_from_lexical_doc(
                    prepared,
                    content_type=content_type,
                    score=score,
                ),
            )
            candidate["source_ranks"][f"lexical:{route}"] = rank
            candidate["source_scores"][f"lexical:{route}"] = score
            candidate["prior_score"] += 1 / (RRF_K + rank)
    return sort_candidates(merged.values())


def candidate_from_hit(hit: dict[str, Any], *, content_type: str, source: str) -> dict[str, Any]:
    return {
        "hit": dict(hit),
        "content_type": content_type,
        "sources": [source],
        "source_ranks": {},
        "source_scores": {},
        "prior_score": 0.0,
    }


def candidate_from_lexical_doc(
    prepared: PreparedLexicalDoc,
    *,
    content_type: str,
    score: float,
) -> dict[str, Any]:
    doc = prepared.doc
    hit = {
        "chunk_id": doc.chunk_id,
        "score": score,
        "file_id": "",
        "file_name": doc.file_name,
        "page": doc.page,
        "crop_url": None,
        "text": doc.fields.get("text", ""),
        "business_metadata": doc.metadata,
        "source_trace": {},
    }
    return candidate_from_hit(hit, content_type=content_type, source="lexical")


def sort_candidates(candidates: Any) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            -item["prior_score"],
            -max(item["source_scores"].values(), default=0.0),
            item["hit"]["chunk_id"],
        ),
    )


def merge_candidate_lists(*candidate_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for candidates in candidate_lists:
        for candidate in candidates:
            chunk_id = candidate["hit"]["chunk_id"]
            if chunk_id not in merged:
                merged[chunk_id] = {
                    **candidate,
                    "sources": list(candidate["sources"]),
                    "source_ranks": dict(candidate["source_ranks"]),
                    "source_scores": dict(candidate["source_scores"]),
                }
                order.append(chunk_id)
                continue
            current = merged[chunk_id]
            current["sources"] = list(dict.fromkeys([*current["sources"], *candidate["sources"]]))
            current["source_ranks"].update(candidate["source_ranks"])
            current["source_scores"].update(candidate["source_scores"])
            current["prior_score"] += candidate["prior_score"]
    return [merged[chunk_id] for chunk_id in order]


def build_candidate_policies(
    routes: dict[str, str],
    query_vectors: dict[str, list[float]],
    lexical_indexes: dict[str, LexicalIndex],
) -> dict[str, list[dict[str, Any]]]:
    by_policy: dict[str, list[dict[str, Any]]] = {policy: [] for policy in CANDIDATE_POLICIES}
    for content_type in CONTENT_TYPES:
        dense = dense_ranking(routes, query_vectors, content_type)
        lexical = lexical_ranking(routes, lexical_indexes[content_type], content_type)
        by_policy["dense_20_per_type"].extend(dense[:SMALL_QUOTA])
        by_policy["dense_40_per_type"].extend(dense[:CONTROL_QUOTA])
        by_policy["dual_dense30_lexical10_per_type"].extend(
            merge_candidate_lists(dense[:30], lexical[:10])
        )
        by_policy["dual_dense20_lexical20_per_type"].extend(
            merge_candidate_lists(dense[:SMALL_QUOTA], lexical[:SMALL_QUOTA])
        )
    return by_policy


def required_group_hashes(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "group_id": group["group_id"],
            "target_hashes": sorted({
                alternative["locator"]["text_sha256"]
                for alternative in group["alternatives"]
            }),
        }
        for group in case["required_evidence_groups"]
    ]


def score_candidates(
    candidates: list[dict[str, Any]], groups: list[dict[str, Any]]
) -> dict[str, Any]:
    hashes = {chunk_text_sha256(candidate["hit"].get("text")) for candidate in candidates}
    group_results = [
        {
            "group_id": group["group_id"],
            "recalled": bool(set(group["target_hashes"]) & hashes),
        }
        for group in groups
    ]
    return {
        "candidate_count": len(candidates),
        "recalled_groups": sum(group["recalled"] for group in group_results),
        "required_groups": len(group_results),
        "strict_complete": all(group["recalled"] for group in group_results),
        "groups": group_results,
        "source_counts": {
            source: sum(source in candidate["sources"] for candidate in candidates)
            for source in ("dense", "lexical")
        },
    }


def rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    documents = [retrieval._rerank_document(candidate) for candidate in candidates]
    reranked = retrieval.rerank_documents(query, documents, min(max(TOP_K_VALUES), len(documents)))
    hits = []
    for index, score in reranked:
        candidate = candidates[index]
        hits.append({
            **candidate["hit"],
            "rerank_score": score,
            "sources": candidate["sources"],
            "source_ranks": candidate["source_ranks"],
        })
    ranks = {chunk_text_sha256(hit.get("text")): rank for rank, hit in enumerate(hits, start=1)}
    group_results = []
    for group in groups:
        matched = sorted(
            ranks[text_hash] for text_hash in group["target_hashes"] if text_hash in ranks
        )
        group_results.append({
            "group_id": group["group_id"],
            "best_rank": matched[0] if matched else None,
        })
    return {
        "groups": group_results,
        "top_hits": [summarize_hit(rank, hit) for rank, hit in enumerate(hits[:10], start=1)],
    }


def summarize_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "rank": rank,
        "chunk_id": hit["chunk_id"],
        "rerank_score": hit.get("rerank_score"),
        "sources": hit.get("sources"),
        "source_ranks": hit.get("source_ranks"),
        "file_name": hit.get("file_name"),
        "page": hit.get("page"),
        "standard_no": metadata.get("standard_no"),
        "content_type": metadata.get("content_type"),
        "section": metadata.get("section"),
        "section_title": metadata.get("section_title"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
    }


def aggregate_candidate_policy(cases: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    recalled = sum(case["candidates"][policy]["recalled_groups"] for case in cases)
    required = sum(case["candidates"][policy]["required_groups"] for case in cases)
    strict = sum(case["candidates"][policy]["strict_complete"] for case in cases)
    return {
        "cases": len(cases),
        "average_candidates": (
            sum(case["candidates"][policy]["candidate_count"] for case in cases) / len(cases)
            if cases else 0.0
        ),
        "required_groups": required,
        "recalled_groups": recalled,
        "evidence_group_recall": ratio(recalled, required),
        "strict_complete_hits": strict,
        "strict_complete_recall": ratio(strict, len(cases)),
    }


def aggregate_rerank_policy(cases: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    output = {}
    for top_k in TOP_K_VALUES:
        recalled = strict = reciprocal_rank = 0
        required = sum(len(case["reranked"][policy]["groups"]) for case in cases)
        for case in cases:
            groups = case["reranked"][policy]["groups"]
            hits = sum(
                group["best_rank"] is not None and group["best_rank"] <= top_k
                for group in groups
            )
            recalled += hits
            strict += int(hits == len(groups))
            reciprocal_rank += sum(
                1 / group["best_rank"]
                for group in groups
                if group["best_rank"] is not None and group["best_rank"] <= top_k
            )
        output[str(top_k)] = {
            "cases": len(cases),
            "required_groups": required,
            "recalled_groups": recalled,
            "evidence_group_recall": ratio(recalled, required),
            "strict_complete_hits": strict,
            "strict_complete_recall": ratio(strict, len(cases)),
            "group_mrr": ratio(reciprocal_rank, required),
        }
    return output


def paired_candidate_comparison(
    cases: list[dict[str, Any]], treatment: str
) -> dict[str, int]:
    wins = losses = both_hit = both_miss = 0
    for case in cases:
        baseline = case["candidates"]["dense_40_per_type"]["strict_complete"]
        treatment_hit = case["candidates"][treatment]["strict_complete"]
        if treatment_hit and not baseline:
            wins += 1
        elif baseline and not treatment_hit:
            losses += 1
        elif treatment_hit:
            both_hit += 1
        else:
            both_miss += 1
    return {
        "wins": wins,
        "losses": losses,
        "net_strict_hits": wins - losses,
        "both_hit": both_hit,
        "both_miss": both_miss,
    }


def paired_rerank_comparison(
    cases: list[dict[str, Any]], treatment: str
) -> dict[str, Any]:
    output = {}
    for top_k in TOP_K_VALUES:
        wins = losses = both_hit = both_miss = 0
        for case in cases:
            values = {}
            for policy in RERANK_POLICIES:
                groups = case["reranked"][policy]["groups"]
                values[policy] = all(
                    group["best_rank"] is not None and group["best_rank"] <= top_k
                    for group in groups
                )
            baseline = values["dense_40_per_type"]
            treatment_hit = values[treatment]
            if treatment_hit and not baseline:
                wins += 1
            elif baseline and not treatment_hit:
                losses += 1
            elif treatment_hit:
                both_hit += 1
            else:
                both_miss += 1
        output[str(top_k)] = {
            "wins": wins,
            "losses": losses,
            "net_strict_hits": wins - losses,
            "both_hit": both_hit,
            "both_miss": both_miss,
        }
    return output


def ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_recall": {
            policy: aggregate_candidate_policy(cases, policy) for policy in CANDIDATE_POLICIES
        },
        "reranked": {
            policy: aggregate_rerank_policy(cases, policy) for policy in RERANK_POLICIES
        },
        "paired_dual_vs_dense40": {
            treatment: {
                "candidates": paired_candidate_comparison(cases, treatment),
                "reranked": paired_rerank_comparison(cases, treatment),
            }
            for treatment in DUAL_POLICIES
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Dense + Lexical Retrieval v2 Ablation",
        "",
        "> Candidate benchmark only: evidence relations remain pending domain review.",
        "",
        "## Candidate Recall",
        "",
        "| Policy | Avg candidates | Evidence groups | Strict cases |",
        "|---|---:|---:|---:|",
    ]
    for policy in CANDIDATE_POLICIES:
        item = report["summary"]["candidate_recall"][policy]
        lines.append(
            f"| {policy} | {item['average_candidates']:.1f} | "
            f"{item['evidence_group_recall']:.1%} "
            f"({item['recalled_groups']}/{item['required_groups']}) | "
            f"{item['strict_complete_recall']:.1%} "
            f"({item['strict_complete_hits']}/{item['cases']}) |"
        )
    lines.extend([
        "",
        "## Reranked Results",
        "",
        "| Policy | K | Evidence groups | Strict cases | Group MRR |",
        "|---|---:|---:|---:|---:|",
    ])
    for policy in RERANK_POLICIES:
        for top_k in (5, 10, 20, 40):
            item = report["summary"]["reranked"][policy][str(top_k)]
            lines.append(
                f"| {policy} | {top_k} | {item['evidence_group_recall']:.1%} "
                f"({item['recalled_groups']}/{item['required_groups']}) | "
                f"{item['strict_complete_recall']:.1%} "
                f"({item['strict_complete_hits']}/{item['cases']}) | "
                f"{item['group_mrr']:.3f} |"
            )
    lines.extend(["", "## Paired Dual vs Dense-40", ""])
    for treatment in DUAL_POLICIES:
        paired = report["summary"]["paired_dual_vs_dense40"][treatment]
        lines.append(
            f"- `{treatment}` candidate wins/losses: "
            f"`{paired['candidates']['wins']}` / `{paired['candidates']['losses']}`; "
            f"reranked Top-10: `{paired['reranked']['10']['wins']}` / "
            f"`{paired['reranked']['10']['losses']}`."
        )
    lines.append("")
    return "\n".join(lines)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    ground_truth = read_json(args.ground_truth.resolve())
    query_payload = read_json(args.queries.resolve())
    eligible = [
        case for case in ground_truth["cases"]
        if case.get("answerability_status") == "answerable_candidate"
    ]
    queries_by_id = query_payload["cases"]

    load_domain_terms(args.terms.resolve())
    lexical_indexes = {
        content_type: build_lexical_index(
            load_docs(
                args.db.resolve(),
                content_type=content_type,
                include_llm_suggestions=False,
            )
        )
        for content_type in CONTENT_TYPES
    }
    query_texts = list(dict.fromkeys(
        queries_by_id[case["case_id"]][route]
        for case in eligible
        for route in DENSE_ROUTES
    ))
    query_vectors = embed_queries(query_texts)

    details = []
    errors = []
    for index, case in enumerate(eligible, start=1):
        case_id = case["case_id"]
        print(f"evaluating {index}/{len(eligible)} {case_id}", flush=True)
        routes = {route: queries_by_id[case_id][route] for route in DENSE_ROUTES}
        groups = required_group_hashes(case)
        try:
            candidate_policies = build_candidate_policies(routes, query_vectors, lexical_indexes)
            candidate_scores = {
                policy: score_candidates(candidates, groups)
                for policy, candidates in candidate_policies.items()
            }
            reranked = {}
            timing = {}
            for policy in RERANK_POLICIES:
                started = time.perf_counter()
                reranked[policy] = rerank_candidates(
                    routes["production"], candidate_policies[policy], groups
                )
                timing[policy] = time.perf_counter() - started
            details.append({
                "case_id": case_id,
                "dataset_split": case["dataset_split"],
                "retrieval_class": case["retrieval_class"],
                "queries": routes,
                "required_groups": groups,
                "candidates": candidate_scores,
                "reranked": reranked,
                "rerank_seconds": timing,
            })
        except Exception as exc:
            errors.append({
                "case_id": case_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            print(f"  failed: {type(exc).__name__}: {exc}", flush=True)

    return {
        "version": 2,
        "status": "complete" if not errors and len(details) == len(eligible) else "complete_with_errors",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline_no_production_changes",
        "gold_status": ground_truth.get("status"),
        "ground_truth": str(args.ground_truth.resolve().relative_to(ROOT)),
        "queries": str(args.queries.resolve().relative_to(ROOT)),
        "query_routes": {
            "dense": list(DENSE_ROUTES),
            "lexical": list(LEXICAL_ROUTES),
            "excluded": ["table_target", "section_target"],
        },
        "lexical": {
            "method": "jieba_pre_tokenized_field_weighted_bm25",
            "field_weights": active_weights(include_llm_suggestions=False),
            "k1": BM25_K1,
            "b": BM25_B,
            "terms": str(args.terms.resolve().relative_to(ROOT)),
        },
        "candidate_budgets": {
            "dense_20_per_type": "20 dense candidates per content type; current production budget.",
            "dense_40_per_type": "40 dense candidates per content type; equal-budget control.",
            "dual_dense30_lexical10_per_type": (
                "30 dense plus 10 lexical candidates per content type before deduplication."
            ),
            "dual_dense20_lexical20_per_type": (
                "20 dense plus 20 lexical candidates per content type before deduplication."
            ),
        },
        "progress": {
            "completed_cases": len(details),
            "total_cases": len(eligible),
            "errors": len(errors),
        },
        "summary": build_summary(details),
        "errors": errors,
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--terms", type=Path, default=DEFAULT_TERMS)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    report = evaluate(args)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
