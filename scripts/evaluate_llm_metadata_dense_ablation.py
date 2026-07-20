"""Compare dense retrieval with and without current LLM keyword/question metadata.

The treatment vectors are generated in memory and never written to chunk_embeddings.
This is a directional experiment against candidate gold, not a final benchmark.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import heapq
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
from app.evidence_locator import chunk_text_sha256, resolve_evidence_locator  # noqa: E402


DEFAULT_FREEZE = ROOT / "evaluation" / "frozen" / "retrieval_eval_v1_candidate_2026-07-13"
DEFAULT_QUERIES = DEFAULT_FREEZE / "reports" / "retrieval_queries_40_v1.json"
DEFAULT_JSON = ROOT / "backend" / "data" / "reports" / "llm_metadata_dense_retrieval_ablation_2026-07-15.json"
DEFAULT_MD = ROOT / "backend" / "data" / "reports" / "llm_metadata_dense_retrieval_ablation_2026-07-15.md"
DEFAULT_PROMPT_VERSION = "power_standard_keywords_questions_v1"
VARIANTS = ("baseline", "keywords", "keywords_questions")
TOP_K_VALUES = (1, 3, 5, 10, 20, 40)
MAX_TOP_K = max(TOP_K_VALUES)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def suggestion_items(metadata_llm: dict[str, Any], field: str, prompt_version: str) -> list[str]:
    suggestion = metadata_llm.get(field)
    if not isinstance(suggestion, dict) or suggestion.get("prompt_version") != prompt_version:
        return []
    value = suggestion.get("value")
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def augmented_document_text(
    base_text: str,
    *,
    keywords: list[str],
    questions: list[str],
    include_questions: bool,
) -> str:
    sections = [base_text]
    if keywords:
        sections.append("检索关键词：" + "；".join(keywords))
    if include_questions and questions:
        sections.append("可回答问题：\n" + "\n".join(f"- {question}" for question in questions))
    return "\n\n".join(section for section in sections if section)


def load_corpus(prompt_version: str) -> tuple[list[dict[str, Any]], set[str]]:
    rows = db.get_conn().execute(
        """SELECT c.id, c.file_id, f.name AS file_name, c.page, c.text,
                  c.business_metadata, c.metadata_llm, e.embedding
           FROM chunks c
           JOIN files f ON f.id=c.file_id
           JOIN chunk_embeddings e ON e.chunk_id=c.id
           WHERE c.status='approved' AND e.model=? AND e.dimension=?
           ORDER BY c.file_id, c.page, c.created_at""",
        (embeddings.DEFAULT_MODEL, embeddings.DEFAULT_DIMENSION),
    ).fetchall()
    documents = []
    metadata_ids: set[str] = set()
    for row in rows:
        metadata_llm = chunk_schema.parse_json_object(row["metadata_llm"])
        keywords = suggestion_items(metadata_llm, "keywords", prompt_version)
        questions = suggestion_items(metadata_llm, "questions", prompt_version)
        source_hash = ""
        for field in ("questions", "keywords"):
            value = metadata_llm.get(field)
            if isinstance(value, dict) and value.get("prompt_version") == prompt_version:
                source_hash = str(value.get("source_text_sha256") or "")
                if source_hash:
                    break
        current = bool(keywords and questions and source_hash == chunk_text_sha256(row["text"]))
        if current:
            metadata_ids.add(str(row["id"]))
        base_document = embeddings.build_document(row).text
        documents.append({
            "chunk_id": str(row["id"]),
            "file_id": str(row["file_id"]),
            "file_name": str(row["file_name"]),
            "page": int(row["page"]),
            "text": str(row["text"] or ""),
            "text_sha256": chunk_text_sha256(row["text"]),
            "business_metadata": chunk_schema.parse_json_object(row["business_metadata"]),
            "base_document": base_document,
            "keywords": keywords if current else [],
            "questions": questions if current else [],
            "baseline_vector": unpack_vector(row["embedding"]),
        })
    return documents, metadata_ids


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    dimension = embeddings.DEFAULT_DIMENSION
    if len(blob) != dimension * 4:
        raise ValueError(f"unexpected embedding blob length: {len(blob)}")
    return struct.unpack(f"<{dimension}f", blob)


def embed_treatments(
    documents: list[dict[str, Any]],
    metadata_ids: set[str],
) -> dict[str, dict[str, list[float]]]:
    selected = [document for document in documents if document["chunk_id"] in metadata_ids]
    overrides: dict[str, dict[str, list[float]]] = {"keywords": {}, "keywords_questions": {}}
    for variant, include_questions in (("keywords", False), ("keywords_questions", True)):
        texts = [
            augmented_document_text(
                document["base_document"],
                keywords=document["keywords"],
                questions=document["questions"],
                include_questions=include_questions,
            )
            for document in selected
        ]
        vectors = []
        for start in range(0, len(texts), embeddings.MAX_BATCH_SIZE):
            batch_vectors, _ = embeddings.embed_with_dashscope(
                texts[start:start + embeddings.MAX_BATCH_SIZE],
                model=embeddings.DEFAULT_MODEL,
                dimension=embeddings.DEFAULT_DIMENSION,
            )
            vectors.extend(batch_vectors)
        overrides[variant] = {
            document["chunk_id"]: vector
            for document, vector in zip(selected, vectors, strict=True)
        }
    return overrides


def embed_queries(queries: list[str]) -> dict[str, list[float]]:
    vectors = []
    for start in range(0, len(queries), embeddings.MAX_BATCH_SIZE):
        vectors.extend(
            embeddings.embed_queries_with_dashscope(
                queries[start:start + embeddings.MAX_BATCH_SIZE],
                model=embeddings.DEFAULT_MODEL,
                dimension=embeddings.DEFAULT_DIMENSION,
            )
        )
    return dict(zip(queries, vectors, strict=True))


def resolve_direct_gold(
    gold_cases: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    conn = db.get_conn()
    by_case: dict[str, list[dict[str, Any]]] = {}
    unresolved = []
    for case in gold_cases:
        resolved = []
        for evidence in case.get("selected_evidence", []):
            if evidence.get("label") != "direct_candidate":
                continue
            matches = resolve_evidence_locator(conn, evidence["locator"])
            if len(matches) != 1:
                unresolved.append({
                    "case_id": case["case_id"],
                    "match_count": len(matches),
                    "locator": evidence["locator"],
                })
                continue
            resolved.append({
                "chunk_id": matches[0]["current_chunk_id"],
                "text_sha256": evidence["locator"]["text_sha256"],
            })
        by_case[case["case_id"]] = resolved
    return by_case, unresolved


def search_documents(
    documents: list[dict[str, Any]],
    query_vector: list[float],
    *,
    overrides: dict[str, list[float]] | None = None,
    top_k: int = MAX_TOP_K,
) -> list[dict[str, Any]]:
    query_norm = vector_norm(query_vector)
    scored = []
    overrides = overrides or {}
    for index, document in enumerate(documents):
        vector = overrides.get(document["chunk_id"], document["baseline_vector"])
        norm = vector_norm(vector)
        if not norm:
            continue
        score = sum(a * b for a, b in zip(query_vector, vector, strict=True)) / (query_norm * norm)
        scored.append((score, index))
    hits = []
    for score, index in heapq.nlargest(top_k, scored):
        document = documents[index]
        metadata = document["business_metadata"]
        hits.append({
            "chunk_id": document["chunk_id"],
            "text_sha256": document["text_sha256"],
            "score": score,
            "file_name": document["file_name"],
            "page": document["page"],
            "content_type": metadata.get("content_type"),
            "standard_no": metadata.get("standard_no"),
            "section": metadata.get("section"),
            "section_title": metadata.get("section_title"),
            "table_no": metadata.get("table_no"),
            "table_title": metadata.get("table_title"),
        })
    return hits


def vector_norm(vector: list[float] | tuple[float, ...]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def evaluate_ranking(hits: list[dict[str, Any]], direct_gold: list[dict[str, Any]]) -> dict[str, Any]:
    gold_hashes = {item["text_sha256"] for item in direct_gold}
    matched = [
        {"text_sha256": hit["text_sha256"], "chunk_id": hit["chunk_id"], "rank": rank}
        for rank, hit in enumerate(hits, start=1)
        if hit["text_sha256"] in gold_hashes
    ]
    return {
        "best_direct_rank": min((item["rank"] for item in matched), default=None),
        "matched_direct": matched,
        "top_hits": [dict(rank=rank, **hit) for rank, hit in enumerate(hits[:10], start=1)],
    }


def aggregate_cases(cases: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    eligible = [case for case in cases if case["direct_gold_count"] > 0]
    metrics: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        case_hits = 0
        evidence_hits = 0
        reciprocal_rank = 0.0
        direct_evidence = sum(case["direct_gold_count"] for case in eligible)
        for case in eligible:
            ranking = case["rankings"][variant]
            ranks = [item["rank"] for item in ranking["matched_direct"]]
            evidence_hits += sum(rank <= top_k for rank in ranks)
            best_rank = ranking["best_direct_rank"]
            if best_rank is not None and best_rank <= top_k:
                case_hits += 1
                reciprocal_rank += 1 / best_rank
        metrics[str(top_k)] = {
            "direct_gold_cases": len(eligible),
            "direct_case_hits": case_hits,
            "direct_case_recall": ratio(case_hits, len(eligible)),
            "direct_gold_evidence": direct_evidence,
            "direct_evidence_hits": evidence_hits,
            "direct_evidence_recall": ratio(evidence_hits, direct_evidence),
            "mrr": ratio(reciprocal_rank, len(eligible)),
        }
    return metrics


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    subsets = {
        "all_eligible": [case for case in cases if case["direct_gold_count"] > 0],
        "metadata_direct_overlap": [case for case in cases if case["metadata_direct_overlap"]],
        "without_metadata_direct_overlap": [
            case for case in cases if case["direct_gold_count"] > 0 and not case["metadata_direct_overlap"]
        ],
    }
    return {
        subset: {
            "by_variant": {variant: aggregate_cases(items, variant) for variant in VARIANTS},
            "by_split": {
                split: {variant: aggregate_cases(split_items, variant) for variant in VARIANTS}
                for split, split_items in grouped(items, "dataset_split").items()
            },
            "paired_vs_baseline": {
                variant: paired_comparison(items, variant) for variant in VARIANTS[1:]
            },
        }
        for subset, items in subsets.items()
    }


def paired_comparison(cases: list[dict[str, Any]], treatment: str) -> dict[str, Any]:
    output = {}
    for top_k in TOP_K_VALUES:
        wins = losses = both_hit = both_miss = 0
        rank_deltas = []
        for case in cases:
            baseline_rank = case["rankings"]["baseline"]["best_direct_rank"]
            treatment_rank = case["rankings"][treatment]["best_direct_rank"]
            baseline_hit = baseline_rank is not None and baseline_rank <= top_k
            treatment_hit = treatment_rank is not None and treatment_rank <= top_k
            if treatment_hit and not baseline_hit:
                wins += 1
            elif baseline_hit and not treatment_hit:
                losses += 1
            elif baseline_hit:
                both_hit += 1
            else:
                both_miss += 1
            if baseline_rank is not None and treatment_rank is not None:
                rank_deltas.append(baseline_rank - treatment_rank)
        output[str(top_k)] = {
            "wins": wins,
            "losses": losses,
            "net_case_hits": wins - losses,
            "both_hit": both_hit,
            "both_miss": both_miss,
            "mean_rank_improvement_when_both_in_top40": (
                sum(rank_deltas) / len(rank_deltas) if rank_deltas else None
            ),
        }
    return output


def grouped(items: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item.get(key) or "unknown")].append(item)
    return dict(sorted(groups.items()))


def ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LLM Metadata Dense Retrieval Ablation",
        "",
        f"Status: `{report['status']}`",
        "",
        "> Candidate gold remains pending domain review. This report isolates the effect of metadata vectors; it is not final business accuracy.",
        "",
        "## Coverage",
        "",
        f"- Current metadata chunks: `{report['coverage']['metadata_chunks']}`",
        f"- Eligible direct-gold cases: `{report['coverage']['eligible_direct_cases']}`",
        f"- Cases whose direct gold has metadata: `{report['coverage']['metadata_overlap_cases']}`",
        f"- Unique direct-gold chunks with metadata: `{report['coverage']['metadata_direct_gold_chunks']}`",
        "",
    ]
    for subset, title in (
        ("all_eligible", "All Eligible Cases"),
        ("metadata_direct_overlap", "Metadata Direct-Gold Overlap"),
    ):
        lines.extend([
            f"## {title}",
            "",
            "| Variant | K | Case recall | Evidence recall | MRR |",
            "|---|---:|---:|---:|---:|",
        ])
        summary = report["summary"][subset]["by_variant"]
        for variant in VARIANTS:
            for top_k in (5, 10, 20, 40):
                item = summary[variant][str(top_k)]
                lines.append(
                    f"| {variant} | {top_k} | {item['direct_case_recall']:.3f} "
                    f"({item['direct_case_hits']}/{item['direct_gold_cases']}) | "
                    f"{item['direct_evidence_recall']:.3f} | {item['mrr']:.3f} |"
                )
        lines.append("")
    lines.extend([
        "## Paired Changes At K=10",
        "",
        "| Subset | Treatment | Wins | Losses | Net |",
        "|---|---|---:|---:|---:|",
    ])
    for subset in ("all_eligible", "metadata_direct_overlap"):
        for variant in VARIANTS[1:]:
            item = report["summary"][subset]["paired_vs_baseline"][variant]["10"]
            lines.append(
                f"| {subset} | {variant} | {item['wins']} | {item['losses']} | {item['net_case_hits']} |"
            )
    lines.append("")
    return "\n".join(lines)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    freeze = args.freeze.resolve()
    case_payload = read_json(freeze / "retrieval_case_pool_v1.json")
    gold_payload = read_json(freeze / "retrieval_gold_candidates_v1.json")
    query_payload = read_json(args.queries.resolve())
    cases = case_payload["cases"]
    gold_by_id = {case["case_id"]: case for case in gold_payload["cases"]}
    queries_by_id = query_payload["cases"]

    documents, metadata_ids = load_corpus(args.prompt_version)
    direct_gold_by_case, unresolved = resolve_direct_gold(gold_payload["cases"])
    if unresolved:
        raise RuntimeError(f"{len(unresolved)} direct gold locators did not resolve uniquely")
    overrides = embed_treatments(documents, metadata_ids)
    production_queries = [queries_by_id[case["case_id"]]["production"] for case in cases]
    query_vectors = embed_queries(list(dict.fromkeys(production_queries)))

    details = []
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        print(f"evaluating {index}/{len(cases)} {case_id}", flush=True)
        query = queries_by_id[case_id]["production"]
        direct_gold = direct_gold_by_case[case_id]
        rankings = {}
        for variant in VARIANTS:
            variant_overrides = None if variant == "baseline" else overrides[variant]
            hits = search_documents(documents, query_vectors[query], overrides=variant_overrides)
            rankings[variant] = evaluate_ranking(hits, direct_gold)
        direct_ids = {item["chunk_id"] for item in direct_gold}
        details.append({
            "case_id": case_id,
            "dataset_split": case.get("dataset_split"),
            "retrieval_class": case.get("retrieval_class"),
            "expected_evidence_type": case.get("expected_evidence_type"),
            "production_query": query,
            "direct_gold_count": len(direct_gold),
            "metadata_direct_overlap": bool(direct_ids & metadata_ids),
            "metadata_direct_chunk_ids": sorted(direct_ids & metadata_ids),
            "rankings": rankings,
        })

    unique_direct_ids = {
        item["chunk_id"] for direct_gold in direct_gold_by_case.values() for item in direct_gold
    }
    overlap_cases = [case for case in details if case["metadata_direct_overlap"]]
    return {
        "version": 1,
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "freeze_dir": str(freeze.relative_to(ROOT)),
        "gold_status": gold_payload.get("status"),
        "gold_warning": (
            "Candidate gold is pending domain review. The 30-chunk treatment overlaps only two "
            "unique direct-gold chunks, so overlap-subset metrics are diagnostic and correlated."
        ),
        "experiment": {
            "embedding_model": embeddings.DEFAULT_MODEL,
            "dimension": embeddings.DEFAULT_DIMENSION,
            "query_route": "production",
            "variants": list(VARIANTS),
            "prompt_version": args.prompt_version,
            "temporary_vectors_persisted": False,
            "top_k_values": list(TOP_K_VALUES),
        },
        "coverage": {
            "approved_embedded_chunks": len(documents),
            "metadata_chunks": len(metadata_ids),
            "eligible_direct_cases": sum(bool(items) for items in direct_gold_by_case.values()),
            "unique_direct_gold_chunks": len(unique_direct_ids),
            "metadata_overlap_cases": len(overlap_cases),
            "metadata_overlap_by_split": dict(Counter(case["dataset_split"] for case in overlap_cases)),
            "metadata_direct_gold_chunks": len(unique_direct_ids & metadata_ids),
            "metadata_direct_chunk_ids": sorted(unique_direct_ids & metadata_ids),
            "unresolved_direct_locators": unresolved,
        },
        "summary": aggregate(details),
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    report = evaluate(args)
    write_json(args.output_json, report)
    write_text(args.output_md, render_markdown(report))
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")


if __name__ == "__main__":
    main()
