"""Phase-4 retrieval ablation helpers: continuation aggregation + reference expansion."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import retrieval_experiments as exp


def _table_candidate(chunk_id: str, *, page: int, table_no: str = "30", text: str = "") -> dict:
    return {
        "chunk_id": chunk_id,
        "file_id": "std-file",
        "page": page,
        "content_type": "table",
        "text": text or f"table fragment {chunk_id}",
        "business_metadata": {
            "content_type": "table",
            "standard_no": "Q/GDW 12126.4-2024",
            "table_no": table_no,
            "table_title": "试验项目及判定标准",
        },
    }


def _section_candidate(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "file_id": "std-file",
        "page": 2,
        "content_type": "section",
        "text": text,
        "business_metadata": {"content_type": "section", "section": "5.1"},
    }


def test_continuation_fragments_merge_into_one_evidence_unit() -> None:
    fragment_a = _table_candidate("t1", page=15, text="表30 第一段")
    fragment_b = _table_candidate("t2", page=16, text="表30（续）第二段")
    other = _table_candidate("t3", page=20, table_no="31")

    units = exp.aggregate_continuation_tables([fragment_b, fragment_a, other])

    assert len(units) == 2
    unit = units[0]
    assert unit["chunk_id"] == "t2"  # best-ranked fragment stays primary
    members = unit["evidence_unit"]["members"]
    assert [member["chunk_id"] for member in members] == ["t1", "t2"]  # page order
    assert unit["text"] == "表30 第一段\n\n表30（续）第二段"
    # Input candidates are not mutated.
    assert fragment_b["text"] == "表30（续）第二段"
    # A lone table still forms its own (single-member) unit.
    assert [m["chunk_id"] for m in units[1]["evidence_unit"]["members"]] == ["t3"]


def test_continuation_completion_pulls_missing_siblings() -> None:
    fragment = _table_candidate("t1", page=15)

    def complete_groups(file_id: str, standard_no: str, table_no: str) -> list[dict]:
        assert (file_id, standard_no, table_no) == (
            "std-file", "Q/GDW 12126.4-2024", "30",
        )
        return [_table_candidate("t1", page=15), _table_candidate("t9", page=17)]

    units = exp.aggregate_continuation_tables([fragment], complete_groups=complete_groups)

    members = units[0]["evidence_unit"]["members"]
    assert [member["chunk_id"] for member in members] == ["t1", "t9"]
    assert members[1]["added_by"] == "continuation_completion"


def test_reference_expansion_appends_referenced_tables() -> None:
    section = _section_candidate("s1", "外施耐压试验电压值见表 7 的规定。")
    already_retrieved = _table_candidate("t7", page=9, table_no="7")

    def fetch(file_id: str, table_no: str) -> list[dict]:
        assert file_id == "std-file"
        if table_no == "7":
            return [already_retrieved, _table_candidate("t7b", page=10, table_no="7")]
        return []

    expanded = exp.expand_table_references(
        [section, already_retrieved], fetch_table_chunks=fetch
    )

    assert [candidate["chunk_id"] for candidate in expanded] == ["s1", "t7", "t7b"]
    assert expanded[-1]["added_by"] == "reference_expansion"
    assert expanded[-1]["source_chunk_id"] == "s1"


def test_reference_expansion_prefers_relations_over_text_regex() -> None:
    candidate = {
        "chunk_id": "s1",
        "file_id": "f",
        "content_type": "section",
        "text": "见表 3",
        "relations": {"references": [{"kind": "table", "no": "K.2", "type": "references_table"}]},
    }
    assert exp.table_references(candidate) == ["K.2"]

    # Without relations, fall back to the text pattern.
    assert exp.table_references(_section_candidate("s2", "空载损耗见表 12，负载损耗见 表 13。")) == ["12", "13"]


def test_non_section_candidates_are_not_expanded() -> None:
    table = _table_candidate("t1", page=3, text="见表 4")

    expanded = exp.expand_table_references(
        [table],
        fetch_table_chunks=lambda *_: [_table_candidate("t4", page=5, table_no="4")],
    )

    assert [candidate["chunk_id"] for candidate in expanded] == ["t1"]
