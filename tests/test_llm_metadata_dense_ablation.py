from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_llm_metadata_dense_ablation.py"
SPEC = importlib.util.spec_from_file_location("evaluate_llm_metadata_dense_ablation", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_augmented_document_text_keeps_variants_isolated() -> None:
    base = "标准号 | 表1\n\n正文"
    keywords = ["负载损耗", "额定容量"]
    questions = ["200 kVA的负载损耗是多少？"]

    keyword_text = module.augmented_document_text(
        base,
        keywords=keywords,
        questions=questions,
        include_questions=False,
    )
    question_text = module.augmented_document_text(
        base,
        keywords=keywords,
        questions=questions,
        include_questions=True,
    )

    assert keyword_text.startswith(base)
    assert "负载损耗；额定容量" in keyword_text
    assert "200 kVA" not in keyword_text
    assert "可回答问题" in question_text
    assert "200 kVA的负载损耗是多少？" in question_text


def test_aggregate_cases_counts_top_k_hits() -> None:
    cases = [
        {
            "direct_gold_count": 1,
            "rankings": {
                "baseline": {"best_direct_rank": 12, "matched_direct": [{"rank": 12}]},
                "keywords": {"best_direct_rank": 8, "matched_direct": [{"rank": 8}]},
                "keywords_questions": {"best_direct_rank": 3, "matched_direct": [{"rank": 3}]},
            },
        }
    ]

    baseline = module.aggregate_cases(cases, "baseline")["10"]
    questions = module.aggregate_cases(cases, "keywords_questions")["10"]

    assert baseline["direct_case_hits"] == 0
    assert questions["direct_case_hits"] == 1
    assert questions["mrr"] == 1 / 3
