from backend.app import chunk_schema


def test_migrates_table_trace_and_logic() -> None:
    layers = chunk_schema.migrate_chunk_metadata_v1_to_v2({
        "standard_no": "GB/T 6451-2023",
        "content_type": "table",
        "table_no": "4",
        "auto_source": "mineru_table",
        "mineru_parse_id": "parse_1",
        "mineru_page_idx": 3,
        "mineru_block_index": 17,
        "mineru_table_bbox": [0.1, 0.2, 0.8, 0.6],
    })

    assert layers["metadata_v2"]["standard_no"] == "GB/T 6451-2023"
    assert layers["metadata_v2"]["content_type"] == "table"
    assert layers["metadata_v2"]["table_no"] == "4"
    assert layers["source_trace"]["mineru_parse_id"] == "parse_1"
    assert layers["source_trace"]["page_start"] == 4
    assert layers["source_trace"]["source_blocks"][0]["type"] == "table"
    assert layers["chunk_logic"]["auto_source"] == "mineru_table"
    assert layers["chunk_logic"]["chunk_type"] == "auto_table"


def test_migrates_section_split_fields() -> None:
    layers = chunk_schema.migrate_chunk_metadata_v1_to_v2({
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

    assert layers["metadata_v2"]["section"] == "4.2"
    assert layers["source_trace"]["page_end"] == 2
    assert layers["source_trace"]["bbox_union"] == [0.1, 0.1, 0.9, 0.7]
    assert layers["chunk_logic"]["split_reason"] == "over_max_chars_child_pack"
    assert layers["chunk_logic"]["chunk_parts"] == 2


def test_existing_v2_values_win_over_legacy_fallback() -> None:
    layers = chunk_schema.ensure_layered_chunk(
        metadata={"content_type": "table", "table_no": "1", "auto_source": "mineru_table"},
        metadata_v2={"content_type": "table", "table_no": "2"},
        chunk_logic={},
    )

    assert layers["metadata_v2"]["table_no"] == "2"
    assert layers["chunk_logic"]["auto_source"] == "mineru_table"
