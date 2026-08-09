"""Compare current table vectors with metadata-and-header-only table vectors.

Treatment vectors are generated in memory and are never written to the database.
The benchmark uses only table alternatives from retrieval ground-truth v2, whose
relations are still pending domain review.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app import chunk_schema, db, embeddings  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402


DEFAULT_GROUND_TRUTH = ROOT / "evaluation" / "test_set.json"
DEFAULT_QUERIES = (
    ROOT
    / "evaluation"
    / "frozen"
    / "retrieval_eval_v1_candidate_2026-07-13"
    / "reports"
    / "retrieval_queries_40_v1.json"
)
DEFAULT_JSON = (
    ROOT / "backend" / "data" / "reports" / "table_header_only_dense_ablation_v2_2026-07-15.json"
)
DEFAULT_MD = (
    ROOT / "backend" / "data" / "reports" / "table_header_only_dense_ablation_v2_2026-07-15.md"
)
VARIANTS = ("baseline_content", "header_only")
POLICIES = ("dense_original", "three_route_rrf")
QUERY_ROUTES = ("production", "semantic", "keyword")
TOP_K_VALUES = (1, 3, 5, 10, 20, 40)
ROUTE_TOP_K = 30
RRF_K = 60


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def table_header_only_text(row: Any) -> str:
    """Build the proposed table document without any chunk body text."""
    business = chunk_schema.parse_json_object(row["business_metadata"])
    labels = [
        business.get("standard_no"),
        business.get("section"),
        business.get("section_title"),
        business.get("table_no"),
        business.get("table_title"),
    ]
    prefix = " | ".join(str(value).strip() for value in labels if str(value or "").strip())
    raw_columns = business.get("table_columns")
    columns = (
        [str(value).strip() for value in raw_columns if str(value).strip()]
        if isinstance(raw_columns, list)
        else []
    )
    header = "表头：" + " | ".join(columns) if columns else ""
    return "\n\n".join(part for part in (prefix, header) if part)


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    dimension = embeddings.DEFAULT_DIMENSION
    if len(blob) != dimension * 4:
        raise ValueError(f"unexpected embedding blob length: {len(blob)}")
    return struct.unpack(f"<{dimension}f", blob)


def normalize(vector: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise ValueError("embedding vector has zero norm")
    return tuple(value / norm for value in vector)


def load_table_corpus() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = db.get_conn().execute(
        """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.text,
                  c.business_metadata, e.embedding, e.text_sha256 AS embedding_text_sha256
           FROM chunks c
           JOIN files f ON f.id=c.file_id
           JOIN chunk_embeddings e ON e.chunk_id=c.id
           WHERE c.status='approved' AND e.model=? AND e.dimension=?
             AND json_extract(c.business_metadata, '$.content_type')='table'
           ORDER BY c.file_id, c.page, c.created_at""",
        (embeddings.DEFAULT_MODEL, embeddings.DEFAULT_DIMENSION),
    ).fetchall()
    documents = []
    stale_ids = []
    with_columns = 0
    for row in rows:
        base_document = embeddings.build_document(row)
        if row["embedding_text_sha256"] != base_document.text_sha256:
            stale_ids.append(str(row["id"]))
        business = chunk_schema.parse_json_object(row["business_metadata"])
        columns = business.get("table_columns")
        if isinstance(columns, list) and any(str(value).strip() for value in columns):
            with_columns += 1
        documents.append({
            "chunk_id": str(row["id"]),
            "file_name": str(row["file_name"]),
            "page": int(row["page"]),
            "text_sha256": chunk_text_sha256(row["text"]),
            "business_metadata": business,
            "baseline_vector": normalize(unpack_vector(row["embedding"])),
            "treatment_text": table_header_only_text(row),
        })
    if stale_ids:
        raise RuntimeError(f"{len(stale_ids)} baseline table embeddings are stale")
    if not documents:
        raise RuntimeError("no embedded approved table chunks found")
    return documents, {
        "approved_embedded_tables": len(documents),
        "tables_with_columns": with_columns,
        "table_columns_coverage": with_columns / len(documents),
    }


def embed_texts(texts: list[str], *, query: bool) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), embeddings.MAX_BATCH_SIZE):
        batch = texts[start:start + embeddings.MAX_BATCH_SIZE]
        print(
            f"embedding {'queries' if query else 'table documents'} "
            f"{start + 1}-{start + len(batch)}/{len(texts)}",
            flush=True,
        )
        if query:
            vectors.extend(
                embeddings.embed_queries_with_dashscope(
                    batch,
                    model=embeddings.DEFAULT_MODEL,
                    dimension=embeddings.DEFAULT_DIMENSION,
                )
            )
        else:
            batch_vectors, _ = embeddings.embed_with_dashscope(
                batch,
                model=embeddings.DEFAULT_MODEL,
                dimension=embeddings.DEFAULT_DIMENSION,
            )
            vectors.extend(batch_vectors)
    return vectors


def table_groups(case: dict[str, Any]) -> list[dict[str, Any]]:
    groups = []
    for group in case.get("required_evidence_groups", []):
        hashes = sorted({
            alternative["locator"]["text_sha256"]
            for alternative in group.get("alternatives", [])
            if alternative.get("locator", {}).get("content_type") == "table"
        })
        if hashes:
            groups.append({"group_id": group["group_id"], "target_hashes": hashes})
    return groups


def rank_documents(
    documents: list[dict[str, Any]],
    query_vector: tuple[float, ...],
    vector_field: str,
) -> list[dict[str, Any]]:
    scored = [
        (sum(a * b for a, b in zip(query_vector, document[vector_field], strict=True)), index)
        for index, document in enumerate(documents)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [dict(score=score, **documents[index]) for score, index in scored]


def rrf_rank(route_rankings: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    documents: dict[str, dict[str, Any]] = {}
    for ranking in route_rankings:
        for rank, document in enumerate(ranking[:ROUTE_TOP_K], start=1):
            chunk_id = document["chunk_id"]
            documents[chunk_id] = document
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
    return [
        dict(rrf_score=scores[chunk_id], **documents[chunk_id])
        for chunk_id in sorted(scores, key=lambda value: (-scores[value], value))
    ]


def score_groups(hits: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = {hit["text_sha256"]: rank for rank, hit in enumerate(hits, start=1)}
    group_results = []
    for group in groups:
        matching = sorted(
            (ranks[text_hash], text_hash)
            for text_hash in group["target_hashes"]
            if text_hash in ranks
        )
        group_results.append({
            "group_id": group["group_id"],
            "best_rank": matching[0][0] if matching else None,
            "matched_hash": matching[0][1] if matching else None,
        })
    return {
        "groups": group_results,
        "top_hits": [summarize_hit(rank, hit) for rank, hit in enumerate(hits[:10], start=1)],
    }


def summarize_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit["business_metadata"]
    return {
        "rank": rank,
        "score": hit.get("score"),
        "rrf_score": hit.get("rrf_score"),
        "text_sha256": hit["text_sha256"],
        "file_name": hit["file_name"],
        "page": hit["page"],
        "standard_no": metadata.get("standard_no"),
        "table_no": metadata.get("table_no"),
        "table_title": metadata.get("table_title"),
        "table_columns": metadata.get("table_columns"),
    }


def aggregate_cases(cases: list[dict[str, Any]], variant: str, policy: str) -> dict[str, Any]:
    metrics = {}
    for top_k in TOP_K_VALUES:
        recalled_groups = complete_cases = 0
        reciprocal_rank = 0.0
        group_count = sum(case["table_group_count"] for case in cases)
        for case in cases:
            groups = case["rankings"][variant][policy]["groups"]
            hit_count = sum(
                group["best_rank"] is not None and group["best_rank"] <= top_k
                for group in groups
            )
            recalled_groups += hit_count
            complete_cases += int(hit_count == len(groups))
            reciprocal_rank += sum(
                1 / group["best_rank"]
                for group in groups
                if group["best_rank"] is not None and group["best_rank"] <= top_k
            )
        metrics[str(top_k)] = {
            "cases": len(cases),
            "complete_case_hits": complete_cases,
            "complete_case_recall": ratio(complete_cases, len(cases)),
            "table_groups": group_count,
            "recalled_table_groups": recalled_groups,
            "table_group_recall": ratio(recalled_groups, group_count),
            "table_group_mrr": ratio(reciprocal_rank, group_count),
        }
    return metrics


def paired_comparison(cases: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    output = {}
    for top_k in TOP_K_VALUES:
        wins = losses = both_hit = both_miss = 0
        rank_improvements = []
        for case in cases:
            baseline_groups = case["rankings"]["baseline_content"][policy]["groups"]
            treatment_groups = case["rankings"]["header_only"][policy]["groups"]
            baseline_hit = all(
                group["best_rank"] is not None and group["best_rank"] <= top_k
                for group in baseline_groups
            )
            treatment_hit = all(
                group["best_rank"] is not None and group["best_rank"] <= top_k
                for group in treatment_groups
            )
            if treatment_hit and not baseline_hit:
                wins += 1
            elif baseline_hit and not treatment_hit:
                losses += 1
            elif treatment_hit:
                both_hit += 1
            else:
                both_miss += 1
            for baseline_group, treatment_group in zip(
                baseline_groups, treatment_groups, strict=True
            ):
                if baseline_group["best_rank"] is not None and treatment_group["best_rank"] is not None:
                    rank_improvements.append(
                        baseline_group["best_rank"] - treatment_group["best_rank"]
                    )
        output[str(top_k)] = {
            "wins": wins,
            "losses": losses,
            "net_complete_case_hits": wins - losses,
            "both_hit": both_hit,
            "both_miss": both_miss,
            "mean_table_group_rank_improvement": (
                sum(rank_improvements) / len(rank_improvements) if rank_improvements else None
            ),
        }
    return output


def ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    lines = [
        "# Table Header-Only Dense Ablation",
        "",
        "> Candidate benchmark only: evidence relations are corpus-verified but pending domain review.",
        "",
        "## Scope",
        "",
        f"- Embedded approved tables: `{coverage['approved_embedded_tables']}`",
        f"- Tables with `table_columns`: `{coverage['tables_with_columns']}` "
        f"(`{coverage['table_columns_coverage']:.1%}`)",
        f"- Evaluated cases: `{coverage['evaluated_cases']}`",
        f"- Evaluated table evidence groups: `{coverage['evaluated_table_groups']}`",
        "- Baseline: current metadata prefix plus complete chunk content.",
        "- Treatment: standard/section/table metadata plus `table_columns`; chunk content excluded.",
        "",
        "## Results",
        "",
        "| Policy | Variant | K | Complete cases | Table groups | Group MRR |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        for variant in VARIANTS:
            for top_k in (5, 10, 20, 40):
                item = report["summary"]["by_policy"][policy][variant][str(top_k)]
                lines.append(
                    f"| {policy} | {variant} | {top_k} | "
                    f"{item['complete_case_recall']:.1%} "
                    f"({item['complete_case_hits']}/{item['cases']}) | "
                    f"{item['table_group_recall']:.1%} "
                    f"({item['recalled_table_groups']}/{item['table_groups']}) | "
                    f"{item['table_group_mrr']:.3f} |"
                )
    lines.extend([
        "",
        "## Header-Only vs Baseline at K=10",
        "",
        "| Policy | Wins | Losses | Net complete cases | Mean group rank improvement |",
        "|---|---:|---:|---:|---:|",
    ])
    for policy in POLICIES:
        item = report["summary"]["paired_header_only_vs_baseline"][policy]["10"]
        lines.append(
            f"| {policy} | {item['wins']} | {item['losses']} | "
            f"{item['net_complete_case_hits']} | "
            f"{item['mean_table_group_rank_improvement']:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    ground_truth = read_json(args.ground_truth.resolve())
    query_payload = read_json(args.queries.resolve())
    queries_by_id = query_payload["cases"]

    documents, coverage = load_table_corpus()
    corpus_hashes = {document["text_sha256"] for document in documents}
    eligible = []
    missing_targets = []
    for case in ground_truth["cases"]:
        if case.get("evaluation_scope") != "retrieval":
            continue
        groups = table_groups(case)
        if not groups:
            continue
        for group in groups:
            if not set(group["target_hashes"]) & corpus_hashes:
                missing_targets.append({
                    "case_id": case["case_id"],
                    "group_id": group["group_id"],
                    "target_hashes": group["target_hashes"],
                })
        eligible.append({
            "case_id": case["case_id"],
            "dataset_split": case["dataset_split"],
            "retrieval_class": case["retrieval_class"],
            "groups": groups,
        })
    if missing_targets:
        raise RuntimeError(f"{len(missing_targets)} table evidence groups do not resolve in corpus")

    treatment_vectors = embed_texts(
        [document["treatment_text"] for document in documents], query=False
    )
    for document, vector in zip(documents, treatment_vectors, strict=True):
        document["header_only_vector"] = normalize(vector)

    query_texts = list(dict.fromkeys(
        queries_by_id[case["case_id"]][route]
        for case in eligible
        for route in QUERY_ROUTES
    ))
    query_vectors = {
        text: normalize(vector)
        for text, vector in zip(query_texts, embed_texts(query_texts, query=True), strict=True)
    }

    details = []
    for index, case in enumerate(eligible, start=1):
        case_id = case["case_id"]
        print(f"evaluating {index}/{len(eligible)} {case_id}", flush=True)
        routes = queries_by_id[case_id]
        rankings: dict[str, Any] = {}
        for variant, vector_field in (
            ("baseline_content", "baseline_vector"),
            ("header_only", "header_only_vector"),
        ):
            route_rankings = {
                route: rank_documents(documents, query_vectors[routes[route]], vector_field)
                for route in QUERY_ROUTES
            }
            rankings[variant] = {
                "dense_original": score_groups(route_rankings["production"], case["groups"]),
                "three_route_rrf": score_groups(
                    rrf_rank([route_rankings[route] for route in QUERY_ROUTES]), case["groups"]
                ),
            }
        details.append({
            "case_id": case_id,
            "dataset_split": case["dataset_split"],
            "retrieval_class": case["retrieval_class"],
            "queries": {route: routes[route] for route in QUERY_ROUTES},
            "table_group_count": len(case["groups"]),
            "table_groups": case["groups"],
            "rankings": rankings,
        })

    coverage.update({
        "evaluated_cases": len(details),
        "evaluated_table_groups": sum(case["table_group_count"] for case in details),
    })
    summary = {
        "by_policy": {
            policy: {
                variant: aggregate_cases(details, variant, policy) for variant in VARIANTS
            }
            for policy in POLICIES
        },
        "paired_header_only_vs_baseline": {
            policy: paired_comparison(details, policy) for policy in POLICIES
        },
    }
    return {
        "version": 2,
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth": str(args.ground_truth.resolve().relative_to(ROOT)),
        "queries": str(args.queries.resolve().relative_to(ROOT)),
        "gold_status": ground_truth.get("status"),
        "scope": "temporary_in_memory_vectors_no_database_writes",
        "model": embeddings.DEFAULT_MODEL,
        "dimension": embeddings.DEFAULT_DIMENSION,
        "variants": {
            "baseline_content": "Current production table embedding document.",
            "header_only": (
                "standard_no, section, section_title, table_no, table_title, and table_columns; "
                "chunk content excluded."
            ),
        },
        "retrieval_policies": {
            "dense_original": "Production query against all approved table chunks.",
            "three_route_rrf": {
                "routes": list(QUERY_ROUTES),
                "route_top_k": ROUTE_TOP_K,
                "rrf_k": RRF_K,
                "excluded_routes": ["table_target", "section_target"],
            },
        },
        "metric_semantics": (
            "Only required evidence groups containing table alternatives are scored. "
            "A group is recalled when any table alternative is returned."
        ),
        "coverage": coverage,
        "summary": summary,
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    db.init_db()
    report = evaluate(args)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
