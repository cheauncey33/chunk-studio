"""Expose unmatched quotes and nearby source text for human review; no verdict rewrite."""
from __future__ import annotations

import argparse
import difflib
from html import unescape
import json
from pathlib import Path
import re


def visible(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def inspect(replay: dict, report: str) -> list[dict]:
    rows = []
    for index, check in enumerate(replay["output"]["result"].get("applicability_checks") or [], 1):
        naming = "命名" in str(check.get("source")) or "JB/T" in str(check.get("source"))
        source = replay["input"].get("naming_rule_context", "") if naming else report
        quote = str(check.get("evidence_quote") or "")
        plain_source, plain_quote = visible(source), visible(quote)
        match = difflib.SequenceMatcher(None, plain_quote, plain_source, autojunk=False).find_longest_match()
        rows.append({
            "index": index, **check, "exact_match": bool(quote and quote in source),
            "visible_match": bool(plain_quote and plain_quote in plain_source),
            "longest_matching_text": plain_quote[match.a:match.a + match.size],
            "nearby_source": plain_source[max(0, match.b - 100):match.b + match.size + 180],
            "note": "Longest matching text is diagnostic only; never used to accept a quote.",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--report-markdown", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = inspect(json.loads(args.replay.read_text(encoding="utf-8")), args.report_markdown.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in rows:
        print(row["index"], row["parameter"], "exact" if row["exact_match"] else "needs_review")


if __name__ == "__main__":
    main()
