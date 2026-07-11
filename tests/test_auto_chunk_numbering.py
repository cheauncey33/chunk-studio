from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import extractors
from app.routers import auto_chunks


def _section_unit(section: str, level: int) -> auto_chunks.SectionUnit:
    return auto_chunks.SectionUnit(
        section=section,
        title="test",
        level=level,
        section_path=[{"section": section, "title": "test"}],
        blocks=[],
    )


def test_section_roots_keep_leaf_section_above_target_level() -> None:
    units = [
        _section_unit("9", 1),
        _section_unit("10", 1),
        _section_unit("11", 1),
        _section_unit("11.1", 2),
        _section_unit("11.2", 2),
    ]

    roots = auto_chunks._select_section_roots(units, target_level=2)

    assert [unit.section for unit in roots] == ["9", "10", "11.1", "11.2"]


def test_extracts_standard_no_from_unicode_slash_filename() -> None:
    assert (
        extractors.extract_standard_no("GB∕T 6451-2023 油浸式电力变压器技术参数和要求.pdf")
        == "GB/T 6451-2023"
    )


def test_extracts_q_gdw_standard_no_from_filename() -> None:
    assert (
        extractors.extract_standard_no("Q∕GDW 12126.4-2024 电力变压器技术规范.pdf")
        == "Q/GDW 12126.4-2024"
    )


def test_extracts_jb_t_standard_no_from_filename() -> None:
    assert extractors.extract_standard_no("JB/T 501-2021 transformer test guide.pdf") == "JB/T 501-2021"


def test_splits_figure_number_from_voltage_title() -> None:
    number, title = auto_chunks._parse_numbered_title("图36kV、10kV级低压端子排列", "figure")

    assert number == "3"
    assert title == "6 kV、10 kV级低压端子排列"


def test_splits_table_number_from_voltage_title() -> None:
    number, title = auto_chunks._parse_numbered_title("表16kV、10kV级参数", "table")

    assert number == "1"
    assert title == "6 kV、10 kV级参数"


def test_keeps_normal_multi_digit_table_number() -> None:
    number, title = auto_chunks._parse_numbered_title("表16 220kV级参数", "table", list(range(1, 16)))

    assert number == "16"
    assert title == "220 kV级参数"


def test_normalizes_latex_units_in_numbered_title() -> None:
    number, title = auto_chunks._parse_numbered_title(
        r"表 18 \( {330}\mathrm{{kV}} \) 油浸式三相双绕组无励磁调压电力变压器能效等级",
        "table",
    )

    assert number == "18"
    assert title == "330 kV 油浸式三相双绕组无励磁调压电力变压器能效等级"


def test_normalizes_embedded_latex_units_in_title() -> None:
    number, title = auto_chunks._parse_numbered_title(
        r"表 8 110 kV 油浸式三相双绕组低压为 \( {35}\mathrm{{kV}} \) 无励磁调压电力变压器能效等级",
        "table",
    )

    assert number == "8"
    assert title == "110 kV 油浸式三相双绕组低压为 35 kV 无励磁调压电力变压器能效等级"


def test_normalizes_latex_in_table_columns() -> None:
    metadata = extractors.extract_auto_metadata(
        r"表 1 参数\n\n<table><tr><td>\( {330}\mathrm{{kV}} \)</td><td>容量\(10\mathrm{kVA}\)</td></tr></table>",
        "GB∕T 6451-2023.pdf",
    )

    assert metadata["table_columns"] == ["330 kV", "容量10 kVA"]


def test_preserves_sqrt_and_fraction_meaning_when_normalizing_latex() -> None:
    text = extractors.normalize_latex_text(
        r"峰值除以 \sqrt{2}，频率不低于80\%，持续时间为 \frac{120\times\text{额定频率}}{\text{试验频率}} s"
    )

    assert "sqrt(2)" in text
    assert "80 %" in text
    assert "(120×额定频率)/(试验频率)" in text


def test_bare_number_caption_requires_explicit_flag() -> None:
    assert auto_chunks._parse_numbered_title("16 220 kV 油浸式变压器能效等级", "table") == (None, None)

    number, title = auto_chunks._parse_numbered_title(
        "16 220 kV 油浸式变压器能效等级",
        "table",
        allow_bare_table=True,
    )

    assert number == "16"
    assert title == "220 kV 油浸式变压器能效等级"


