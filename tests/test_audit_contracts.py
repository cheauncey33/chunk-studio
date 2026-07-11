import json
import sqlite3
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.evidence_locator import chunk_text_sha256, resolve_evidence_locator
from scripts.audit_chunk_quality import ChunkAudit, audit_chunk, mark_duplicates
from scripts.validate_audit_eval import validate_dataset


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_audit_eval_contracts_and_stable_evidence() -> None:
    dataset_path = ROOT / "evaluation/standard_value_audit_v1.json"
    dataset = _load(dataset_path)
    card_validator = Draft202012Validator(
        _load(ROOT / "backend/schemas/standard_value_audit_card.schema.json")
    )
    result_validator = Draft202012Validator(
        _load(ROOT / "backend/schemas/standard_value_audit_result.schema.json")
    )
    assert len(dataset["cases"]) == 12
    assert "chunk_id" not in json.dumps(dataset, ensure_ascii=False)
    for case in dataset["cases"]:
        card_validator.validate(case["audit_card"])
        result_validator.validate(case["expected_result"])
        assert case["expected_result"]["card_id"] == case["audit_card"]["card_id"]

    summary = validate_dataset(dataset_path, None, verify_corpus=False)
    assert summary == {"cases": 12, "evidence_locators": 15, "corpus_verified": False}


def test_chunk_text_hash_ignores_layout_whitespace_only() -> None:
    assert chunk_text_sha256("a\n  b") == chunk_text_sha256("a b")


def test_resolves_stable_locator_without_chunk_uuid() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE chunks (
            id TEXT, page INTEGER, text TEXT,
            business_metadata TEXT, source_trace TEXT
        )"""
    )
    text = "10 test section\nvalue 35 kV"
    conn.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
        (
            "random-rebuild-id",
            7,
            text,
            json.dumps({"standard_no": "TEST 1-2026", "content_type": "section", "section": "10"}),
            json.dumps({"page_start": 7, "page_end": 7}),
        ),
    )
    locator = {
        "standard_no": "TEST 1-2026",
        "content_type": "section",
        "section": "10",
        "page_start": 7,
        "page_end": 7,
        "text_sha256": chunk_text_sha256(text),
    }

    assert len(resolve_evidence_locator(conn, locator)) == 1


def test_declared_section_parts_are_not_duplicate_candidates() -> None:
    base = {
        "file_id": "file-1",
        "file_name": "test.pdf",
        "page": 1,
        "content_type": "section",
        "text_length": 100,
        "status": "pending",
        "business_metadata": {
            "content_type": "section",
            "section": "A.2",
            "section_title": "计算",
        },
        "source_trace": {},
        "relations": {},
    }
    first = ChunkAudit(id="part-1", chunk_logic={"split": {"from": "A.2", "part": 1, "parts": 2}}, **base)
    second = ChunkAudit(id="part-2", chunk_logic={"split": {"from": "A.2", "part": 2, "parts": 2}}, **base)

    mark_duplicates([first, second])

    assert second.issues == []


def test_only_approved_quality_clean_chunk_is_indexable() -> None:
    base = {
        "id": "chunk-1",
        "file_id": "file-1",
        "page": 1,
        "text": "A sufficiently long and reviewable chunk text.",
        "metadata": "{}",
        "business_metadata": json.dumps({
            "standard_no": "TEST 1-2026",
            "content_type": "section",
            "section": "1",
        }),
        "source_trace": json.dumps({
            "page_start": 1,
            "page_end": 1,
            "source_blocks": [{"page": 1, "type": "text", "bbox": [0.1, 0.1, 0.9, 0.2]}],
        }),
        "chunk_logic": "{}",
        "relations": "{}",
    }

    pending = audit_chunk({**base, "status": "pending"}, {"name": "test.pdf"})
    approved = audit_chunk({**base, "status": "approved"}, {"name": "test.pdf"})

    assert not pending.indexable
    assert not pending.needs_review
    assert [issue.tag for issue in pending.issues] == ["not_approved"]
    assert approved.indexable
    assert approved.issues == []
