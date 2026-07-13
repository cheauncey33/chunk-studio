"""Build typed retrieval candidates and model-reviewed stable evidence locators."""
from __future__ import annotations

import argparse
from http import HTTPStatus
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import db, embeddings  # noqa: E402
from app.evidence_locator import chunk_text_sha256, resolve_evidence_locator  # noqa: E402


DEFAULT_CASES = ROOT / "evaluation" / "retrieval_case_pool_v1.json"
DEFAULT_QUERY_PROMPT = ROOT / "evaluation" / "prompts" / "retrieval_query_planner_v1.md"
DEFAULT_JUDGE_PROMPT = ROOT / "evaluation" / "prompts" / "retrieval_evidence_judge_v1.md"
REPORT_DIR = BACKEND / "data" / "reports"
DEFAULT_QUERIES = REPORT_DIR / "retrieval_queries_40_v1.json"
DEFAULT_CANDIDATES = REPORT_DIR / "retrieval_candidates_40_v1.json"
DEFAULT_LABELS = REPORT_DIR / "retrieval_labels_40_v1.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "retrieval_evidence_candidates_v1.json"
MODEL = "qwen3.6-27b"
CONTENT_TYPES = ("table", "section")
ROUTE_TOP_K = 20
FINAL_PER_TYPE = 20
RRF_K = 60
LABELS = {"direct_candidate", "supporting_candidate", "uncertain"}


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text, strict=False)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be an object")
    return parsed


def _call_openai_compatible_model(
    system_prompt: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=180,
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"model call failed: status={response.status_code} body={response.text[:500]}"
        )
    data = response.json()
    return _parse_json_object(data["choices"][0]["message"]["content"])


def _call_model(
    system_prompt: str,
    payload: dict[str, Any],
    *,
    model: str = MODEL,
    provider: str = "dashscope",
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    if provider == "openai_compatible":
        resolved_api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or db.get_setting("llm.api_key")
        )
        if not resolved_api_key:
            raise RuntimeError("OpenAI-compatible API key is not configured")
        return _call_openai_compatible_model(
            system_prompt,
            payload,
            api_key=resolved_api_key,
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            model=model,
        )

    api_key = os.environ.get("DASHSCOPE_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DashScope API key is not configured")

    last_error: Exception | None = None
    for _ in range(2):
        if model == "qwen3.6-27b":
            from dashscope import MultiModalConversation

            response = MultiModalConversation.call(
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": [{"text": system_prompt}]},
                    {"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]},
                ],
                result_format="message",
                enable_thinking=False,
                temperature=0,
            )
        else:
            from dashscope import Generation

            response = Generation.call(
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                result_format="message",
                response_format={"type": "json_object"},
                temperature=0,
            )
        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"model call failed: status={response.status_code} code={response.code} message={response.message}"
            )
        try:
            return _parse_json_object(response.output["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError("model returned invalid JSON twice") from last_error


def _production_query(case: dict[str, Any]) -> str:
    context = case.get("sample_context") or {}
    parts = [
        str(context.get(key) or "").strip()
        for key in ("model", "rated_capacity", "rated_voltage", "sample_name")
    ]
    parts.extend([
        case["test_item"]["project_name"],
        case["reported_requirement"]["text"],
    ])
    return " ".join(dict.fromkeys(part for part in parts if part))


def plan_queries(cases: list[dict[str, Any]], prompt: str, path: Path) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "model": MODEL, "cases": {}})
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        planned = _call_model(prompt, {
            "sample_context": case.get("sample_context") or {},
            "test_item": case["test_item"],
            "reported_requirement": case["reported_requirement"],
        })
        queries = {"production": _production_query(case)}
        for key in ("semantic", "keyword", "table_target", "section_target"):
            value = str(planned.get(key) or "").strip()
            if value:
                queries[key] = value
        if "semantic" not in queries or "keyword" not in queries:
            raise ValueError(f"{case_id}: planner omitted required queries")
        output["cases"][case_id] = queries
        _write_json(path, output)
        print(f"planned {index}/{len(cases)} {case_id}", flush=True)
    return output


