"""Use an API model to decode a product model from a naming-rule Markdown file."""
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


DEFAULT_CASES = ROOT / "evaluation" / "retrieval_seed_cases_v1.json"
DEFAULT_OUTPUT = BACKEND / "data" / "reports" / "model_naming_rule_experiment_v1.json"
DEFAULT_LLM_MODEL = "qwen-flash"
CASE_ID = "hbjc-load-loss-pk"
RAW_MODEL = "S20-M.RL-400/10-NX2"
RRF_K = 60


SYSTEM_PROMPT = """你是产品型号命名规则解析器。你的输入只有检测报告中提取的原始型号、报告直接可见的上下文，以及一份型号命名规则Markdown。

任务：严格依据命名规则解释型号，并生成一条用于寻找标准参数表的table_target检索Query。

约束：
1. 只能使用输入中的报告事实和命名规则，不得使用行业常识补全。
2. 不得推断或输出损耗、耐压、温升等标准限值。
3. 每项解释必须给出规则章节或表号，并提供Markdown中的短原文证据。
4. 无法解释的型号片段必须放入unresolved_segments，不得猜测。
5. table_target只描述产品类型、型号派生特征、容量、电压和目标参数表主题，不得包含报告声称的标准值。
6. 严格输出JSON对象，不要输出Markdown代码块。

输出结构：
{
  "raw_model": "原始型号",
  "decoded_description": "完整自然语言解释",
  "decoded_features": [
    {
      "segment": "型号片段",
      "meaning": "含义",
      "evidence_section": "章节或表号",
      "evidence_quote": "规则原文短摘录"
    }
  ],
  "retrieval_terms": ["可用于检索的产品特征"],
  "unresolved_segments": ["无法解释的片段"],
  "table_target": "目标表描述Query"
}"""


def _parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be a JSON object")
    return parsed


def decode_model(markdown: str, *, llm_model: str) -> dict[str, Any]:
    api_key = os.environ.get("DASHSCOPE_API_KEY") or db.get_setting("llm.api_key")
    if not api_key:
        raise RuntimeError("DashScope API key is not configured")
    user_content = json.dumps(
        {
            "report_context": {
                "raw_model": RAW_MODEL,
                "rated_capacity": "400 kVA",
                "rated_voltage": "10/0.4 kV",
                "phase_count": "3相",
                "product_type": "油浸式变压器",
                "test_item": "短路阻抗和负载损耗测量",
                "parameter_name": "负载损耗Pk"
            },
            "naming_rule_markdown": markdown,
        },
        ensure_ascii=False,
    )
    if llm_model == "qwen3.6-27b":
        from dashscope import MultiModalConversation

        response = MultiModalConversation.call(
            api_key=api_key,
            model=llm_model,
            messages=[
                {"role": "system", "content": [{"text": SYSTEM_PROMPT}]},
                {"role": "user", "content": [{"text": user_content}]},
            ],
            result_format="message",
            enable_thinking=False,
            temperature=0,
        )
    else:
        from dashscope import Generation

        response = Generation.call(
            api_key=api_key,
            model=llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            result_format="message",
            response_format={"type": "json_object"},
            temperature=0,
        )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"model naming decode failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )
    content = response.output["choices"][0]["message"]["content"]
    decoded = _parse_json_object(content)
    if decoded.get("raw_model") != RAW_MODEL:
        raise ValueError("model response changed the raw model")
    table_target = str(decoded.get("table_target") or "").strip()
    if not table_target:
        raise ValueError("model response omitted table_target")
    return decoded


def retrieve(case: dict[str, Any], table_target: str) -> dict[str, Any]:
    baseline_queries = {
        route: query
        for route, query in case["queries"].items()
        if route in {"production", "semantic", "keyword"}
    }
    routes = {**baseline_queries, "model_table_target": table_target}
    merged: dict[str, dict[str, Any]] = {}
    standalone_rank = None
    for route, query in routes.items():
        result = embeddings.vector_search(query, top_k=20, content_type="table")
        for rank, hit in enumerate(result["hits"], start=1):
            metadata = hit["business_metadata"]
            if (
                route == "model_table_target"
                and metadata.get("standard_no") == "Q/GDW 12126.4-2024"
                and metadata.get("table_no") == "6"
                and standalone_rank is None
            ):
                standalone_rank = rank
            candidate = merged.setdefault(
                hit["chunk_id"],
                {"business_metadata": metadata, "rrf_score": 0.0, "route_ranks": {}},
            )
            candidate["rrf_score"] += 1 / (RRF_K + rank)
            candidate["route_ranks"][route] = rank
    ranked = sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)
    target = next(
        (
            (rank, candidate)
            for rank, candidate in enumerate(ranked, start=1)
            if candidate["business_metadata"].get("standard_no") == "Q/GDW 12126.4-2024"
            and candidate["business_metadata"].get("table_no") == "6"
        ),
        None,
    )
    return {
        "baseline_routes": list(baseline_queries),
        "added_route": "model_table_target",
        "model_table_target": table_target,
        "target": {"standard_no": "Q/GDW 12126.4-2024", "table_no": "6"},
        "standalone_target_rank": standalone_rank,
        "fused_target_rank": target[0] if target else None,
        "target_route_ranks": target[1]["route_ranks"] if target else {},
        "merged_candidate_count": len(ranked),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    args = parser.parse_args()

    markdown = args.markdown.read_text(encoding="utf-8")
    spec = json.loads(args.cases.read_text(encoding="utf-8"))
    case = next(item for item in spec["cases"] if item["id"] == CASE_ID)
    db.init_db()
    decoded = decode_model(markdown, llm_model=args.llm_model)
    retrieval = retrieve(case, str(decoded["table_target"]))
    report = {
        "version": 1,
        "scope": "read_only_model_naming_rule_experiment",
        "llm_model": args.llm_model,
        "naming_rule_file": str(args.markdown),
        "database_writes": False,
        "decoded_model": decoded,
        "retrieval": retrieval,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decoded_model": decoded, "retrieval": retrieval}, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