def test_finds_side_caption_for_rotated_table() -> None:
    blocks = [
        {"type": "header", "bbox": [0.10, 0.08, 0.23, 0.10], "content": "GB 20052-2024"},
        {"type": "image_caption", "bbox": [0.112, 0.354, 0.137, 0.687], "content": "表 2 10 kV 干式三相双绕组无励磁调压配电变压器能效等级"},
        {"type": "table", "bbox": [0.152, 0.121, 0.866, 0.918], "content": "<table></table>"},
    ]

    found = auto_chunks._find_caption_block(
        blocks,
        2,
        [0.152, 0.121, 0.866, 0.918],
        auto_chunks.AutoTableChunkRequest(),
    )

    assert found is blocks[1]


def test_parses_appendix_table_number() -> None:
    number, title = auto_chunks._parse_numbered_title("表 C.1 典型牌号带材磁密选择表", "table")

    assert number == "C.1"
    assert title == "典型牌号带材磁密选择表"


def test_parses_bare_appendix_table_caption_when_allowed() -> None:
    number, title = auto_chunks._parse_numbered_title(
        "A.4 10 kV三相非晶合金铁心无励磁调压配电变压器损耗水平代号",
        "table",
        allow_bare_table=True,
    )

    assert number == "A.4"
    assert title == "10 kV三相非晶合金铁心无励磁调压配电变压器损耗水平代号"


def test_parses_bare_appendix_continued_table_caption_when_allowed() -> None:
    number, title = auto_chunks._parse_numbered_title("E.4（续）", "table", allow_bare_table=True)

    assert number == "E.4"
    assert title == "(续)"


def test_marks_continued_table_and_inherits_title() -> None:
    metadata = {"table_no": "30", "table_title": "(续)"}

    auto_chunks._apply_table_continuation_metadata(metadata, {"30": "试验项目及判定标准"})

    assert metadata["table_title"] == "试验项目及判定标准"
    assert metadata["split"]["reason"] == "continued_table"
    assert metadata["belongs_to"]["table_no"] == "30"


def test_marks_full_caption_ending_in_continuation() -> None:
    metadata = {"table_no": "K.2", "table_title": "包含负载周期内允许的负载（续）"}

    auto_chunks._apply_table_continuation_metadata(
        metadata,
        {"K.2": "包含负载周期内允许的负载"},
    )

    assert metadata["table_title"] == "包含负载周期内允许的负载"
    assert metadata["split"]["reason"] == "continued_table"


def test_merges_multiline_caption_before_table() -> None:
    blocks = [
        {
            "type": "image_caption",
            "bbox": [0.28, 0.12, 0.73, 0.14],
            "content": "表 K.2 包含负载周期内允许的负载和相应的日寿命损失",
        },
        {
            "type": "image_caption",
            "bbox": [0.32, 0.143, 0.73, 0.159],
            "content": "（以正常天数计）以及最大热点温升的示例表（续）",
        },
        {"type": "table", "bbox": [0.12, 0.17, 0.90, 0.62], "content": "<table></table>"},
    ]

    found = auto_chunks._find_caption_block(
        blocks,
        2,
        [0.12, 0.17, 0.90, 0.62],
        auto_chunks.AutoTableChunkRequest(),
    )

    assert found is not None
    assert found["content"].endswith("（续）")
    assert found["bbox"] == [0.28, 0.12, 0.73, 0.159]


def test_appendix_headings_form_section_tree() -> None:
    model = [[
        {"type": "paragraph_title", "bbox": [0.1, 0.1, 0.4, 0.12], "content": "11 冲击试验报告"},
        {"type": "text", "bbox": [0.1, 0.13, 0.9, 0.2], "content": "正文"},
        {"type": "paragraph_title", "bbox": [0.4, 0.22, 0.6, 0.24], "content": "附录 A"},
        {"type": "paragraph_title", "bbox": [0.1, 0.25, 0.4, 0.27], "content": "A.1 概述"},
        {"type": "text", "bbox": [0.1, 0.28, 0.9, 0.35], "content": "A.1 正文"},
        {"type": "paragraph_title", "bbox": [0.1, 0.36, 0.5, 0.38], "content": "A.2 波前电阻计算"},
        {"type": "text", "bbox": [0.1, 0.39, 0.9, 0.46], "content": "A.2 正文"},
        {"type": "text", "bbox": [0.4, 0.48, 0.6, 0.50], "content": "附录B"},
        {"type": "text", "bbox": [0.1, 0.51, 0.9, 0.58], "content": "附录 B 正文"},
    ]]

    units = auto_chunks._extract_section_units(model)
    roots = auto_chunks._select_section_roots(units, target_level=2)

    assert [unit.section for unit in units] == ["11", "A", "A.1", "A.2", "B"]
    assert [unit.section for unit in roots] == ["11", "A.1", "A.2", "B"]
    assert units[1].blocks[0].text.startswith("附录 A")


