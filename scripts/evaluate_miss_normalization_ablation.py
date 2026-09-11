"""Compare original and field-normalized production-only candidate recall on miss groups."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import retrieval  # noqa: E402
from app.evidence_locator import chunk_text_sha256  # noqa: E402
from analyze_retrieval_diagnostics import read_groups  # noqa: E402
from evaluate_test_set import build_production_query  # noqa: E402


TOP_K = 30
CONTEXT_FIELDS = ("sample_name", "model", "rated_capacity", "rated_voltage")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized_base(raw: str) -> str:
    normalized = unicodedata.normalize("NFKC", raw)
    normalized = re.sub(r"参考温度（[^）]*）", "参考温度", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_query(case: dict[str, Any], *, include_standards: bool = True) -> str:
    """Build a deterministic field query without using the gold locator."""
    normalized = _normalized_base(build_production_query(case))

    project_name = str(case["detection_project"].get("project_name") or "")
    fields: list[str] = [f"检测项={project_name}"] if project_name else []
    if "空载" in normalized:
        fields.extend([
            "参数名=空载损耗 P0、空载电流",
            "表头=空载损耗、空载电流",
        ])
    if "短路" in normalized or "负载损耗" in normalized:
        fields.extend([
            "参数名=短路阻抗 Zk、负载损耗 Pk、总损耗",
            "表头=短路阻抗、负载损耗、总损耗、参考温度",
        ])
    if "参考温度" in normalized:
        fields.append("单位=℃")
    if "kW" in normalized or "KW" in normalized:
        fields.append("单位=kW")
    standards = (case["detection_project"].get("sample_context") or {}).get(
        "declared_standard_nos"
    ) or []
    if include_standards and standards:
        fields.append("标准号=" + "、".join(str(item) for item in standards))
    return normalized + " 结构化字段：" + "；".join(fields)


def _decimal_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"\d+\.\d+", text):
        try:
            tokens.add(str(Decimal(raw).normalize()))
        except InvalidOperation:
            continue
    return tokens


def _context_key(context: dict[str, Any]) -> tuple[str, ...]:
    model = re.sub(r"\s+", "", str(context.get("model") or "")).upper()
    return (model,)


def infer_source_contexts(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Restore sample context from legacy cases using report-level numeric signatures.

    Transformer-extension cases retain a source-report hash but omit model, capacity,
    voltage, and sample name.  Match discriminative reported numeric values against
    the legacy cases, then propagate the uniquely identified context to sibling
    cases from the same source report.  Gold locators and evidence text are unused.
    """
    contexts_by_key: dict[tuple[str, ...], dict[str, str]] = {}
    token_contexts: dict[str, set[tuple[str, ...]]] = {}
    for case in (payload.get("legacy") or {}).get("cases") or []:
        project = case.get("detection_project") or {}
        context = project.get("sample_context") or {}
        key = _context_key(context)
        if not any(key):
            continue
        contexts_by_key[key] = {
            field: str(context.get(field) or "").strip()
            for field in CONTEXT_FIELDS
        }
        requirement = str((project.get("reported_requirement") or {}).get("text") or "")
        if "损耗" not in requirement:
            continue
        for token in _decimal_tokens(requirement):
            token_contexts.setdefault(token, set()).add(key)

    report_context_candidates: dict[str, set[tuple[str, ...]]] = {}
    extension = payload.get("transformer_extension", payload)
    for case in extension.get("cases") or []:
        source_hash = str((case.get("source_report") or {}).get("source_sha256") or "")
        project = case.get("detection_project") or {}
        requirement = str((project.get("reported_requirement") or {}).get("text") or "")
        if "损耗" not in requirement:
            continue
        for token in _decimal_tokens(requirement):
            candidates = token_contexts.get(token) or set()
            if len(candidates) == 1:
                report_context_candidates.setdefault(source_hash, set()).update(candidates)

    return {
        source_hash: contexts_by_key[next(iter(keys))]
        for source_hash, keys in report_context_candidates.items()
        if source_hash and len(keys) == 1
    }


