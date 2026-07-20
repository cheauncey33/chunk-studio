"""Offline jieba-based lexical retrieval demo for approved chunks.

This script is intentionally separate from the production search API. It reads
the local SQLite database, tokenizes stable business fields plus chunk text,
and prints explainable field/token evidence for the top matches.

Run from the repository root:
    $env:PYTHONIOENCODING='utf-8'
    uv run --with jieba python scripts/demo_lexical_retrieval.py "10kV 油浸式变压器负载损耗要求"
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

try:
    import jieba
except ImportError as exc:  # pragma: no cover - exercised manually
    raise SystemExit(
        "jieba is not installed. Run with: "
        "uv run --with jieba python scripts/demo_lexical_retrieval.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "backend" / "data" / "chunkstudio.db"
DEFAULT_TERMS = ROOT / "evaluation" / "domain_terms_jieba.txt"

FIELD_WEIGHTS = {
    "standard_no": 12.0,
    "table_no": 10.0,
    "section": 10.0,
    "table_title": 8.0,
    "table_columns": 7.0,
    "section_title": 7.0,
    "figure_title": 6.0,
    "business_keywords": 6.0,
    "llm_keywords": 3.0,
    "text": 1.0,
}

TOKEN_RE = re.compile(
    r"[a-zA-Z]+(?:/[a-zA-Z]+)?\s*\d+(?:\.\d+)*(?:-\d+)?|"
    r"\d+(?:\.\d+)?\s*(?:kv|kva|kw|w|v|a|hz|db)|"
    r"\d+(?:\.\d+)*",
    re.IGNORECASE,
)

STOP_TOKENS = {
    "nbsp",
    "以及",
    "或者",
    "进行",
    "应按",
    "符合",
    "规定",
    "要求",
    "试验",
    "标准",
}


@dataclass
class ChunkDoc:
    chunk_id: str
    file_name: str
    page: int
    fields: dict[str, str]
    metadata: dict[str, Any]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="10kV 油浸式变压器负载损耗要求")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--terms", type=Path, default=DEFAULT_TERMS)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--content-type", choices=["table", "section", "image", "text"])
    parser.add_argument(
        "--include-llm-suggestions",
        action="store_true",
        help="Also search metadata_llm keyword suggestions. Off by default.",
    )
    args = parser.parse_args()

    load_domain_terms(args.terms)
    docs = load_docs(
        args.db,
        content_type=args.content_type,
        include_llm_suggestions=args.include_llm_suggestions,
    )
    if not docs:
        raise SystemExit("No approved chunks found for the selected filters.")

    query_tokens = tokens_for_search(args.query)
    idf = build_idf(docs)
    scored = [
        score_doc(doc, query_tokens, idf, include_llm_suggestions=args.include_llm_suggestions)
        for doc in docs
    ]
    scored = [item for item in scored if item["score"] > 0]
    scored.sort(key=lambda item: item["score"], reverse=True)

    print(json.dumps({
        "query": args.query,
        "query_tokens": query_tokens,
        "candidate_chunks": len(docs),
        "matched_chunks": len(scored),
        "top_k": args.top_k,
        "content_type": args.content_type,
        "include_llm_suggestions": args.include_llm_suggestions,
        "weights": active_weights(args.include_llm_suggestions),
    }, ensure_ascii=False, indent=2))
    print()

    for rank, item in enumerate(scored[: args.top_k], start=1):
        doc: ChunkDoc = item["doc"]
        print(f"#{rank} score={item['score']:.4f} chunk={doc.chunk_id}")
        print(f"file={doc.file_name} page={doc.page}")
        print("metadata=" + json.dumps(compact_metadata(doc.metadata), ensure_ascii=False))
        print("evidence=" + json.dumps(item["evidence"], ensure_ascii=False))
        print("text=" + excerpt(doc.fields.get("text", "")))
        print()


def load_domain_terms(path: Path) -> None:
    if path.exists():
        jieba.load_userdict(str(path))


def load_docs(
    db_path: Path,
    *,
    content_type: str | None,
    include_llm_suggestions: bool,
) -> list[ChunkDoc]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """SELECT c.id, f.name AS file_name, c.page, c.text,
                        c.business_metadata, c.metadata_llm
                 FROM chunks c
                 JOIN files f ON f.id=c.file_id
                 WHERE c.status='approved'"""
        params: list[Any] = []
        if content_type:
            sql += " AND json_extract(c.business_metadata, '$.content_type')=?"
            params.append(content_type)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    docs = []
    for row in rows:
        metadata = parse_json_object(row["business_metadata"])
        llm = parse_json_object(row["metadata_llm"])
        fields = {
            "standard_no": scalar(metadata.get("standard_no")),
            "table_no": scalar(metadata.get("table_no")),
            "section": scalar(metadata.get("section")),
            "table_title": scalar(metadata.get("table_title")),
            "table_columns": flatten(metadata.get("table_columns")),
            "section_title": scalar(metadata.get("section_title")),
            "figure_title": scalar(metadata.get("figure_title")),
            "business_keywords": flatten(metadata.get("keywords")),
            "text": scalar(row["text"]),
        }
        if include_llm_suggestions:
            fields["llm_keywords"] = flatten(extract_llm_value(llm.get("keywords")))
        docs.append(
            ChunkDoc(
                chunk_id=str(row["id"]),
                file_name=str(row["file_name"]),
                page=int(row["page"]),
                fields=fields,
                metadata=metadata,
            )
        )
    return docs


def build_idf(docs: list[ChunkDoc]) -> dict[str, float]:
    doc_freq: Counter[str] = Counter()
    for doc in docs:
        seen = set()
        for field in active_weights(include_llm_suggestions=True):
            seen.update(tokens_for_search(doc.fields.get(field, "")))
        doc_freq.update(seen)
    total = len(docs)
    return {
        token: math.log((total + 1) / (freq + 1)) + 1
        for token, freq in doc_freq.items()
    }


def score_doc(
    doc: ChunkDoc,
    query_tokens: list[str],
    idf: dict[str, float],
    *,
    include_llm_suggestions: bool,
) -> dict[str, Any]:
    query_counts = Counter(query_tokens)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score = 0.0
    for field, weight in active_weights(include_llm_suggestions).items():
        field_counts = Counter(tokens_for_search(doc.fields.get(field, "")))
        for token, qtf in query_counts.items():
            tf = field_counts.get(token, 0)
            if not tf:
                continue
            contribution = weight * min(tf, 3) * idf.get(token, 1.0) * min(qtf, 2)
            score += contribution
            evidence[field].append({
                "token": token,
                "tf": tf,
                "idf": round(idf.get(token, 1.0), 4),
                "score": round(contribution, 4),
            })
    return {
        "doc": doc,
        "score": score,
        "evidence": dict(evidence),
    }


def active_weights(include_llm_suggestions: bool) -> dict[str, float]:
    if include_llm_suggestions:
        return dict(FIELD_WEIGHTS)
    return {key: value for key, value in FIELD_WEIGHTS.items() if key != "llm_keywords"}


def tokens_for_search(text: str) -> list[str]:
    normalized = normalize_text(text)
    tokens = [normalize_token(token) for token in jieba.lcut_for_search(normalized)]
    tokens.extend(normalize_token(match.group(0)) for match in TOKEN_RE.finditer(normalized))
    return [
        token
        for token in dict.fromkeys(tokens)
        if keep_token(token)
    ]


def normalize_text(value: Any) -> str:
    return str(value or "").replace("（", "(").replace("）", ")").lower()


def normalize_token(token: str) -> str:
    return re.sub(r"\s+", "", token.strip().lower())


def keep_token(token: str) -> bool:
    if not token or token in STOP_TOKENS:
        return False
    if re.fullmatch(r"[\W_]+", token):
        return False
    if len(token) >= 2:
        return True
    return token.isdigit()


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_llm_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(flatten(item) for item in value)
    if isinstance(value, dict):
        return " ".join(flatten(item) for item in value.values())
    return str(value)


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    return flatten(value)


def compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "standard_no",
        "content_type",
        "section",
        "section_title",
        "table_no",
        "table_title",
        "figure_title",
    )
    return {key: metadata.get(key) for key in keys if metadata.get(key)}


def excerpt(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


if __name__ == "__main__":
    main()
