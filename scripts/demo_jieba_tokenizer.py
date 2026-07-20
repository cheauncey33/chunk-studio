"""Small jieba demo for retrieval-oriented Chinese tokenization.

Run from repo root:
    uv run --with jieba python scripts/demo_jieba_tokenizer.py
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile

import jieba


DOMAIN_TERMS = [
    "负载损耗",
    "空载损耗",
    "雷电冲击",
    "外施耐压",
    "油浸式变压器",
    "绕组温升",
    "短路阻抗",
    "声压级",
    "GB/T 1094.1",
    "GB 20052",
]

SAMPLES = [
    "油浸式变压器负载损耗和空载损耗应符合 GB 20052 的能效限定值要求。",
    "雷电冲击试验电压和外施耐压试验应按 GB/T 1094.3 的规定执行。",
    "表 6 10kV 级油浸式配电变压器性能参数",
]

QUERY = "10kV 油浸式变压器负载损耗要求"


def main() -> None:
    print("== default precise mode ==")
    for text in SAMPLES:
        print(text)
        print(jieba.lcut(text))

    print("\n== default search mode ==")
    for text in SAMPLES:
        print(text)
        print(jieba.lcut_for_search(text))

    load_domain_dict()

    print("\n== with domain dictionary, search mode ==")
    for text in SAMPLES:
        print(text)
        print(jieba.lcut_for_search(text))

    print("\n== toy weighted lexical scoring ==")
    query_tokens = set(tokens_for_search(QUERY))
    print("query:", QUERY)
    print("query_tokens:", sorted(query_tokens))
    chunks = [
        {
            "id": "table-6",
            "table_title": "表 6 10kV 级油浸式配电变压器性能参数",
            "keywords": "负载损耗 空载损耗 能效限定值",
            "content": "容量、电压等级、空载损耗、负载损耗、短路阻抗等性能参数。",
        },
        {
            "id": "section-7",
            "table_title": "",
            "keywords": "雷电冲击 外施耐压 试验电压",
            "content": "绕组的雷电冲击试验和外施耐压试验电压按标准规定。",
        },
    ]
    weights = {"table_title": 8, "keywords": 6, "content": 1}
    for chunk in chunks:
        score, evidence = weighted_score(query_tokens, chunk, weights)
        print(chunk["id"], "score=", score, "evidence=", evidence)


def load_domain_dict() -> None:
    """Load a temporary userdict so the demo has no repo artifact dependency."""
    lines = [f"{term} 10000 nz" for term in DOMAIN_TERMS]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
        f.write("\n".join(lines))
        path = Path(f.name)
    try:
        jieba.load_userdict(str(path))
    finally:
        path.unlink(missing_ok=True)


def tokens_for_search(text: str) -> list[str]:
    return [
        token.strip().lower()
        for token in jieba.lcut_for_search(text)
        if len(token.strip()) >= 2
    ]


def weighted_score(
    query_tokens: set[str],
    chunk: dict[str, str],
    weights: dict[str, int],
) -> tuple[int, dict[str, list[str]]]:
    score = 0
    evidence: dict[str, list[str]] = {}
    for field, weight in weights.items():
        field_tokens = Counter(tokens_for_search(chunk.get(field, "")))
        matched = sorted(query_tokens.intersection(field_tokens))
        if not matched:
            continue
        evidence[field] = matched
        score += weight * sum(field_tokens[token] for token in matched)
    return score, evidence


if __name__ == "__main__":
    main()