def targeted_query(case: dict[str, Any], context: dict[str, str]) -> str:
    """Build a domain-routed rewrite without using a Gold locator or quote."""
    raw_query = _normalized_base(build_production_query(case))
    base = normalize_query(case, include_standards=False)
    prefix = " ".join(context.get(field, "") for field in CONTEXT_FIELDS).strip()
    if "参考温度" in raw_query:
        return "GB/T 1094.1-2013 变压器试验 负载损耗测量 参考温度 75 ℃"
    standard = "GB/T 6451-2023"
    return f"{prefix} {base}；标准号={standard}".strip()


def restored_context_query(case: dict[str, Any], context: dict[str, str]) -> str:
    prefix = " ".join(context.get(field, "") for field in CONTEXT_FIELDS).strip()
    return f"{prefix} {normalize_query(case, include_standards=False)}".strip()


def compact_hit(rank: int, hit: dict[str, Any]) -> dict[str, Any]:
    metadata = hit.get("business_metadata") or {}
    return {
        "rank": rank,
        "chunk_id": hit.get("chunk_id"),
        "text_sha256": chunk_text_sha256(hit.get("text")),
        "content_type": metadata.get("content_type") or hit.get("content_type"),
        "standard_no": metadata.get("standard_no"),
        "section": metadata.get("section"),
        "table_no": metadata.get("table_no"),
        "rrf_score": hit.get("rrf_score"),
        "source_ranks": hit.get("source_ranks") or {},
    }


