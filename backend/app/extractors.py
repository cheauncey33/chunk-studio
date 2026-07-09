"""Programmatic (regex/heuristic) metadata extractors for `auto` fields.

These produce deterministic values — standard_no (from filename), content_type,
table_no, table_header, table_columns, table_ref — that are written into the
stable business metadata layer. The caller merges them *only into empty fields*,
never overwriting user edits.

LLM-derived fields (summary/keywords/scope) live elsewhere and are not touched
here; this module is the cheap, deterministic layer that can run synchronously
on every chunk create / OCR completion.
"""
from __future__ import annotations

import html
import re
from typing import Any

# Standard number from a filename stem, e.g. "GBT 6451-2023", "GB/T 6451-2023",
# "GB 50011-2019", "JGJ 3-2010". Separators between prefix/number/year may be
# space, "+", or nothing.
_STANDARD_NO_RE = re.compile(
    r'(GB(?:/?T)?|JGJ|DBJ|DB|HJ|DL|YY|JT|BB)[\s+]*[\d.]+[\s+]*[-—-][\s+]*\d{4}',
    re.IGNORECASE,
)

# The chunk's own table number/title. Only meaningful when the chunk actually
# contains a table (see extract_auto_metadata). The title pattern intentionally
# stops at a line break or the table markup so it captures:
#   表 1 6 kV、10 kV 级 ... 配电变压器
# as:
#   table_no=1, table_header=6 kV、10 kV 级 ... 配电变压器
_TABLE_NO_RE = re.compile(r'表\s*0*(\d+)')
_TABLE_TITLE_RE = re.compile(
    r'表\s*0*(?P<no>\d+)\s+'
    r'(?P<title>[^\r\n<|]+?(?:变压器|电抗器|开关设备|电缆|导线|母线|装置|设备|参数|要求|限值|数据|性能|特性|表|值)?)'
    r'(?=\s*(?:\r?\n|<table\b|\||$))',
    re.IGNORECASE,
)

# References to *other* tables: "见表3", "如表1所示", "参见表5", "表2所列".
# Two alternatives because the cue word may precede or follow the number.
_TABLE_REF_RE = re.compile(
    r'(?:详见|参见|见|如)\s*表\s*0*(\d+)|表\s*0*(\d+)\s*(?:所示|所列|中|如下)',
)

# First <tr>...</tr> of the first <table> in the text.
_FIRST_TR_RE = re.compile(r'<table[^>]*>(.*?)</tr>', re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')

_TABLE_OPEN_RE = re.compile(r'<table[\s>]', re.IGNORECASE)


def extract_standard_no(filename: str | None) -> str | None:
    """Pull a standard number like 'GB/T 6451-2023' from the filename."""
    if not filename:
        return None
    stem = re.sub(r'\.[^.]+$', '', filename)  # strip extension
    m = _STANDARD_NO_RE.search(stem)
    if not m:
        return None
    s = m.group(0).upper()
    s = re.sub(r'^GBT', 'GB/T', s)
    s = s.replace('+', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'\s*-\s*', '-', s)
    return s or None


def _strip_cell(cell: str) -> str:
    cell = _TAG_RE.sub('', cell)
    cell = html.unescape(cell)
    return re.sub(r'\s+', ' ', cell).strip()


def _extract_table_columns(text: str) -> list[str]:
    m = _FIRST_TR_RE.search(text)
    if not m:
        return []
    cells = _CELL_RE.findall(m.group(1))
    header = [_strip_cell(c) for c in cells]
    return [c for c in header if c]


def _text_before_first_table(text: str) -> str:
    """Return caption-like text before the first table, with HTML tags removed."""
    before = re.split(r'<table\b', text, maxsplit=1, flags=re.IGNORECASE)[0]
    before = re.sub(r'</(?:p|div|h\d|br|tr|table)>', '\n', before, flags=re.IGNORECASE)
    before = _TAG_RE.sub('', before)
    before = html.unescape(before)
    before = before.replace('\u3000', ' ')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in before.splitlines()]
    return '\n'.join(line for line in lines if line)


def _extract_table_caption(text: str) -> tuple[str | None, str | None]:
    """Extract this chunk's table number and caption/title.

    Prefer the text before the first <table>, because OCR often emits a caption
    line followed by HTML table markup. Fall back to the full first kilobyte so
    markdown-like table text can still be handled.
    """
    candidates = [_text_before_first_table(text), text[:1000]]
    for candidate in candidates:
        for m in _TABLE_TITLE_RE.finditer(candidate):
            title = re.sub(r'\s+', ' ', m.group('title')).strip(' ：:;；')
            if title:
                return m.group('no'), title
    no = _extract_table_no(text)
    return no, None


def _extract_table_no(text: str) -> str | None:
    m = _TABLE_NO_RE.search(text)
    return m.group(1) if m else None


def _extract_table_ref(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for m in _TABLE_REF_RE.finditer(text):
        n = m.group(1) or m.group(2)
        if n and n not in seen:
            seen.add(n)
            refs.append(n)
    return refs


def _extract_content_type(text: str) -> str | None:
    if not text or not text.strip():
        return None
    return 'table' if _TABLE_OPEN_RE.search(text) else 'text'


def extract_auto_metadata(text: str | None, filename: str | None) -> dict[str, Any]:
    """Return the auto-extractable fields for a chunk.

    Pure function; caller merges into metadata respecting the "don't overwrite
    non-empty" rule (see merge_auto_metadata).
    """
    text = text or ''
    out: dict[str, Any] = {}

    sn = extract_standard_no(filename)
    if sn:
        out['standard_no'] = sn

    ct = _extract_content_type(text)
    if ct:
        out['content_type'] = ct

    # table_no / table_header only make sense for chunks that actually contain
    # a table; otherwise "表3" in running prose is a reference, not this chunk's
    # own number.
    if ct == 'table':
        tn, title = _extract_table_caption(text)
        if tn:
            out['table_no'] = tn
        if title:
            out['table_header'] = title
        columns = _extract_table_columns(text)
        if columns:
            out['table_columns'] = columns

    refs = _extract_table_ref(text)
    if refs:
        out['table_ref'] = refs

    return out


def _is_empty(value: Any) -> bool:
    return value is None or value == '' or value == [] or value == {}


def _is_legacy_table_header(key: str, value: Any) -> bool:
    """Old builds stored table column cells in table_header as a list."""
    return key == 'table_header' and isinstance(value, list)


def merge_auto_metadata(
    meta: dict[str, Any], text: str | None, filename: str | None
) -> dict[str, Any]:
    """Merge auto-extracted fields into `meta`, only filling empty slots.

    Returns the (mutated) meta dict. Non-empty values — including user edits —
    are never overwritten, so re-running after OCR or a manual edit is safe.
    """
    auto = extract_auto_metadata(text, filename)
    for key, value in auto.items():
        existing = meta.get(key)
        if _is_empty(existing) or _is_legacy_table_header(key, existing):
            meta[key] = value
    return meta