def _merge_hits(queries: dict[str, str], content_type: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for route, query in queries.items():
        result = embeddings.vector_search(query, top_k=ROUTE_TOP_K, content_type=content_type)
        for rank, hit in enumerate(result["hits"], start=1):
            candidate = merged.setdefault(hit["chunk_id"], {
                **hit,
                "content_type": content_type,
                "route_ranks": {},
                "route_scores": {},
                "rrf_score": 0.0,
            })
            candidate["route_ranks"][route] = rank
            candidate["route_scores"][route] = hit["score"]
            candidate["rrf_score"] += 1 / (RRF_K + rank)
    ranked = sorted(
        merged.values(),
        key=lambda item: (item["rrf_score"], max(item["route_scores"].values())),
        reverse=True,
    )[:FINAL_PER_TYPE]
    for rank, candidate in enumerate(ranked, start=1):
        candidate["type_rank"] = rank
    return ranked


def retrieve_candidates(
    cases: list[dict[str, Any]], query_data: dict[str, Any], path: Path
) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "model": embeddings.DEFAULT_MODEL, "cases": {}})
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        candidates = []
        for content_type in CONTENT_TYPES:
            candidates.extend(_merge_hits(query_data["cases"][case_id], content_type))
        candidates.sort(key=lambda item: item["rrf_score"], reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate["candidate_key"] = f"c{rank:02d}"
        output["cases"][case_id] = {
            "queries": query_data["cases"][case_id],
            "type_counts": {
                content_type: sum(item["content_type"] == content_type for item in candidates)
                for content_type in CONTENT_TYPES
            },
            "candidates": candidates,
        }
        _write_json(path, output)
        print(f"retrieved {index}/{len(cases)} {case_id}", flush=True)
    return output


def judge_candidates(
    cases: list[dict[str, Any]], candidate_data: dict[str, Any], prompt: str, path: Path
) -> dict[str, Any]:
    output = _read_json(path, {"version": 1, "model": MODEL, "cases": {}})
    for index, case in enumerate(cases, start=1):
        case_id = case["case_id"]
        if case_id in output["cases"]:
            continue
        candidates = candidate_data["cases"][case_id]["candidates"]
        compact = []
        for candidate in candidates:
            metadata = candidate["business_metadata"]
            compact.append({
                "candidate_key": candidate["candidate_key"],
                "content_type": candidate["content_type"],
                "standard_no": metadata.get("standard_no"),
                "section": metadata.get("section"),
                "section_title": metadata.get("section_title"),
                "table_no": metadata.get("table_no"),
                "table_title": metadata.get("table_title"),
                "page": candidate["page"],
                "text": candidate["text"][:8000],
            })
        judged = _call_model(prompt, {
            "case": {
                "sample_context": case.get("sample_context") or {},
                "test_item": case["test_item"],
                "reported_requirement": case["reported_requirement"],
            },
            "candidates": compact,
        })
        selected = judged.get("selected") or []
        valid_keys = {item["candidate_key"] for item in candidates}
        normalized = []
        seen = set()
        for item in selected:
            key = str(item.get("candidate_key") or "")
            label = str(item.get("label") or "")
            if key not in valid_keys or label not in LABELS or key in seen:
                continue
            seen.add(key)
            normalized.append({
                "candidate_key": key,
                "label": label,
                "evidence_quote": str(item.get("evidence_quote") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
            })
        output["cases"][case_id] = {
            "selected": normalized,
            "missing_evidence": str(judged.get("missing_evidence") or "").strip(),
            "review_note": str(judged.get("review_note") or "").strip(),
        }
        _write_json(path, output)
        print(f"judged {index}/{len(cases)} {case_id}: {len(normalized)} selected", flush=True)
    return output


def _locator(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate["business_metadata"]
    trace = candidate["source_trace"]
    locator = {
        "standard_no": metadata["standard_no"],
        "content_type": candidate["content_type"],
        "page_start": trace.get("page_start", candidate["page"]),
        "page_end": trace.get("page_end", candidate["page"]),
        "text_sha256": chunk_text_sha256(candidate["text"]),
    }
    for field in ("section", "section_title", "table_no", "table_title"):
        if metadata.get(field):
            locator[field] = metadata[field]
    return locator


def build_review(
    cases: list[dict[str, Any]], candidate_data: dict[str, Any], labels: dict[str, Any]
) -> dict[str, Any]:
    reviewed = []
    for case in cases:
        case_id = case["case_id"]
        candidates = {
            item["candidate_key"]: item
            for item in candidate_data["cases"][case_id]["candidates"]
        }
        label_data = labels["cases"][case_id]
        selected = []
        for item in label_data["selected"]:
            candidate = candidates[item["candidate_key"]]
            quote = item["evidence_quote"]
            quote_verified = bool(quote and quote in candidate["text"])
            selected.append({
                "label": item["label"],
                "locator": _locator(candidate),
                "evidence_quote": quote if quote_verified else "",
                "quote_verified": quote_verified,
                "reason": item["reason"],
                "retrieval": {
                    "type_rank": candidate["type_rank"],
                    "route_ranks": candidate["route_ranks"],
                },
            })
        reviewed.append({
            "case_id": case_id,
            "dataset_split": case["dataset_split"],
            "gold_status": "candidate_model_reviewed_pending_domain_review",
            "selected_evidence": selected,
            "missing_evidence": label_data["missing_evidence"],
            "review_note": label_data["review_note"],
        })
    return {
        "version": 1,
        "source_cases": "evaluation/retrieval_case_pool_v1.json",
        "query_model": MODEL,
        "judge_model": MODEL,
        "retrieval_policy": "Top 20 per route and content type; RRF merge; final 20 table + 20 section",
        "status": "candidate_model_reviewed_pending_domain_review",
        "cases": reviewed,
    }


def validate_locators(review: dict[str, Any]) -> dict[str, Any]:
    checked = 0
    invalid = []
    for case in review["cases"]:
        for evidence in case["selected_evidence"]:
            checked += 1
            count = len(resolve_evidence_locator(db.get_conn(), evidence["locator"]))
            if count != 1:
                invalid.append({"case_id": case["case_id"], "matches": count, "locator": evidence["locator"]})
    if invalid:
        raise ValueError(f"{len(invalid)} evidence locators did not resolve uniquely")
    return {"checked_locators": checked, "invalid_locators": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--query-prompt", type=Path, default=DEFAULT_QUERY_PROMPT)
    parser.add_argument("--judge-prompt", type=Path, default=DEFAULT_JUDGE_PROMPT)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    case_payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = case_payload["cases"]
    db.init_db()
    query_data = plan_queries(cases, args.query_prompt.read_text(encoding="utf-8"), args.queries)
    candidate_data = retrieve_candidates(cases, query_data, args.candidates)
    label_data = judge_candidates(
        cases, candidate_data, args.judge_prompt.read_text(encoding="utf-8"), args.labels
    )
    review = build_review(cases, candidate_data, label_data)
    review["validation"] = validate_locators(review)
    args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "cases": len(review["cases"]),
        "selected_evidence": sum(len(case["selected_evidence"]) for case in review["cases"]),
        "with_direct": sum(
            any(item["label"] == "direct_candidate" for item in case["selected_evidence"])
            for case in review["cases"]
        ),
        "with_missing_evidence": sum(bool(case["missing_evidence"]) for case in review["cases"]),
        **review["validation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
