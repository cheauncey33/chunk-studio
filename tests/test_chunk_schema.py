import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import chunk_schema, db


def test_migrates_table_trace_logic_and_relations() -> None:
    layers = chunk_schema.migrate_chunk_metadata_v1({
        "standard_no": "GB/T 6451-2023",
        "content_type": "table",
        "table_no": "4",
        "table_header": "legacy title",
        "table_ref": ["7"],
        "auto_source": "mineru_table",
        "mineru_parse_id": "parse_1",
        "mineru_page_idx": 3,
        "mineru_block_index": 17,
        "mineru_table_bbox": [0.1, 0.2, 0.8, 0.6],
    })

    assert layers["business_metadata"]["standard_no"] == "GB/T 6451-2023"
    assert layers["business_metadata"]["content_type"] == "table"
    assert layers["business_metadata"]["table_no"] == "4"
    assert layers["business_metadata"]["table_title"] == "legacy title"
    assert "table_header" not in layers["business_metadata"]
    assert layers["source_trace"]["parse_id"] == "parse_1"
    assert layers["source_trace"]["page_start"] == 4
    assert layers["source_trace"]["source_blocks"][0]["page"] == 4
    assert layers["source_trace"]["source_blocks"][0]["type"] == "table"
    assert layers["chunk_logic"]["creation_mode"] == "auto"
    assert layers["chunk_logic"]["generator"] == "mineru"
    assert layers["chunk_logic"]["chunk_type"] == "table"
    assert layers["relations"]["references"][0]["kind"] == "table"


def test_migrates_section_split_fields() -> None:
    layers = chunk_schema.migrate_chunk_metadata_v1({
        "content_type": "section",
        "section": "4.2",
        "section_path": [{"section": "4", "title": "技术要求"}],
        "source_blocks": [
            {"page_idx": 0, "block_index": 1, "type": "title", "bbox": [0.1, 0.1, 0.9, 0.2]},
            {"page_idx": 1, "block_index": 2, "type": "text", "bbox": [0.1, 0.2, 0.9, 0.7]},
        ],
        "page_start": 1,
        "page_end": 2,
        "split_from": "4",
        "split_reason": "over_max_chars_child_pack",
        "chunk_part": 1,
        "chunk_parts": 2,
    })

    assert layers["business_metadata"]["section"] == "4.2"
    assert layers["source_trace"]["page_end"] == 2
    assert layers["source_trace"]["source_blocks"][0]["page"] == 1
    assert layers["source_trace"]["bbox_union"] == [0.1, 0.1, 0.9, 0.7]
    assert layers["chunk_logic"]["split"]["reason"] == "over_max_chars_child_pack"
    assert layers["chunk_logic"]["split"]["parts"] == 2


def test_existing_business_values_win_over_legacy_fallback() -> None:
    layers = chunk_schema.ensure_layered_chunk(
        metadata={"content_type": "table", "table_no": "1", "auto_source": "mineru_table"},
        business_metadata={"content_type": "table", "table_no": "2"},
        chunk_logic={},
    )

    assert layers["business_metadata"]["table_no"] == "2"
    assert layers["chunk_logic"]["creation_mode"] == "auto"


def test_upgrades_metadata_v2_database_without_losing_edits(monkeypatch, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "legacy-metadata-v2.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            metadata TEXT NOT NULL DEFAULT '{}',
            metadata_v2 TEXT NOT NULL DEFAULT '{}',
            source_trace TEXT NOT NULL DEFAULT '{}',
            chunk_logic TEXT NOT NULL DEFAULT '{}'
        )"""
    )
    conn.executemany(
        "INSERT INTO chunks(id, metadata, metadata_v2, source_trace, chunk_logic) VALUES(?,?,?,?,?)",
        [
            (
                "old-only",
                json.dumps({
                    "standard_no": "GB/T 1-2026",
                    "table_no": "1",
                    "summary": "flat fallback",
                    "table_ref": ["7"],
                }),
                json.dumps({
                    "table_no": "2",
                    "table_title": "user-edited title",
                    "custom_field": "keep me",
                }),
                "{}",
                "{}",
            ),
            (
                "interrupted-upgrade",
                json.dumps({"table_no": "1", "summary": "flat fallback"}),
                json.dumps({"table_no": "2", "table_title": "v2 title"}),
                "{}",
                "{}",
            ),
        ],
    )
    monkeypatch.setattr(db, "_conn", conn)

    db._migrate_chunk_layer_columns()
    conn.execute(
        "UPDATE chunks SET business_metadata=? WHERE id='interrupted-upgrade'",
        (json.dumps({"table_no": "3", "reviewed": True}),),
    )
    db._backfill_chunk_layers()

    old_row = conn.execute(
        "SELECT business_metadata, relations FROM chunks WHERE id='old-only'"
    ).fetchone()
    old_business = json.loads(old_row["business_metadata"])
    assert old_business == {
        "standard_no": "GB/T 1-2026",
        "table_no": "2",
        "summary": "flat fallback",
        "table_title": "user-edited title",
        "custom_field": "keep me",
    }
    assert json.loads(old_row["relations"])["references"][0]["no"] == "7"

    upgraded = json.loads(conn.execute(
        "SELECT business_metadata FROM chunks WHERE id='interrupted-upgrade'"
    ).fetchone()["business_metadata"])
    assert upgraded == {
        "table_no": "3",
        "reviewed": True,
        "summary": "flat fallback",
        "table_title": "v2 title",
    }

    db._backfill_chunk_layers()
    rerun = json.loads(conn.execute(
        "SELECT business_metadata FROM chunks WHERE id='interrupted-upgrade'"
    ).fetchone()["business_metadata"])
    assert rerun == upgraded
    conn.close()