def test_appendix_section_heading_is_not_discovered_as_table_caption() -> None:
    assert not auto_chunks._looks_like_table_caption("B.2 声压法——逐点测量附录")
    assert auto_chunks._looks_like_table_caption("表 B.2 声压法——逐点测量附录")
    assert auto_chunks._looks_like_table_caption("E.4 最小空气间隙", allow_bare=True)


def test_adjacent_same_table_columns_are_marked_as_continuation() -> None:
    previous = auto_chunks.Candidate(
        page=15,
        bbox={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.5},
        table_bbox=[0.1, 0.1, 0.9, 0.6],
        caption_bbox=None,
        caption="表 3 试验电压水平",
        table_html="<table></table>",
        block_index=1,
        metadata={"table_no": "3", "table_title": "试验电压水平", "table_columns": ["设备最高电压 U_ m", "冲击"]},
    )
    metadata = {"table_no": "3", "table_title": "试验电压水平", "table_columns": ["设备最高电压 U_m", "冲击"]}

    auto_chunks._apply_adjacent_table_continuation(metadata, page=16, previous=previous)

    assert metadata["table_kind"] == "continued_table"
    assert metadata["split"]["reason"] == "continued_table"


def test_rejects_stale_markdown_caption_from_distant_page() -> None:
    assert auto_chunks._markdown_caption_allowed("表 1 试验接受准则", 20, {"1": 19})
    assert not auto_chunks._markdown_caption_allowed("表 1 试验接受准则", 39, {"1": 19})


def test_extracts_markdown_table_caption_before_table() -> None:
    markdown = """
正文见表 23。

表 23 绕组温升限值

<table><tr><td>绝缘系统温度</td><td>温升限值</td></tr></table>
"""

    entries = auto_chunks._extract_markdown_table_captions(markdown)

    assert len(entries) == 1
    assert entries[0].caption == "表 23 绕组温升限值"


def test_markdown_table_caption_match_supports_split_table_block() -> None:
    markdown = """
表 1 油浸式电力变压器试验项目

<table><tr><td>序号</td><td>试验</td></tr><tr><td>1</td><td>绝缘油试验</td></tr><tr><td>2</td><td>绕组电阻测量</td></tr></table>

表 2 绝缘油耐压规定值

<table><tr><td>电压等级</td><td>击穿电压</td></tr></table>
"""
    entries = auto_chunks._extract_markdown_table_captions(markdown)

    first, cursor = auto_chunks._match_markdown_table_caption(
        "<table><tr><td>序号</td><td>试验</td></tr><tr><td>1</td><td>绝缘油试验</td></tr></table>",
        entries,
        0,
    )
    continued, cursor = auto_chunks._match_markdown_table_caption(
        "<table><tr><td>2</td><td>绕组电阻测量</td></tr></table>",
        entries,
        cursor,
    )

    assert first is not None
    assert first.caption == "表 1 油浸式电力变压器试验项目"
    assert continued is not None
    assert continued.caption == "表 1 油浸式电力变压器试验项目"


def test_classifies_numbered_and_continued_tables() -> None:
    assert auto_chunks._classify_table_kind(
        "表 1 试验项目\n\n<table><tr><td>序号</td></tr></table>",
        {"table_no": "1", "table_title": "试验项目"},
    ) == "numbered_table"
    assert auto_chunks._classify_table_kind(
        "<table><tr><td>续页</td></tr></table>",
        {"table_no": "1", "table_title": "试验项目", "split": {"reason": "continued_table"}},
    ) == "continued_table"


def test_classifies_unnumbered_table_kinds() -> None:
    assert auto_chunks._classify_table_kind(
        "<table><tr><td>符号和缩略语</td><td>含义</td><td>单位</td></tr></table>",
        {"table_columns": ["符号和缩略语", "含义", "单位"]},
    ) == "symbol_table"
    assert auto_chunks._classify_table_kind(
        "<table><tr><td>报告编号</td><td>额定电压%</td><td>额定电流%</td><td>分接位置</td></tr></table>",
        {"table_columns": ["报告编号", "额定电压%", "额定电流%", "分接位置"]},
    ) == "report_form"
    assert auto_chunks._classify_table_kind(
        "<table><tr><td>试验电流幅值</td><td>电流频率</td><td>声波频率</td></tr><tr><td>I_1</td><td>f_1</td><td>2f_1</td></tr></table>",
        {"table_columns": ["试验电流幅值", "电流频率", "声波频率"]},
    ) == "formula_table"