def run_candidate_query(query: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = retrieval.retrieve_candidate_pool(
        query,
        query_routes={"production": query},
        route_top_k=retrieval.ROUTE_TOP_K,
        candidates_per_type=retrieval.CANDIDATES_PER_TYPE,
        lexical_candidates_per_type=retrieval.LEXICAL_CANDIDATES_PER_TYPE,
    )
    hits = [compact_hit(rank, hit) for rank, hit in enumerate(result["hits"], 1)]
    return {
        "query": query,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "candidate_count": result.get("candidate_count"),
        "hits": hits,
    }


def best_gold_rank(run: dict[str, Any], gold_hashes: list[str]) -> int | None:
    ranks = {
        hit["text_sha256"]: int(hit["rank"])
        for hit in run["hits"][:TOP_K]
    }
    matched = [ranks[text_hash] for text_hash in gold_hashes if text_hash in ranks]
    return min(matched) if matched else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--miss-diagnostics-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    payload = read_json(args.ground_truth)
    extension = payload.get("transformer_extension", payload)
    cases_by_id = {case["case_id"]: case for case in extension["cases"]}
    source_contexts = infer_source_contexts(payload)
    miss_groups = [
        group
        for group in read_groups(args.miss_diagnostics_csv)
        if group["outcome"] == "not_recalled"
    ]

    rows = []
    query_cache: dict[str, dict[str, Any]] = {}

    def run_once(query: str) -> dict[str, Any]:
        if query not in query_cache:
            query_cache[query] = run_candidate_query(query)
        return query_cache[query]

    original_runs: dict[str, dict[str, Any]] = {}
    normalized_runs: dict[str, dict[str, Any]] = {}
    for index, group in enumerate(miss_groups, 1):
        case = cases_by_id[group["case_id"]]
        gold_hashes = [
            alternative["locator"]["text_sha256"]
            for evidence_group in case["required_evidence_groups"]
            if evidence_group["group_id"] == group["group_id"]
            for alternative in evidence_group["alternatives"]
        ]
        original_query = group["query"]
        symbol_query = _normalized_base(original_query)
        field_query = normalize_query(case, include_standards=False)
        field_standard_query = normalize_query(case, include_standards=True)
        source_hash = str((case.get("source_report") or {}).get("source_sha256") or "")
        restored_context = source_contexts.get(source_hash) or {}
        context_only_query = restored_context_query(case, restored_context)
        targeted_only_query = targeted_query(case, {})
        context_targeted_query = targeted_query(case, restored_context)
        print(f"evaluating {index}/{len(miss_groups)} {group['case_id']}", flush=True)
        original = run_once(original_query)
        symbol_normalized = run_once(symbol_query)
        field_normalized = run_once(field_query)
        field_standard_normalized = run_once(field_standard_query)
        context_only = run_once(context_only_query)
        targeted_only = run_once(targeted_only_query)
        context_targeted = run_once(context_targeted_query)
        original_runs[group["case_id"]] = original
        normalized_runs[group["case_id"]] = {
            "symbol": symbol_normalized,
            "field": field_normalized,
            "field_standard": field_standard_normalized,
            "context_only": context_only,
            "targeted_only": targeted_only,
            "context_targeted": context_targeted,
        }
        original_rank = best_gold_rank(original, gold_hashes)
        symbol_rank = best_gold_rank(symbol_normalized, gold_hashes)
        field_rank = best_gold_rank(field_normalized, gold_hashes)
        field_standard_rank = best_gold_rank(field_standard_normalized, gold_hashes)
        context_only_rank = best_gold_rank(context_only, gold_hashes)
        targeted_only_rank = best_gold_rank(targeted_only, gold_hashes)
        context_targeted_rank = best_gold_rank(context_targeted, gold_hashes)
        rows.append(
            {
                "case_id": group["case_id"],
                "group_id": group["group_id"],
                "original_query": original_query,
                "symbol_query": symbol_query,
                "field_query": field_query,
                "field_standard_query": field_standard_query,
                "context_only_query": context_only_query,
                "targeted_only_query": targeted_only_query,
                "context_targeted_query": context_targeted_query,
                "restored_context": restored_context,
                "evidence_type": group["evidence_type"],
                "gold_hashes": gold_hashes,
                "original_rank": original_rank,
                "symbol_rank": symbol_rank,
                "field_rank": field_rank,
                "field_standard_rank": field_standard_rank,
                "context_only_rank": context_only_rank,
                "targeted_only_rank": targeted_only_rank,
                "context_targeted_rank": context_targeted_rank,
                "original_hit": original_rank is not None and original_rank <= TOP_K,
                "symbol_hit": symbol_rank is not None and symbol_rank <= TOP_K,
                "field_hit": field_rank is not None and field_rank <= TOP_K,
                "field_standard_hit": field_standard_rank is not None and field_standard_rank <= TOP_K,
                "context_only_hit": context_only_rank is not None and context_only_rank <= TOP_K,
                "targeted_only_hit": targeted_only_rank is not None and targeted_only_rank <= TOP_K,
                "context_targeted_hit": context_targeted_rank is not None and context_targeted_rank <= TOP_K,
                "original_candidate_count": original.get("candidate_count"),
                "symbol_candidate_count": symbol_normalized.get("candidate_count"),
                "field_candidate_count": field_normalized.get("candidate_count"),
                "field_standard_candidate_count": field_standard_normalized.get("candidate_count"),
                "context_only_candidate_count": context_only.get("candidate_count"),
                "targeted_only_candidate_count": targeted_only.get("candidate_count"),
                "context_targeted_candidate_count": context_targeted.get("candidate_count"),
                "original_seconds": original["duration_seconds"],
                "symbol_seconds": symbol_normalized["duration_seconds"],
                "field_seconds": field_normalized["duration_seconds"],
                "field_standard_seconds": field_standard_normalized["duration_seconds"],
                "context_only_seconds": context_only["duration_seconds"],
                "targeted_only_seconds": targeted_only["duration_seconds"],
                "context_targeted_seconds": context_targeted["duration_seconds"],
            }
        )

    original_hits = sum(row["original_hit"] for row in rows)
    variant_hits = {
        "original": original_hits,
        "symbol_normalized": sum(row["symbol_hit"] for row in rows),
        "field_normalized": sum(row["field_hit"] for row in rows),
        "field_plus_standards": sum(row["field_standard_hit"] for row in rows),
        "restored_context_only": sum(row["context_only_hit"] for row in rows),
        "targeted_standard_only": sum(row["targeted_only_hit"] for row in rows),
        "restored_context_plus_targeted_standard": sum(
            row["context_targeted_hit"] for row in rows
        ),
    }
    result = {
        "cases": len(rows),
        "unique_original_queries": len({row["original_query"] for row in rows}),
        "top30_hits": variant_hits,
        "top30_recall": {
            variant: hits / len(rows) if rows else None
            for variant, hits in variant_hits.items()
        },
        "by_evidence_type": {
            evidence_type: {
                "cases": sum(row["evidence_type"] == evidence_type for row in rows),
                "original_hits": sum(
                    row["evidence_type"] == evidence_type and row["original_hit"]
                    for row in rows
                ),
                "symbol_hits": sum(row["evidence_type"] == evidence_type and row["symbol_hit"] for row in rows),
                "field_hits": sum(row["evidence_type"] == evidence_type and row["field_hit"] for row in rows),
                "field_standard_hits": sum(row["evidence_type"] == evidence_type and row["field_standard_hit"] for row in rows),
                "context_only_hits": sum(row["evidence_type"] == evidence_type and row["context_only_hit"] for row in rows),
                "targeted_only_hits": sum(row["evidence_type"] == evidence_type and row["targeted_only_hit"] for row in rows),
                "context_targeted_hits": sum(row["evidence_type"] == evidence_type and row["context_targeted_hit"] for row in rows),
            }
            for evidence_type in sorted({row["evidence_type"] for row in rows})
        },
        "rows": rows,
        "restored_source_contexts": source_contexts,
        "runs": {"original": original_runs, "normalized": normalized_runs},
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Miss query normalization ablation",
        "",
        "Production-only candidate-pool Top-30; reranking is disabled. Gold alternatives within one Group use OR semantics.",
        "",
        f"Groups: `{len(rows)}`; unique original queries: `{result['unique_original_queries']}`.",
        "",
        "| Variant | Top-30 hits | Recall |",
        "|---|---:|---:|",
        f"| Original query | {variant_hits['original']} | {result['top30_recall']['original']:.1%} |",
        f"| Symbol normalized | {variant_hits['symbol_normalized']} | {result['top30_recall']['symbol_normalized']:.1%} |",
        f"| Fields + table headers | {variant_hits['field_normalized']} | {result['top30_recall']['field_normalized']:.1%} |",
        f"| Fields + headers + standards | {variant_hits['field_plus_standards']} | {result['top30_recall']['field_plus_standards']:.1%} |",
        f"| Restored context only | {variant_hits['restored_context_only']} | {result['top30_recall']['restored_context_only']:.1%} |",
        f"| Targeted standard only | {variant_hits['targeted_standard_only']} | {result['top30_recall']['targeted_standard_only']:.1%} |",
        f"| Restored context + targeted standard | {variant_hits['restored_context_plus_targeted_standard']} | {result['top30_recall']['restored_context_plus_targeted_standard']:.1%} |",
        "",
        "| Case | Evidence | Original | Symbols | Fields | +Standards | Context | Target | Both |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case_id']} | {row['evidence_type']} | {row['original_rank'] or '-'} | "
            f"{row['symbol_rank'] or '-'} | {row['field_rank'] or '-'} | "
            f"{row['field_standard_rank'] or '-'} | {row['context_only_rank'] or '-'} | "
            f"{row['targeted_only_rank'] or '-'} | {row['context_targeted_rank'] or '-'} |"
        )
    lines.extend(
        [
            "",
            "Variants add NFKC symbol normalization, explicit parameter/table-header fields, units, and declared standards. The final variant restores model/capacity/voltage from sibling report cases via legacy numeric signatures and routes the parameter family to one standard. None uses the Gold locator, table/section number, or Gold quote.",
            "",
        ]
    )
    args.output_md.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
