"""Generate and evaluate versioned query-harness strategies against test_set."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import llm, retrieval  # noqa: E402
from evaluate_test_set import metrics_at_k, score_ranking  # noqa: E402

PROMPT_VERSION = "query_harness_v1"
STRATEGIES = (
    "raw_anchor", "declarative_requirement", "interrogative_requirement",
    "reverse_verification", "parameter_declarative", "parameter_interrogative",
    "numeric_table_exploration", "condition_section_exploration",
)
INSTRUCTIONS = {
    "declarative_requirement": "改写为面向标准原文的简洁陈述式检索短句，寻找该项目的标准要求；不要使用‘请查找’，也不要假设存在表、限值或结论。",
    "interrogative_requirement": "改写为标准原文可以回答的问题，询问该项目适用的要求、方法、条件或判定规则；不要假设其中任何一种一定存在。",
    "reverse_verification": "把报告声明改写成中立核验问题，询问该检测项目的报告声明是否有标准依据；声明不是已确认事实。声明为空时改写成标准要求问句。",
    "parameter_declarative": "仅把非空样品参数作为限定条件，改写为面向标准原文的陈述式检索短句；不得补造参数。",
    "parameter_interrogative": "仅把非空样品参数作为限定条件，改写为询问该样品该检测项目适用何种标准要求的问题；不得补造参数。",
    "numeric_table_exploration": "生成偏向数值判据、允许偏差、限值、表题或公式的检索表达；这只是召回方向，不得断言一定存在表或数值判据。",
    "condition_section_exploration": "生成偏向适用范围、条件、方法、例外或条文的检索表达；这只是召回方向，不得断言一定存在这些内容。",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(case: dict[str, Any], strategy: str, model: str | None) -> dict[str, str]:
    item = case["detection_project"]
    project = str(item["project_name"]).strip()
    if strategy == "raw_anchor":
        return {"semantic_query": project, "keyword_query": project}
    payload = {
        "project_name": project,
        "reported_requirement": (item.get("reported_requirement") or {}).get("text") or "",
        "sample_context": {k: v for k, v in (item.get("sample_context") or {}).items() if v},
        "standard_anchors": [],
        "strategy": strategy,
    }
    parsed = llm.chat_json([
        {"role": "system", "content": "你是标准文档检索查询改写器。检测项目名称必须原样保留；只能使用输入事实，不得补造参数、标准条款或结论。只输出JSON：{\"semantic_query\":\"...\",\"keyword_query\":\"...\"}。"},
        {"role": "user", "content": INSTRUCTIONS[strategy] + "\n输入：\n" + json.dumps(payload, ensure_ascii=False)},
    ], model=model, temperature=0)
    out = {key: str(parsed.get(key) or "").strip() for key in ("semantic_query", "keyword_query")}
    if not out["semantic_query"] or not out["keyword_query"]:
        raise ValueError("invalid rewrite output")
    for key in out:
        if project not in out[key]:
            out[key] = f"{project} {out[key]}"
    return out


def evaluate(case: dict[str, Any], queries: dict[str, str]) -> dict[str, Any]:
    result = retrieval.hybrid_search(
        queries["semantic_query"], top_k=20,
        query_routes={"production": queries["semantic_query"], "keyword": queries["keyword_query"]},
    )
    ranking = score_ranking(result["hits"], case)
    return {"case_id": case["case_id"], "queries": queries, "ranking": ranking,
            "metrics": {str(k): metrics_at_k(ranking, k) for k in (5, 10)},
            "degraded": result["degraded"]}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in (5, 10):
        items = [row["metrics"][str(k)] for row in rows]
        best_ranks = [row["ranking"]["best_relevant_rank"] for row in rows]
        out[str(k)] = {
            "cases": len(rows),
            "strict_complete_hits": sum(bool(x["strict_complete_hit"]) for x in items),
            "strict_complete_recall": sum(bool(x["strict_complete_hit"]) for x in items) / len(items),
            "evidence_group_recall": sum(int(x["recalled_groups"]) for x in items) / sum(int(x["required_groups"]) for x in items),
            "mrr": sum(1 / rank for rank in best_ranks if rank and rank <= k) / len(rows),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--strategies", default="all")
    parser.add_argument("--model")
    parser.add_argument("--output-root", type=Path, default=ROOT / "evaluation" / "experiments" / "query_harness")
    args = parser.parse_args()
    selected = STRATEGIES if args.strategies == "all" else tuple(args.strategies.split(","))
    if any(x not in STRATEGIES for x in selected):
        raise ValueError(f"unknown strategy; choose from {', '.join(STRATEGIES)}")
    run_dir = args.output_root / args.run_name
    if run_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing run: {run_dir}")
    cases = [x for x in read_json(ROOT / "evaluation" / "test_set.json")["cases"] if x["answerability_status"] == "answerable_candidate"]
    write_json(run_dir / "manifest.json", {"prompt_version": PROMPT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "model": llm.public_config(model=args.model), "strategies": selected, "cases": len(cases)})
    for strategy in selected:
        rows = []
        for index, case in enumerate(cases, 1):
            print(f"{strategy} {index}/{len(cases)} {case['case_id']}", flush=True)
            rows.append(evaluate(case, generate(case, strategy, args.model)))
        write_json(run_dir / f"{strategy}.json", {"strategy": strategy, "summary": summary(rows), "cases": rows})


if __name__ == "__main__":
    main()
