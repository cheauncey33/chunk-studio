"""Audit chunk quality before building search/RAG indexes.

The script is intentionally read-only: it inspects the current SQLite database
and writes a JSON plus Markdown report under backend/data/reports.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import chunk_schema, config, db  # noqa: E402


OLD_METADATA_KEYS = {
    "metadata_v2",
    "notes",
    "table_ref",
    "figure_ref",
    "table_header",
    "figure_header",
    "auto_chunk_types",
    "auto_source",
    "mineru_parse_id",
    "mineru_model",
    "mineru_page_idx",
    "mineru_block_index",
    "mineru_table_bbox",
    "mineru_image_bbox",
    "mineru_caption_bbox",
}

LATEX_RE = re.compile(r"\\\(|\\\)|\\mathrm|\\text|\\frac|\\sqrt")
REVIEW_SEVERITY = {
    "missing_standard_no": "major",
    "missing_content_type": "major",
    "missing_source_trace": "major",
    "missing_source_block": "major",
    "missing_bbox": "major",
    "old_metadata_keys": "major",
    "latex_residue": "major",
    "table_missing_no": "major",
    "table_missing_title": "major",
    "image_missing_caption": "major",
    "short_text": "minor",
    "very_long_text": "minor",
    "table_without_columns": "minor",
    "duplicate_candidate": "minor",
    "continued_table": "info",
}
INDEX_BLOCKING_TAGS = {
    "missing_standard_no",
    "missing_content_type",
    "missing_source_trace",
    "missing_source_block",
    "missing_bbox",
    "old_metadata_keys",
    "latex_residue",
}


@dataclass
class ChunkIssue:
    tag: str
    detail: str = ""


@dataclass
class ChunkAudit:
    id: str
    file_id: str
    file_name: str
    page: int
    content_type: str
    text_length: int
    business_metadata: dict[str, Any]
    source_trace: dict[str, Any]
    chunk_logic: dict[str, Any]
    relations: dict[str, Any]
    issues: list[ChunkIssue] = field(default_factory=list)
    indexable: bool = True
    needs_review: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "page": self.page,
            "content_type": self.content_type,
            "text_length": self.text_length,
            "indexable": self.indexable,
            "needs_review": self.needs_review,
            "issues": [{"tag": issue.tag, "detail": issue.detail} for issue in self.issues],
            "business_metadata": self.business_metadata,
            "source_trace_summary": {
                "page_start": self.source_trace.get("page_start"),
                "page_end": self.source_trace.get("page_end"),
                "source_block_count": len(self.source_trace.get("source_blocks") or []),
                "has_bbox_union": bool(self.source_trace.get("bbox_union")),
            },
            "chunk_logic": self.chunk_logic,
            "relations_keys": sorted(self.relations.keys()),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit chunk quality before indexing.")
    parser.add_argument("--json", default="", help="Optional output JSON path.")
    parser.add_argument("--md", default="", help="Optional output Markdown path.")
    parser.add_argument("--sample-limit", type=int, default=8, help="Problem samples per file.")
    args = parser.parse_args()

    db.init_db()
    report = build_report(sample_limit=args.sample_limit)
    out_dir = config.DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(args.json) if args.json else out_dir / "chunk_quality_report.json"
    md_path = Path(args.md) if args.md else out_dir / "chunk_quality_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def build_report(*, sample_limit: int) -> dict[str, Any]:
    conn = db.get_conn()
    files = {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "page_count": row["page_count"],
            "metadata": chunk_schema.parse_json_object(row["metadata"]),
        }
        for row in conn.execute("SELECT * FROM files ORDER BY created_at").fetchall()
    }
    rows = conn.execute("SELECT * FROM chunks ORDER BY file_id, page, created_at").fetchall()
    audits = [audit_chunk(row, files.get(row["file_id"], {})) for row in rows]
    mark_duplicates(audits)

    totals = summarize_audits(audits)
    by_file = []
    for file_id, file_info in files.items():
        file_audits = [audit for audit in audits if audit.file_id == file_id]
        by_file.append(summarize_file(file_info, file_audits, sample_limit=sample_limit))

    return {
        "version": 1,
        "generated_from": str(config.DB_PATH),
        "summary": totals,
        "files": by_file,
        "chunks": [audit.to_json() for audit in audits],
    }


def audit_chunk(row: Any, file_info: dict[str, Any]) -> ChunkAudit:
    metadata = chunk_schema.parse_json_object(row["metadata"])
    layers = chunk_schema.ensure_layered_chunk(
        metadata=metadata,
        business_metadata=row["business_metadata"],
        source_trace=row["source_trace"],
        chunk_logic=row["chunk_logic"],
        relations=row["relations"],
    )
    business = layers["business_metadata"]
    source_trace = layers["source_trace"]
    chunk_logic = layers["chunk_logic"]
    relations = layers["relations"]
    content_type = str(business.get("content_type") or chunk_logic.get("chunk_type") or "unknown")
    text = row["text"] or ""
    audit = ChunkAudit(
        id=row["id"],
        file_id=row["file_id"],
        file_name=file_info.get("name") or "",
        page=row["page"],
        content_type=content_type,
        text_length=len(text),
        business_metadata=business,
        source_trace=source_trace,
        chunk_logic=chunk_logic,
        relations=relations,
    )

    add_common_issues(audit, text, metadata)
    if content_type == "table":
        add_table_issues(audit)
    elif content_type == "image":
        add_image_issues(audit, text)
    elif content_type == "section":
        add_section_issues(audit)

    audit.needs_review = any(REVIEW_SEVERITY.get(issue.tag) == "major" for issue in audit.issues)
    audit.indexable = not any(issue.tag in INDEX_BLOCKING_TAGS for issue in audit.issues)
    return audit


def add_common_issues(audit: ChunkAudit, text: str, legacy_metadata: dict[str, Any]) -> None:
    business = audit.business_metadata
    source_trace = audit.source_trace
    if not business.get("standard_no"):
        audit.issues.append(ChunkIssue("missing_standard_no"))
    if not business.get("content_type"):
        audit.issues.append(ChunkIssue("missing_content_type"))
    if not source_trace:
        audit.issues.append(ChunkIssue("missing_source_trace"))
    blocks = source_trace.get("source_blocks")
    if not isinstance(blocks, list) or not blocks:
        audit.issues.append(ChunkIssue("missing_source_block"))
    elif not any(isinstance(block, dict) and block.get("bbox") for block in blocks):
        audit.issues.append(ChunkIssue("missing_bbox"))
    if LATEX_RE.search(json.dumps(business, ensure_ascii=False)) or LATEX_RE.search(text):
        audit.issues.append(ChunkIssue("latex_residue"))
    old_hits = sorted(
        key
        for layer in (legacy_metadata, business, source_trace, audit.chunk_logic, audit.relations)
        for key in OLD_METADATA_KEYS
        if key in layer
    )
    if old_hits:
        audit.issues.append(ChunkIssue("old_metadata_keys", ", ".join(old_hits)))
    if len(text.strip()) < 20:
        audit.issues.append(ChunkIssue("short_text", str(len(text.strip()))))
    if len(text) > 20000:
        audit.issues.append(ChunkIssue("very_long_text", str(len(text))))


def add_table_issues(audit: ChunkAudit) -> None:
    business = audit.business_metadata
    table_kind = str(business.get("table_kind") or "")
    if table_kind in {"numbered_table", "continued_table"} and not business.get("table_no"):
        audit.issues.append(ChunkIssue("table_missing_no"))
    if table_kind in {"numbered_table", "continued_table"} and not business.get("table_title"):
        audit.issues.append(ChunkIssue("table_missing_title"))
    if not business.get("table_columns"):
        audit.issues.append(ChunkIssue("table_without_columns"))


def add_image_issues(audit: ChunkAudit, text: str) -> None:
    business = audit.business_metadata
    if not business.get("figure_no") or not business.get("figure_title") or not text.strip():
        audit.issues.append(ChunkIssue("image_missing_caption"))


def add_section_issues(audit: ChunkAudit) -> None:
    business = audit.business_metadata
    if not business.get("section"):
        audit.issues.append(ChunkIssue("section_missing_no"))


def mark_duplicates(audits: list[ChunkAudit]) -> None:
    seen: dict[tuple[str, str, str, str], ChunkAudit] = {}
    for audit in audits:
        business = audit.business_metadata
        key_value = ""
        if audit.content_type == "table":
            key_value = str(business.get("table_no") or "")
        elif audit.content_type == "image":
            key_value = str(business.get("figure_no") or "")
        elif audit.content_type == "section":
            key_value = str(business.get("section") or "")
        if not key_value:
            continue
        key = (audit.file_id, audit.content_type, key_value, str(business.get("table_title") or business.get("figure_title") or business.get("section_title") or ""))
        if key in seen:
            if is_declared_split(audit):
                continue
            if is_continued_table(audit):
                audit.issues.append(ChunkIssue("continued_table", f"continues {seen[key].id}"))
            else:
                audit.issues.append(ChunkIssue("duplicate_candidate", f"similar key as {seen[key].id}"))
        else:
            seen[key] = audit


def is_declared_split(audit: ChunkAudit) -> bool:
    split = audit.chunk_logic.get("split")
    return (
        isinstance(split, dict)
        and bool(split.get("from"))
        and isinstance(split.get("part"), int)
        and isinstance(split.get("parts"), int)
    )


def is_continued_table(audit: ChunkAudit) -> bool:
    if audit.content_type != "table":
        return False
    split = audit.chunk_logic.get("split")
    if isinstance(split, dict) and split.get("reason") == "continued_table":
        return True
    belongs_to = audit.relations.get("belongs_to")
    if isinstance(belongs_to, dict) and belongs_to.get("type") == "continued_table":
        return True
    return False


def summarize_audits(audits: list[ChunkAudit]) -> dict[str, Any]:
    tag_counts = Counter(issue.tag for audit in audits for issue in audit.issues)
    table_kind_counts = Counter(
        str(audit.business_metadata.get("table_kind") or "unknown")
        for audit in audits
        if audit.content_type == "table"
    )
    return {
        "total_chunks": len(audits),
        "indexable_chunks": sum(1 for audit in audits if audit.indexable),
        "needs_review_chunks": sum(1 for audit in audits if audit.needs_review),
        "by_content_type": dict(Counter(audit.content_type for audit in audits)),
        "by_table_kind": dict(sorted(table_kind_counts.items())),
        "issue_counts": dict(sorted(tag_counts.items())),
    }


def summarize_file(file_info: dict[str, Any], audits: list[ChunkAudit], *, sample_limit: int) -> dict[str, Any]:
    tag_counts = Counter(issue.tag for audit in audits for issue in audit.issues)
    issue_samples = []
    for audit in audits:
        if not audit.issues:
            continue
        issue_samples.append({
            "id": audit.id,
            "page": audit.page,
            "content_type": audit.content_type,
            "issues": [{"tag": issue.tag, "detail": issue.detail} for issue in audit.issues],
            "business_metadata": {
                key: audit.business_metadata.get(key)
                for key in ("standard_no", "content_type", "table_kind", "table_no", "table_title", "figure_no", "figure_title", "section", "section_title")
                if audit.business_metadata.get(key) is not None
            },
        })
        if len(issue_samples) >= sample_limit:
            break
    return {
        "id": file_info.get("id"),
        "name": file_info.get("name"),
        "page_count": file_info.get("page_count"),
        "summary": {
            "total_chunks": len(audits),
            "indexable_chunks": sum(1 for audit in audits if audit.indexable),
            "needs_review_chunks": sum(1 for audit in audits if audit.needs_review),
            "by_content_type": dict(Counter(audit.content_type for audit in audits)),
            "by_table_kind": dict(sorted(Counter(
                str(audit.business_metadata.get("table_kind") or "unknown")
                for audit in audits
                if audit.content_type == "table"
            ).items())),
            "issue_counts": dict(sorted(tag_counts.items())),
        },
        "issue_samples": issue_samples,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Chunk Quality Report", ""]
    summary = report["summary"]
    lines.extend([
        "## Summary",
        "",
        f"- Total chunks: {summary['total_chunks']}",
        f"- Indexable chunks: {summary['indexable_chunks']}",
        f"- Needs review: {summary['needs_review_chunks']}",
        f"- By content type: `{json.dumps(summary['by_content_type'], ensure_ascii=False)}`",
        f"- By table kind: `{json.dumps(summary.get('by_table_kind', {}), ensure_ascii=False)}`",
        f"- Issue counts: `{json.dumps(summary['issue_counts'], ensure_ascii=False)}`",
        "",
    ])
    for file_report in report["files"]:
        file_summary = file_report["summary"]
        lines.extend([
            f"## {file_report['name']}",
            "",
            f"- File id: `{file_report['id']}`",
            f"- Pages: {file_report['page_count']}",
            f"- Total chunks: {file_summary['total_chunks']}",
            f"- Indexable chunks: {file_summary['indexable_chunks']}",
            f"- Needs review: {file_summary['needs_review_chunks']}",
            f"- By content type: `{json.dumps(file_summary['by_content_type'], ensure_ascii=False)}`",
            f"- By table kind: `{json.dumps(file_summary.get('by_table_kind', {}), ensure_ascii=False)}`",
            f"- Issue counts: `{json.dumps(file_summary['issue_counts'], ensure_ascii=False)}`",
            "",
        ])
        if file_report["issue_samples"]:
            lines.append("### Samples")
            lines.append("")
            for sample in file_report["issue_samples"]:
                lines.append(
                    f"- Page {sample['page']} `{sample['content_type']}` `{sample['id']}`: "
                    f"{', '.join(issue['tag'] for issue in sample['issues'])}"
                )
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
