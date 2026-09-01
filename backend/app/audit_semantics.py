"""Generic deterministic semantics for the fixed audit workflow.

The module contains syntax and relation primitives only.  Standard clauses,
project names, expected answers, and evaluation examples must arrive as report
or retrieved evidence data; none of them may select a code path here.
"""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import math
import re
from typing import Any


EVIDENCE_ROLES = (
    "nominal_rule",
    "tolerance_rule",
    "method_rule",
    "applicability_rule",
)

_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_TAG_RE = re.compile(r"<[^>]+>")
_UNIT_RE = re.compile(
    r"(?i)(kva|mva|khz|mhz|kv|ka|kw|mw|ms|μs|us|ma|va|db(?:a)?|hz|s|%|v|a|w)"
)
# Convert only unambiguous scales. milli/mega collisions such as "MV" are refused.
_UNIT_BASE: dict[str, tuple[str, float]] = {
    "w": ("w", 1.0),
    "kw": ("w", 1000.0),
    "mw": ("w", 1_000_000.0),
    "va": ("va", 1.0),
    "kva": ("va", 1000.0),
    "mva": ("va", 1_000_000.0),
    "v": ("v", 1.0),
    "kv": ("v", 1000.0),
    "a": ("a", 1.0),
    "ka": ("a", 1000.0),
    "ma": ("a", 0.001),
    "hz": ("hz", 1.0),
    "khz": ("hz", 1000.0),
    "mhz": ("hz", 1_000_000.0),
    "s": ("s", 1.0),
    "ms": ("s", 0.001),
    "us": ("s", 1e-6),
    "μs": ("s", 1e-6),
    "%": ("ratio", 0.01),
    "db": ("db", 1.0),
    "dba": ("db", 1.0),
}
_ASCII_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]*", re.IGNORECASE)
_VALUE_AFTER_SEPARATOR_RE = re.compile(r"[:：]\s*(.+)$", re.DOTALL)
_PRESCRIPTIVE_RE = re.compile(r"(?:应|应该|应当|不得|不应|至少|至多|必须|允许|限值|额定|规定)")
_CONDITIONAL_RE = re.compile(r"(?:如果|当|在.+?时|对于|适用|除非|取决于|条件|范围)")
_PROCEDURAL_RE = re.compile(r"(?:方法|程序|步骤|测量|施加|记录|进行|按照|依次)")
_TOLERANCE_RE = re.compile(r"(?:±|偏差|公差|容差|允许范围)")
_CONSTRAINT_RE = re.compile(
    r"(?P<field>[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,20})\s*"
    r"(?P<operator><=|>=|≤|≥|<|>|=|等于|不大于|不小于|不超过|至少)\s*"
    r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>[A-Za-zμ%]+)?",
    re.IGNORECASE,
)
_SCOPE_RE = re.compile(
    r"(?:(?P<total>总(?:计|共|数|数量|次数)?|共)\s*(?P<total_count>\d+(?:\.\d+)?)|"
    r"(?P<per>每|各)\s*(?P<denominator>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z_-]{0,15})"
    r"(?:\s*(?P<per_count>\d+(?:\.\d+)?))?)"
)


def _plain(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<eq\b[^>]*>|</eq>", "", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normal(value: Any) -> str:
    return _plain(value).lower().replace("_", "").replace(" ", "")


def _numbers(value: Any) -> list[float]:
    text = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", _plain(value))
    return [float(match.group(0).replace(",", ".")) for match in _NUMBER_RE.finditer(text)]


def _profile_entries(sample_profile: dict[str, Any] | None) -> list[tuple[str, str]]:
    profile = sample_profile if isinstance(sample_profile, dict) else {}
    entries: list[tuple[str, str]] = []
    for section_name in ("from_report", "from_model_decode"):
        section = profile.get(section_name)
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                entries.append((str(key), str(value)))
            elif isinstance(value, dict):
                entries.extend((str(k), str(v)) for k, v in value.items() if str(v).strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        entries.extend((str(k), str(v)) for k, v in item.items() if str(v).strip())
                    elif str(item).strip():
                        entries.append((str(key), str(item)))
    return entries


def _find_entry(entries: list[tuple[str, str]], aliases: tuple[str, ...]) -> tuple[str, str] | None:
    normalized_aliases = tuple(_normal(alias) for alias in aliases)
    for key, value in entries:
        normalized = _normal(key)
        if any(alias in normalized or normalized in alias for alias in normalized_aliases):
            return key, value
    return None


def _first_quantity(entry: tuple[str, str] | None) -> tuple[float | None, dict[str, Any] | None]:
    if entry is None:
        return None, None
    values = _numbers(entry[1])
    if not values:
        return None, None
    return values[0], {"field": entry[0], "raw": entry[1], "mode": "reported"}


def resolve_applicability(
    sample_profile: dict[str, Any] | None,
    *,
    project_name: str = "",
    requirement_text: str = "",
) -> dict[str, Any]:
    """Expose reported parameters without selecting a domain branch.

    ``project_name`` and ``requirement_text`` remain keyword-only compatibility
    arguments for callers, but deliberately do not influence the result.
    Applicability is resolved later from retrieved constraints.
    """
    del project_name, requirement_text
    entries = _profile_entries(sample_profile)
    highest_entry = _find_entry(entries, ("um", "设备最高电压", "equipment_highest_voltage"))
    capacity_entry = _find_entry(entries, ("rated_capacity", "额定容量", "capacity"))
    voltage_entry = _find_entry(
        entries,
        ("rated_voltage", "额定电压", "电压比", "系统标称电压", "nominal_voltage"),
    )
    um, um_source = _first_quantity(highest_entry)
    capacity, capacity_source = _first_quantity(capacity_entry)
    voltage_values = _numbers(voltage_entry[1]) if voltage_entry else []
    system_voltage = max(voltage_values) if voltage_values else None
    low_voltage = min(voltage_values) if len(voltage_values) > 1 else None
    parameters = {
        "system_nominal_voltage_kv": system_voltage,
        "rated_voltage_high_kv": system_voltage,
        "rated_voltage_low_kv": low_voltage,
        "um_kv": um,
        "capacity_kva": capacity,
    }
    return {
        "version": 2,
        "state": "parameters_only",
        "parameters": parameters,
        "constraints": [],
        "required_parameters": [],
        "resolved": False,
        "sources": {
            "system_nominal_voltage": (
                {"field": voltage_entry[0], "raw": voltage_entry[1], "mode": "reported"}
                if voltage_entry
                else None
            ),
            "um": um_source,
            "capacity": capacity_source,
        },
    }


def _semantic_tokens(value: Any) -> set[str]:
    text = _normal(value)
    text = _UNIT_RE.sub("", text)
    ascii_tokens = {token.lower() for token in _ASCII_TOKEN_RE.findall(text) if len(token) > 1}
    chinese = "".join(char for char in text if "\u4e00" <= char <= "\u9fff")
    grams = {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}
    if len(chinese) == 1:
        grams.add(chinese)
    return ascii_tokens | grams


def semantic_similarity(left: Any, right: Any) -> float:
    """Return a project-agnostic lexical concept score."""
    left_tokens = _semantic_tokens(left)
    right_tokens = _semantic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    score = len(overlap) / max(1, min(len(left_tokens), len(right_tokens)))
    left_normal, right_normal = _normal(left), _normal(right)
    if left_normal in right_normal or right_normal in left_normal:
        score = max(score, 0.9)
    return score


def parse_scope(text: str) -> dict[str, Any]:
    totals: list[float] = []
    per: list[dict[str, Any]] = []
    for match in _SCOPE_RE.finditer(_plain(text)):
        if match.group("total_count"):
            totals.append(float(match.group("total_count")))
        elif match.group("denominator"):
            per.append({
                "denominator": match.group("denominator"),
                "count": float(match.group("per_count")) if match.group("per_count") else None,
            })
    if totals and per:
        basis = "mixed"
    elif totals:
        basis = "total"
    elif per:
        basis = "per_entity"
    else:
        basis = "unknown"
    return {"basis": basis, "totals": totals, "per_entities": per}


def _value_kind(raw: str) -> tuple[str, str]:
    text = _plain(raw)
    normalized = text.replace("×", "*").replace("÷", "/").replace("√", "sqrt")
    if re.search(r"[A-Za-z]", normalized) and re.search(r"[+*/()]", normalized):
        return "expression", normalized
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", normalized):
        return "enum", normalized
    if _NUMBER_RE.search(normalized):
        if re.search(r"(?:至|到|~|～)", normalized) or len(_numbers(normalized)) >= 2 and any(
            marker in normalized for marker in ("≤", "<", "≥", ">")
        ):
            return "range", normalized
        return "quantity", normalized
    return "text", normalized


def _operator(text: str) -> str:
    normalized = _plain(text)
    if "≤" in normalized or "<=" in normalized or "不大于" in normalized or "不应超过" in normalized:
        return "le"
    if "≥" in normalized or ">=" in normalized or "不小于" in normalized or "不低于" in normalized:
        return "ge"
    if "<" in normalized:
        return "lt"
    if ">" in normalized:
        return "gt"
    if re.search(r"(?:至|到|~|～)", normalized):
        return "between"
    return "eq"


def parse_claim(text: str, *, evidence_role: str | None = None) -> dict[str, Any]:
    """Parse one report/evidence statement into a generic claim envelope."""
    plain = _plain(text)
    separator = _VALUE_AFTER_SEPARATOR_RE.search(plain)
    if separator:
        property_text = plain[:separator.start()].strip(" ，,。;")
        raw_value = separator.group(1).strip()
    else:
        prescribed = re.search(r"^(.{1,120}?)(?:应为|为|等于)\s*(.+)$", plain)
        property_text = prescribed.group(1).strip(" ，,。;") if prescribed else plain
        raw_value = prescribed.group(2).strip() if prescribed else ""
    kind, normalized_value = _value_kind(raw_value)
    unit_match = _UNIT_RE.search(raw_value) or _UNIT_RE.search(plain)
    return {
        "property": {"concept": _normal(property_text), "source_text": property_text},
        "value": {
            "kind": kind,
            "raw": raw_value,
            "normalized": normalized_value,
            "numbers": _numbers(raw_value),
            "unit": unit_match.group(0).lower() if unit_match else None,
            "operator": _operator(raw_value),
        },
        "scope": parse_scope(plain),
        "conditions": [],
        "evidence_role": evidence_role,
    }


_BARE_UNIT_PLACEHOLDERS = {"", "/", "-", "—", "–", "无", "n/a", "na"}
_PRESCRIBED_VALUE_RE = re.compile(r"^(.{1,120}?)(?:应为|为|等于)\s*(.+)$")


def _prescribed_value_is_structured(raw_value: str) -> bool:
    text = _plain(raw_value)
    if not text:
        return False
    if _NUMBER_RE.search(text):
        return True
    kind, _normalized = _value_kind(text)
    return kind in {"enum", "boolean"}


def _requirement_property_and_value(
    requirement_text: str,
    project_name: str = "",
) -> tuple[str, str, str]:
    """Split one requirement into property/value using syntax only."""
    plain = _plain(requirement_text)
    separator = _VALUE_AFTER_SEPARATOR_RE.search(plain)
    if separator:
        return (
            plain[:separator.start()].strip(" ，,。;"),
            separator.group(1).strip(),
            "separator",
        )
    prescribed = _PRESCRIBED_VALUE_RE.search(plain)
    if prescribed and _prescribed_value_is_structured(prescribed.group(2)):
        return (
            prescribed.group(1).strip(" ，,。;"),
            prescribed.group(2).strip(),
            "prescribed",
        )
    constraint = _CONSTRAINT_RE.search(plain)
    if constraint:
        field = constraint.group("field")
        start = constraint.start()
        return field, plain[start + len(field):].strip(), "constraint"
    has_value_shape = bool(_NUMBER_RE.search(plain)) or _operator(plain) != "eq"
    project = _plain(project_name)
    if has_value_shape:
        if project:
            return project, plain, "project_and_text"
        return "", plain, "value_only"
    if project:
        return project, plain, "unstructured_with_project"
    return plain, "", "unparsed"


def _inherit_requirement_unit(
    claim: dict[str, Any],
    unit: str | None,
) -> dict[str, Any] | None:
    value = claim.get("value") if isinstance(claim.get("value"), dict) else {}
    if value.get("unit"):
        return None
    text = str(unit or "").strip()
    if text.lower() in _BARE_UNIT_PLACEHOLDERS:
        return None
    match = _UNIT_RE.search(text)
    if not match:
        return None
    nested = dict(claim)
    nested["value"] = {**value, "unit": match.group(0).lower()}
    return nested


def _requirement_claim_program_ready(claim: dict[str, Any]) -> tuple[bool, str]:
    value = claim.get("value") if isinstance(claim.get("value"), dict) else {}
    kind = str(value.get("kind") or "")
    numbers = value.get("numbers") or []
    raw = str(value.get("raw") or "").strip()
    if kind == "expression":
        return False, "expression"
    if kind in {"quantity", "range"}:
        if len(numbers) == 1:
            return True, "scalar_quantity"
        if len(numbers) > 1:
            return False, "multiple_numbers"
        return False, "missing_number"
    if kind == "enum" and raw:
        return True, "categorical"
    if kind == "boolean":
        return True, "boolean"
    if kind == "text" and raw and re.fullmatch(r"[A-Za-z0-9_.+-]+", raw):
        return True, "categorical"
    return False, "unstructured_text"


def extract_requirement_claim(
    requirement_text: str,
    *,
    unit: str | None = None,
    project_name: str = "",
) -> dict[str, Any]:
    """Unify one reported detection requirement into a claim envelope.

    ``project_name`` is only a property label when the requirement text has no
    identifiable property. It never selects a specialized code path.
    """
    nodes: list[dict[str, Any]] = []
    property_text, raw_value, split_method = _requirement_property_and_value(
        requirement_text,
        project_name,
    )
    value_source = raw_value if raw_value else ""
    if not value_source and split_method in {"unparsed", "unstructured_with_project"}:
        value_source = _plain(requirement_text)
    kind, normalized_value = _value_kind(value_source)
    unit_match = _UNIT_RE.search(value_source) or _UNIT_RE.search(_plain(requirement_text))
    claim = {
        "property": {"concept": _normal(property_text), "source_text": property_text},
        "value": {
            "kind": kind,
            "raw": value_source,
            "normalized": normalized_value,
            "numbers": _numbers(value_source),
            "unit": unit_match.group(0).lower() if unit_match else None,
            "operator": _operator(value_source or requirement_text),
        },
        "scope": parse_scope(requirement_text),
        "conditions": [],
        "evidence_role": None,
    }
    nodes.append({
        "node": "unify_requirement",
        "state": "ok",
        "split_method": split_method,
        "property": property_text,
        "raw_value": value_source,
        "kind": kind,
        "operator": claim["value"]["operator"],
        "unit": claim["value"]["unit"],
    })
    inherited = _inherit_requirement_unit(claim, unit)
    if inherited is not None:
        claim = inherited
        nodes.append({
            "node": "inherit_requirement_unit",
            "state": "applied",
            "unit": claim["value"]["unit"],
        })
    elif unit and str(unit).strip().lower() not in _BARE_UNIT_PLACEHOLDERS:
        nodes.append({
            "node": "inherit_requirement_unit",
            "state": "skipped",
            "reason": "already_has_unit" if claim["value"].get("unit") else "unrecognized_unit",
            "source_unit": unit,
        })
    ready, reason = _requirement_claim_program_ready(claim)
    nodes.append({
        "node": "program_ready",
        "state": "yes" if ready else "no",
        "reason_code": reason,
    })
    return {
        "claim": claim,
        "program_ready": ready,
        "reason_code": reason,
        "split_method": split_method,
        "source_text": requirement_text,
        "source_unit": unit,
        "nodes": nodes,
    }


def classify_evidence_roles(candidate: dict[str, Any]) -> list[str]:
    """Classify reusable linguistic functions, never domain projects."""
    metadata = candidate.get("business_metadata") or {}
    text = _plain(candidate.get("text"))
    title = " ".join(
        str(metadata.get(name) or "")
        for name in ("table_title", "section_title")
    )
    blob = f"{title} {text}"
    roles: list[str] = []
    is_table = str(metadata.get("content_type") or candidate.get("content_type") or "") == "table"
    if _TOLERANCE_RE.search(blob):
        roles.append("tolerance_rule")
    if _CONDITIONAL_RE.search(blob):
        roles.append("applicability_rule")
    if is_table or _PRESCRIPTIVE_RE.search(blob):
        roles.append("nominal_rule")
    if _PROCEDURAL_RE.search(blob):
        roles.append("method_rule")
    if not roles:
        roles.append("method_rule")
    return [role for role in EVIDENCE_ROLES if role in roles]


def extract_applicability_constraints(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract explicit scalar predicates without interpreting domain branches."""
    constraints: list[dict[str, Any]] = []
    text = _plain(candidate.get("text"))
    for match in _CONSTRAINT_RE.finditer(text):
        constraints.append({
            "field": match.group("field"),
            "operator": _operator(match.group("operator")),
            "value": float(match.group("value").replace(",", ".")),
            "unit": (match.group("unit") or "").lower() or None,
            "source_text": match.group(0),
            "candidate_key": candidate.get("candidate_key"),
        })
    return constraints


def _constraint_parameter(field: str, parameters: dict[str, Any]) -> tuple[str, float] | None:
    definitions = (
        ("system_nominal_voltage_kv", ("system_nominal_voltage", "系统标称电压", "标称电压")),
        ("rated_voltage_high_kv", ("rated_voltage", "额定电压", "电压")),
        ("um_kv", ("um", "设备最高电压", "equipment_highest_voltage")),
        ("capacity_kva", ("capacity", "容量", "rated_capacity")),
    )
    ranked: list[tuple[float, str, float]] = []
    for name, aliases in definitions:
        value = parameters.get(name)
        if value is None:
            continue
        ranked.append((max(semantic_similarity(field, alias) for alias in aliases), name, float(value)))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < 0.5:
        return None
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.1:
        return None
    return ranked[0][1], ranked[0][2]


def evaluate_constraint(constraint: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one explicit predicate against reported parameters."""
    resolved = _constraint_parameter(str(constraint.get("field") or ""), parameters)
    if resolved is None:
        return {**constraint, "state": "unresolved", "reason": "parameter_not_bound"}
    parameter, actual = resolved
    expected = float(constraint["value"])
    operator = str(constraint.get("operator") or "eq")
    satisfied = {
        "eq": math.isclose(actual, expected, rel_tol=1e-9),
        "le": actual <= expected,
        "lt": actual < expected,
        "ge": actual >= expected,
        "gt": actual > expected,
    }.get(operator)
    if satisfied is None:
        return {**constraint, "state": "unresolved", "reason": "operator_not_supported"}
    return {
        **constraint,
        "state": "satisfied" if satisfied else "failed",
        "parameter": parameter,
        "actual": actual,
    }


def evaluate_candidate_applicability(
    candidates: list[dict[str, Any]],
    applicability: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate each evidence candidate independently; never choose a branch."""
    parameters = applicability.get("parameters") or {}
    evaluations: list[dict[str, Any]] = []
    for candidate in candidates:
        constraints = candidate.get("applicability_constraints") or []
        if not constraints:
            continue
        results = [evaluate_constraint(item, parameters) for item in constraints]
        states = {item["state"] for item in results}
        state = "unresolved" if "unresolved" in states else "not_applicable" if "failed" in states else "applicable"
        evaluations.append({
            "candidate_key": candidate.get("candidate_key"),
            "state": state,
            "constraints": results,
        })
    return evaluations


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._rowspan = 1
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []
            values = {str(key).lower(): value for key, value in attrs}
            self._rowspan = max(1, int(values.get("rowspan") or 1))
            self._colspan = max(1, int(values.get("colspan") or 1))

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(
                (_plain(" ".join(self._cell)), self._rowspan, self._colspan)
            )
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _html_table(text: str) -> tuple[list[str], list[list[str]]]:
    parser = _TableParser()
    parser.feed(str(text or ""))
    if len(parser.rows) < 2:
        return [], []
    expanded: list[list[str]] = []
    spans: dict[int, tuple[int, str]] = {}
    for raw_row in parser.rows:
        row: list[str] = []
        column = 0

        def consume_spans() -> None:
            nonlocal column
            while column in spans:
                remaining, value = spans[column]
                row.append(value)
                if remaining <= 1:
                    del spans[column]
                else:
                    spans[column] = (remaining - 1, value)
                column += 1

        consume_spans()
        for value, rowspan, colspan in raw_row:
            consume_spans()
            for offset in range(colspan):
                row.append(value)
                if rowspan > 1:
                    spans[column + offset] = (rowspan - 1, value)
            column += colspan
        consume_spans()
        expanded.append(row)
    width = max(len(row) for row in expanded)
    expanded = [row + [""] * (width - len(row)) for row in expanded]

    first_row_spans = [rowspan for _value, rowspan, _colspan in parser.rows[0]]
    first_row_has_grouped_columns = any(
        colspan > 1 for _value, _rowspan, colspan in parser.rows[0]
    )
    header_depth = max(first_row_spans, default=1) if first_row_has_grouped_columns else 1
    header_depth = min(header_depth, len(expanded) - 1)
    if header_depth >= len(expanded):
        return [], []
    headers = []
    for column in range(width):
        path: list[str] = []
        for row in expanded[:header_depth]:
            value = row[column]
            if value and (not path or value != path[-1]):
                path.append(value)
        headers.append(" / ".join(path))
    return headers, expanded[header_depth:]


def _cell_matches(cell: str, target: float) -> bool:
    values = _numbers(cell)
    if not values:
        return False
    operator = _operator(cell)
    if operator in {"le", "lt"}:
        return target <= values[0] + 1e-9 if operator == "le" else target < values[0]
    if operator in {"ge", "gt"}:
        return target >= values[0] - 1e-9 if operator == "ge" else target > values[0]
    if operator == "between" and len(values) >= 2:
        lower, upper = sorted(values[:2])
        return lower - 1e-9 <= target <= upper + 1e-9
    return any(abs(value - target) <= max(1e-6, abs(target) * 1e-4) for value in values)


def _profile_selectors(applicability: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = applicability.get("parameters") or {}
    definitions = (
        ("system_nominal_voltage_kv", ("system_nominal_voltage", "系统标称电压", "标称电压")),
        ("um_kv", ("um", "设备最高电压", "equipment_highest_voltage")),
        ("capacity_kva", ("capacity", "容量", "rated_capacity")),
    )
    selectors = []
    for name, aliases in definitions:
        value = parameters.get(name)
        if value is not None:
            selectors.append({"parameter": name, "value": float(value), "aliases": aliases})
    return selectors


def _alias_row_selectors(
    headers: list[str],
    applicability: dict[str, Any],
    *,
    exclude_headers: set[str],
) -> list[tuple[int, str, float]]:
    selectors: list[tuple[int, str, float]] = []
    profile = _profile_selectors(applicability)
    if not profile:
        return selectors
    for index, header in enumerate(headers):
        if header in exclude_headers:
            continue
        ranked = sorted(
            (
                (max(semantic_similarity(header, alias) for alias in selector["aliases"]), selector)
                for selector in profile
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0.5:
            selector = ranked[0][1]
            selectors.append((index, str(selector["parameter"]), float(selector["value"])))
    return selectors


def _cell_match_kind(cell: str, target: float) -> str | None:
    values = _numbers(cell)
    if not values:
        return None
    if _operator(cell) == "eq" and any(
        abs(value - target) <= max(1e-6, abs(target) * 1e-4) for value in values
    ):
        return "exact"
    if _cell_matches(cell, target):
        return "range"
    return None


def _sample_matching_rows(
    headers: list[str],
    rows: list[list[str]],
    applicability: dict[str, Any],
    *,
    exclude_headers: set[str],
) -> list[tuple[int, list[str], list[dict[str, Any]]]]:
    """Match rows whose non-property cells agree with sample parameters.

    Exact numeric cells beat inequality class labels so a 10 kV sample does not
    bind every ``≤35 / ≤66 / ≤110`` row at once.
    """
    profile = _profile_selectors(applicability)
    if not profile or not rows:
        return []
    scored: list[tuple[str, int, list[str], list[dict[str, Any]]]] = []
    for row_index, row in enumerate(rows, start=1):
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        kinds: list[str] = []
        for index, header in enumerate(headers):
            if header in exclude_headers or index >= len(row):
                continue
            for selector in profile:
                name = str(selector["parameter"])
                if name in seen:
                    continue
                kind = _cell_match_kind(row[index], float(selector["value"]))
                if not kind:
                    continue
                hits.append({
                    "parameter": name,
                    "target": float(selector["value"]),
                    "column": header,
                    "cell": row[index],
                    "match_kind": kind,
                })
                seen.add(name)
                kinds.append(kind)
                break
        if hits:
            scored.append(("exact" if "exact" in kinds else "range", row_index, row, hits))
    if any(kind == "exact" for kind, _index, _row, _hits in scored):
        scored = [item for item in scored if item[0] == "exact"]
    return [(index, row, hits) for _kind, index, row, hits in scored]


def bind_table_row(
    candidate: dict[str, Any],
    applicability: dict[str, Any],
    *,
    exclude_headers: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return a binding only for one fully satisfied row.

    Partial and multiple matches remain explicit non-authoritative states.
    Tables without applicability columns stay eligible: a single data row binds,
    otherwise remaining columns may match sample parameter values.
    """
    metadata = candidate.get("business_metadata") or {}
    if str(metadata.get("content_type") or candidate.get("content_type") or "") != "table":
        return None
    headers, rows = _html_table(str(candidate.get("text") or ""))
    if not headers or not rows:
        return None
    excluded = set(exclude_headers or [])
    selectors = _alias_row_selectors(headers, applicability, exclude_headers=excluded)
    matches_by_row: list[tuple[int, list[str], list[dict[str, Any]]]] = []
    if selectors:
        for row_index, row in enumerate(rows, start=1):
            matches = [
                {"parameter": name, "target": target, "column": headers[column], "cell": row[column]}
                for column, name, target in selectors
                if column < len(row) and _cell_matches(row[column], target)
            ]
            if len(matches) == len(selectors):
                matches_by_row.append((row_index, row, matches))
        state = "matched" if len(matches_by_row) == 1 else "ambiguous" if matches_by_row else "unresolved"
    else:
        matches_by_row = _sample_matching_rows(
            headers,
            rows,
            applicability,
            exclude_headers=excluded,
        )
        if len(matches_by_row) == 1:
            state = "matched"
        elif matches_by_row:
            state = "ambiguous"
        elif len(rows) == 1:
            matches_by_row = [(1, rows[0], [])]
            state = "matched"
        else:
            state = "unresolved"
    result: dict[str, Any] = {
        "state": state,
        "matched": state == "matched",
        "selector_count": len(selectors) if selectors else (len(matches_by_row[0][2]) if matches_by_row else 0),
        "selector_matches": len(matches_by_row[0][2]) if len(matches_by_row) == 1 else (len(selectors) if matches_by_row else 0),
        "headers": headers,
        "candidate_row_count": len(matches_by_row) if matches_by_row else 0,
    }
    if state == "matched":
        row_index, row, matches = matches_by_row[0]
        result.update({
            "row_index": row_index,
            "row": row,
            "column_values": {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)},
            "matched_parameters": matches,
        })
    return result


def annotate_candidates(candidates: list[dict[str, Any]], applicability: dict[str, Any]) -> list[dict[str, Any]]:
    for candidate in candidates:
        candidate["evidence_roles"] = classify_evidence_roles(candidate)
        candidate["applicability_constraints"] = extract_applicability_constraints(candidate)
        binding = bind_table_row(candidate, applicability)
        if binding:
            candidate["table_row_binding"] = binding
        else:
            candidate.pop("table_row_binding", None)
    return candidates


def _claim_property_text(claim: dict[str, Any]) -> str:
    return str((claim.get("property") or {}).get("source_text") or "")


def _header_core_text(header: str) -> str:
    return _UNIT_RE.sub("", _normal(header))


def _header_qualifies_property(property_text: str, header: str) -> bool:
    token = _plain(_UNIT_RE.sub("", header)).strip(" /")
    if not token:
        return False
    escaped = re.escape(token)
    return re.search(rf"[\(（]{escaped}[\)）]", _plain(property_text)) is not None


def _property_header_similarity(property_text: str, header: str) -> float:
    """Score a table header against a claim property without short-token false hits."""
    if not _plain(property_text) or not _plain(header):
        return 0.0
    if len(_header_core_text(header)) <= 2:
        return 1.0 if _header_qualifies_property(property_text, header) else 0.0
    return semantic_similarity(property_text, header)


def _rank_property_on_table(
    report_claim: dict[str, Any],
    headers: list[str],
    *,
    table_title: str = "",
    condition_headers: set[str] | None = None,
) -> dict[str, Any]:
    property_text = _claim_property_text(report_claim)
    skipped = set(condition_headers or [])
    ranked = sorted(
        (
            {
                "score": _property_header_similarity(property_text, header),
                "header": header,
                "normalized_header": _normal(header),
            }
            for header in headers
            if header not in skipped
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    title_score = semantic_similarity(property_text, table_title) if table_title else 0.0
    node = {
        "node": "property_match",
        "normalized_property": _normal(property_text),
        "ranked": ranked[:3],
        "title_score": title_score,
    }
    if ranked and ranked[0]["score"] >= 0.35:
        if len(ranked) == 1 or abs(ranked[0]["score"] - ranked[1]["score"]) >= 0.1:
            return {
                **node,
                "state": "unique",
                "header": ranked[0]["header"],
                "score": ranked[0]["score"],
                "source": "header",
            }
        return {**node, "state": "ambiguous", "header": None}
    if title_score >= 0.5:
        qualified = [item for item in ranked if item["score"] >= 1.0]
        if len(qualified) == 1:
            return {
                **node,
                "state": "unique",
                "header": qualified[0]["header"],
                "score": qualified[0]["score"],
                "source": "title_qualifier",
            }
        remaining = [header for header in headers if header not in skipped]
        if len(remaining) == 1:
            return {
                **node,
                "state": "unique",
                "header": remaining[0],
                "score": title_score,
                "source": "title_only_column",
            }
    if not ranked or ranked[0]["score"] < 0.35:
        return {**node, "state": "unbound", "header": None}
    return {**node, "state": "ambiguous", "header": None}


def _select_unique_property_header(report_claim: dict[str, Any], binding: dict[str, Any]) -> str | None:
    ranked = _rank_property_headers(report_claim, binding)
    if ranked["state"] != "unique":
        return None
    return ranked["header"]


def _rank_property_headers(report_claim: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    condition_headers = {
        str(item.get("column") or "")
        for item in binding.get("matched_parameters") or []
        if isinstance(item, dict)
    }
    return _rank_property_on_table(
        report_claim,
        list(binding.get("headers") or []),
        table_title=str(binding.get("table_title") or ""),
        condition_headers=condition_headers,
    )


def _canonical_unit(unit: Any) -> str | None:
    text = str(unit or "").strip().lower().replace("µ", "u").replace("db(a)", "dba")
    return text or None


def normalize_unit_pair(left_unit: Any, right_unit: Any) -> dict[str, Any]:
    """Align two units onto one base dimension, or refuse conversion."""
    left = _canonical_unit(left_unit)
    right = _canonical_unit(right_unit)
    if left and right and left == right:
        return {
            "state": "same",
            "left_unit": left,
            "right_unit": right,
            "base": left,
            "scale_left": 1.0,
            "scale_right": 1.0,
        }
    if not left and not right:
        return {
            "state": "unitless",
            "left_unit": None,
            "right_unit": None,
            "base": None,
            "scale_left": 1.0,
            "scale_right": 1.0,
        }
    if not left or not right:
        return {
            "state": "one_sided",
            "left_unit": left,
            "right_unit": right,
            "base": left or right,
            "scale_left": 1.0,
            "scale_right": 1.0,
        }
    left_base = _UNIT_BASE.get(left)
    right_base = _UNIT_BASE.get(right)
    if not left_base or not right_base or left_base[0] != right_base[0]:
        return {
            "state": "unavailable",
            "left_unit": left,
            "right_unit": right,
            "base": None,
            "reason": "unit_conversion_unavailable",
            "scale_left": None,
            "scale_right": None,
        }
    return {
        "state": "converted",
        "left_unit": left,
        "right_unit": right,
        "base": left_base[0],
        "scale_left": left_base[1],
        "scale_right": right_base[1],
    }


def _operator_direction(operator: str) -> str | None:
    if operator in {"le", "lt"}:
        return "upper"
    if operator in {"ge", "gt"}:
        return "lower"
    if operator == "eq":
        return "exact"
    return None


def _bound_tightness(report_value: float, standard_value: float, operator: str) -> str:
    equal = math.isclose(report_value, standard_value, rel_tol=1e-9, abs_tol=1e-12)
    if operator in {"le", "lt"}:
        if operator == "le" and equal:
            return "equal"
        return "stricter" if report_value < standard_value else "looser"
    if operator in {"ge", "gt"}:
        if operator == "ge" and equal:
            return "equal"
        return "stricter" if report_value > standard_value else "looser"
    return "equal" if equal else "different"


def compare_claims(report_claim: dict[str, Any], evidence_claim: dict[str, Any]) -> dict[str, Any]:
    """Compare two already-bound claims using generic value semantics."""
    left = report_claim.get("value") or {}
    right = evidence_claim.get("value") or {}
    property_score = semantic_similarity(
        _claim_property_text(report_claim),
        _claim_property_text(evidence_claim),
    )
    if property_score < 0.35:
        return {
            "relation": "not_comparable",
            "tightness": "not_comparable",
            "reason": "property_not_bound",
            "property_similarity": property_score,
        }
    units = normalize_unit_pair(left.get("unit"), right.get("unit"))
    left_numbers, right_numbers = left.get("numbers") or [], right.get("numbers") or []
    if left.get("kind") in {"enum", "text"} or right.get("kind") in {"enum", "text"}:
        equal = _normal(left.get("normalized")) == _normal(right.get("normalized"))
        return {
            "relation": "equal" if equal else "different",
            "tightness": "equal" if equal else "different",
            "kind": "text",
            "property_similarity": property_score,
            "unit_normalize": units,
        }
    if units["state"] == "unavailable":
        return {
            "relation": "not_comparable",
            "tightness": "not_comparable",
            "reason": "unit_conversion_unavailable",
            "property_similarity": property_score,
            "unit_normalize": units,
        }
    if len(left_numbers) != 1 or len(right_numbers) != 1:
        return {
            "relation": "not_comparable",
            "tightness": "not_comparable",
            "reason": "non_scalar_or_ambiguous",
            "property_similarity": property_score,
            "unit_normalize": units,
        }
    scale_left = float(units["scale_left"] or 1.0)
    scale_right = float(units["scale_right"] or 1.0)
    report_value = float(left_numbers[0]) * scale_left
    standard_value = float(right_numbers[0]) * scale_right
    report_operator = str(left.get("operator") or "eq")
    standard_operator = str(right.get("operator") or "eq")
    report_dir = _operator_direction(report_operator)
    standard_dir = _operator_direction(standard_operator)
    if report_dir == "exact" and standard_dir in {"upper", "lower"}:
        operator = standard_operator
    elif standard_dir == "exact" and report_dir in {"upper", "lower"}:
        operator = report_operator
    elif report_dir and standard_dir and report_dir != standard_dir:
        return {
            "relation": "conflicts",
            "tightness": "different",
            "kind": "operator_conflict",
            "reason": "operator_direction_conflict",
            "report_value": report_value,
            "standard_value": standard_value,
            "report_operator": report_operator,
            "standard_operator": standard_operator,
            "property_similarity": property_score,
            "unit_normalize": units,
        }
    else:
        operator = standard_operator
    tightness = _bound_tightness(report_value, standard_value, operator)
    if operator in {"le", "lt", "ge", "gt"}:
        relation = "supports" if tightness in {"equal", "stricter"} else "conflicts"
        kind = "upper_bound" if operator in {"le", "lt"} else "lower_bound"
    else:
        relation = "equal" if tightness == "equal" else "different"
        kind = "exact"
    return {
        "relation": relation,
        "tightness": tightness,
        "kind": kind,
        "report_value": report_value,
        "standard_value": standard_value,
        "report_operator": report_operator,
        "standard_operator": operator,
        "property_similarity": property_score,
        "unit_normalize": units,
    }


def _status_for_table_comparison(item: dict[str, Any]) -> str | None:
    tightness = str(item.get("tightness") or "")
    if tightness in {"equal", "stricter"}:
        return "supported"
    if tightness in {"looser", "different"}:
        return "mismatch"
    relation = str(item.get("relation") or "")
    if relation in {"equal", "supports"}:
        return "supported"
    if relation in {"different", "conflicts"}:
        return "mismatch"
    return None


def _inherit_header_unit(claim: dict[str, Any], header: str) -> dict[str, Any]:
    value = claim.get("value") if isinstance(claim.get("value"), dict) else {}
    if value.get("unit"):
        return claim
    match = _UNIT_RE.search(header)
    if not match:
        return claim
    nested = dict(claim)
    nested["value"] = {**value, "unit": match.group(0).lower()}
    return nested


_SUM_FORMULA_RE = re.compile(
    r"([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff]*)\s*=\s*"
    r"([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff]*)\s*\+\s*"
    r"([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff]*)"
)


def _formula_aliases(value: Any) -> list[str]:
    if isinstance(value, dict):
        raw = value.get("aliases") or value.get("result_aliases") or []
    else:
        raw = value
    aliases = [str(item).strip() for item in (raw or []) if str(item).strip()]
    return list(dict.fromkeys(aliases))


def derived_sum_formulas(rules: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    """Read generic sum formulas from manual-rule payloads; ignore other rule types."""
    if isinstance(rules, dict):
        items = list(rules.get("rules") or rules.get("formulas") or [])
    elif isinstance(rules, list):
        items = rules
    else:
        items = []
    formulas: list[dict[str, Any]] = []
    for rule in items:
        if not isinstance(rule, dict):
            continue
        parsed = rule.get("formula") if isinstance(rule.get("formula"), dict) else {}
        op = str(parsed.get("op") or "").strip().lower()
        result_aliases = _formula_aliases(parsed.get("result_aliases") or parsed.get("result"))
        addends_raw = parsed.get("addends") if isinstance(parsed.get("addends"), list) else []
        addends = [
            {
                "id": str(item.get("id") or "").strip() or f"addend_{index}",
                "aliases": _formula_aliases(item),
            }
            for index, item in enumerate(addends_raw)
            if isinstance(item, dict) and _formula_aliases(item)
        ]
        if op != "sum" or len(result_aliases) < 1 or len(addends) < 2:
            match = _SUM_FORMULA_RE.search(str(rule.get("rule_text") or ""))
            if not match or str(rule.get("rule_type") or "") != "derived_numeric_formula":
                continue
            result_aliases = [match.group(1)]
            addends = [
                {"id": "left", "aliases": [match.group(2)]},
                {"id": "right", "aliases": [match.group(3)]},
            ]
        formulas.append({
            "rule_id": str(rule.get("rule_id") or ""),
            "op": "sum",
            "result_aliases": result_aliases,
            "addends": addends,
        })
    return formulas


def _alias_matches_property(property_text: str, alias: str) -> bool:
    left = _normal(property_text)
    right = _normal(alias)
    if not left or not right:
        return False
    return left == right or right in left or left in right


def _claim_alias_score(claim: dict[str, Any], aliases: list[str]) -> float:
    property_text = _claim_property_text(claim)
    return 1.0 if any(_alias_matches_property(property_text, alias) for alias in aliases) else 0.0


def _quantity_base(claim: dict[str, Any]) -> dict[str, Any] | None:
    value = claim.get("value") if isinstance(claim.get("value"), dict) else {}
    numbers = value.get("numbers") or []
    if len(numbers) != 1:
        return None
    unit = _canonical_unit(value.get("unit"))
    if not unit:
        return {"dimension": "unitless", "value": float(numbers[0]), "unit": None}
    base = _UNIT_BASE.get(unit)
    if not base:
        return None
    return {"dimension": base[0], "value": float(numbers[0]) * base[1], "unit": unit}


def _synthetic_property_claim(alias: str) -> dict[str, Any]:
    return {
        "property": {"concept": _normal(alias), "source_text": alias},
        "value": {
            "kind": "quantity",
            "raw": "",
            "normalized": "",
            "numbers": [],
            "unit": None,
            "operator": "eq",
        },
        "scope": parse_scope(""),
        "conditions": [],
        "evidence_role": None,
    }


def _unique_addend_cell(
    aliases: list[str],
    candidates: list[dict[str, Any]],
    applicability: dict[str, Any],
) -> dict[str, Any]:
    bound: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for alias in aliases:
        synthetic = _synthetic_property_claim(alias)
        for candidate in candidates:
            result = _evaluate_one_table_candidate(
                candidate,
                synthetic,
                applicability=applicability,
                compare=False,
            )
            attempts.append({"alias": alias, **result["attempt"]})
            cell = result.get("bound_cell")
            if not isinstance(cell, dict):
                continue
            header = str(cell.get("header") or "")
            if not _alias_matches_property(header, alias):
                continue
            scaled = _quantity_base(cell.get("evidence_claim") or {})
            if scaled is None:
                continue
            bound.append({**cell, "scaled": scaled, "alias": alias})
    if not bound:
        return {"state": "unbound", "attempts": attempts}
    dimensions = {item["scaled"]["dimension"] for item in bound}
    values = {round(item["scaled"]["value"], 9) for item in bound}
    if len(dimensions) > 1 or len(values) > 1:
        return {
            "state": "conflict",
            "attempts": attempts,
            "bound": bound,
        }
    return {"state": "unique", "cell": bound[0], "attempts": attempts, "bound": bound}


def evaluate_derived_sum(
    report_claim: dict[str, Any],
    candidates: list[dict[str, Any]],
    applicability: dict[str, Any],
    *,
    formulas: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Derive a standard value by summing uniquely bound addend cells."""
    if _quantity_base(report_claim) is None:
        return None
    matched = [
        formula
        for formula in (formulas or [])
        if _claim_alias_score(report_claim, list(formula.get("result_aliases") or [])) >= 1.0
        and len(formula.get("addends") or []) >= 2
    ]
    if not matched:
        return None
    nodes: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for formula in matched:
        addend_results: list[dict[str, Any]] = []
        complete = True
        for addend in formula.get("addends") or []:
            result = _unique_addend_cell(
                list(addend.get("aliases") or []),
                candidates,
                applicability,
            )
            attempts.extend(result.get("attempts") or [])
            addend_results.append({"id": addend.get("id"), **result})
            if result.get("state") != "unique":
                complete = False
        nodes.append({
            "node": "derived_sum",
            "rule_id": formula.get("rule_id"),
            "state": "ok" if complete else "incomplete",
            "addends": [
                {
                    "id": item.get("id"),
                    "state": item.get("state"),
                    "header": (item.get("cell") or {}).get("header"),
                    "candidate_key": (item.get("cell") or {}).get("candidate_key"),
                    "value": ((item.get("cell") or {}).get("scaled") or {}).get("value"),
                    "unit": ((item.get("cell") or {}).get("scaled") or {}).get("unit"),
                }
                for item in addend_results
            ],
        })
        if not complete:
            continue
        scaled_parts = [item["cell"]["scaled"] for item in addend_results]
        dimensions = {item["dimension"] for item in scaled_parts}
        if len(dimensions) != 1:
            nodes.append({
                "node": "derived_sum",
                "state": "unit_mismatch",
                "rule_id": formula.get("rule_id"),
            })
            continue
        total = sum(item["value"] for item in scaled_parts)
        dimension = next(iter(dimensions))
        report_unit = _canonical_unit(
            ((report_claim.get("value") or {}).get("unit") if isinstance(report_claim.get("value"), dict) else None)
        )
        report_base = _UNIT_BASE.get(report_unit) if report_unit else None
        if report_base and report_base[0] == dimension:
            display_total = total / report_base[1]
            evidence_unit = report_unit
        else:
            display_total = total
            evidence_unit = None if dimension == "unitless" else dimension
        evidence_claim = {
            "property": dict(report_claim.get("property") or {}),
            "value": {
                "kind": "quantity",
                "raw": str(display_total),
                "normalized": str(display_total),
                "numbers": [display_total],
                "unit": evidence_unit,
                "operator": "eq",
            },
            "scope": parse_scope(""),
            "conditions": [],
            "evidence_role": "nominal_rule",
        }
        relation = compare_claims(report_claim, evidence_claim)
        status = _status_for_table_comparison(relation)
        nodes.append({
            "node": "compare",
            "relation": relation.get("relation"),
            "tightness": relation.get("tightness"),
            "reason": relation.get("reason"),
            "unit_normalize": relation.get("unit_normalize"),
        })
        if relation.get("relation") == "not_comparable" or status is None:
            nodes.append({
                "node": "verdict",
                "state": "fallback",
                "reason": relation.get("reason") or "not_comparable",
            })
            continue
        keys = [
            str(item["cell"].get("candidate_key") or "")
            for item in addend_results
            if item["cell"].get("candidate_key")
        ]
        comparison = {
            "source": "generic_derived_sum",
            "candidate_key": keys[0] if keys else None,
            "evidence_candidate_keys": list(dict.fromkeys(keys)),
            **relation,
            "conclusion": "supports" if status == "supported" else "conflicts",
            "status": status,
            "target_column": " + ".join(
                str(item["cell"].get("header") or item.get("id") or "")
                for item in addend_results
            ),
            "addends": [
                {
                    "id": item.get("id"),
                    "header": item["cell"].get("header"),
                    "candidate_key": item["cell"].get("candidate_key"),
                    "raw_standard": item["cell"].get("raw_standard"),
                    "value": item["cell"]["scaled"]["value"],
                    "unit": item["cell"]["scaled"]["unit"],
                }
                for item in addend_results
            ],
            "derived_total": total,
            "rule_id": formula.get("rule_id"),
            "trace": {
                "report_claim": report_claim,
                "evidence_claim": evidence_claim,
                "formula": formula,
                "property_similarity": relation.get("property_similarity"),
                "unit_normalize": relation.get("unit_normalize"),
            },
        }
        decision = {
            "mode": "programmatic_formula",
            "reason_code": "derived_sum_comparable",
            "status": status,
            "candidate_key": comparison["candidate_key"],
            "authoritative_count": 1,
            "tightness": comparison.get("tightness"),
            "relation": comparison.get("relation"),
            "comparison": comparison,
        }
        nodes.append({"node": "verdict", "state": "authoritative", "status": status})
        return {
            "decision": decision,
            "comparisons": [comparison],
            "attempts": attempts,
            "nodes": nodes,
        }
    return {
        "decision": {
            "mode": "fallback_llm",
            "reason_code": "derived_sum_incomplete",
            "status": None,
            "candidate_key": None,
            "authoritative_count": 0,
        },
        "comparisons": [],
        "attempts": attempts,
        "nodes": nodes,
    }


def evaluate_table_claims(
    requirement_text: str,
    project_name: str,
    candidates: list[dict[str, Any]],
    *,
    unit: str | None = None,
    requirement_extraction: dict[str, Any] | None = None,
    applicability: dict[str, Any] | None = None,
    manual_knowledge_rules: dict[str, Any] | list[Any] | None = None,
    formulas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Match claim property to a table column first, then bind the applicable row.

    ``project_name`` is a property label for unification only. Section text is
    not authoritative here.
    """
    extraction = requirement_extraction or extract_requirement_claim(
        requirement_text,
        unit=unit,
        project_name=project_name,
    )
    report_claim = extraction["claim"]
    nodes: list[dict[str, Any]] = list(extraction.get("nodes") or [])
    if not extraction.get("program_ready"):
        decision = {
            "mode": "fallback_llm",
            "reason_code": "requirement_not_program_ready",
            "status": None,
            "candidate_key": None,
            "authoritative_count": 0,
        }
        nodes.append({"node": "select_verdict", **decision})
        path = {
            "mode": decision["mode"],
            "reason_code": decision["reason_code"],
            "status": None,
            "nodes": nodes,
            "attempts": [],
        }
        return {
            "report_claim": report_claim,
            "requirement_extraction": extraction,
            "comparisons": [],
            "decision": decision,
            "path": path,
        }
    comparisons: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    resolved_formulas = formulas or derived_sum_formulas(manual_knowledge_rules)
    skip_addend_aliases = [
        str(alias)
        for formula in resolved_formulas
        if _claim_alias_score(report_claim, list(formula.get("result_aliases") or [])) >= 1.0
        for addend in (formula.get("addends") or [])
        for alias in (addend.get("aliases") or [])
        if str(alias).strip()
    ]
    applicability_payload = applicability if isinstance(applicability, dict) else {}
    for candidate in candidates:
        attempt = _evaluate_one_table_candidate(
            candidate,
            report_claim,
            applicability=applicability_payload,
            skip_addend_aliases=skip_addend_aliases,
        )
        attempts.append(attempt["attempt"])
        if attempt.get("comparison"):
            comparisons.append(attempt["comparison"])
    decision = resolve_table_claim_decision(comparisons)
    formula_path: dict[str, Any] | None = None
    if decision.get("mode") != "programmatic_table":
        formula_path = evaluate_derived_sum(
            report_claim,
            candidates,
            applicability_payload,
            formulas=resolved_formulas,
        )
        if formula_path and formula_path["decision"].get("mode") == "programmatic_formula":
            decision = formula_path["decision"]
            comparisons = list(formula_path.get("comparisons") or [])
            attempts.extend(formula_path.get("attempts") or [])
            nodes.extend(formula_path.get("nodes") or [])
        elif formula_path:
            nodes.extend(formula_path.get("nodes") or [])
            attempts.extend(formula_path.get("attempts") or [])
    nodes.append({"node": "select_verdict", **decision})
    path = {
        "mode": decision["mode"],
        "reason_code": decision["reason_code"],
        "status": decision.get("status"),
        "nodes": nodes,
        "attempts": attempts,
    }
    return {
        "report_claim": report_claim,
        "requirement_extraction": extraction,
        "comparisons": comparisons,
        "decision": decision,
        "path": path,
    }


def _evaluate_one_table_candidate(
    candidate: dict[str, Any],
    report_claim: dict[str, Any],
    *,
    applicability: dict[str, Any],
    compare: bool = True,
    skip_addend_aliases: list[str] | None = None,
) -> dict[str, Any]:
    key = candidate.get("candidate_key")
    attempt_nodes: list[dict[str, Any]] = []
    content_type = str(
        candidate.get("content_type")
        or (candidate.get("business_metadata") or {}).get("content_type")
        or ""
    )
    if content_type != "table":
        attempt_nodes.append({"node": "table_bind", "state": "skipped_non_table"})
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    existing = candidate.get("table_row_binding") if isinstance(candidate.get("table_row_binding"), dict) else {}
    headers = list(existing.get("headers") or [])
    if not headers:
        headers, _rows = _html_table(str(candidate.get("text") or ""))
    if not headers:
        attempt_nodes.append({"node": "table_bind", "state": "absent"})
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    metadata = candidate.get("business_metadata") or {}
    condition_headers = {
        str(item.get("column") or "")
        for item in existing.get("matched_parameters") or []
        if isinstance(item, dict)
    }
    header_match = _rank_property_on_table(
        report_claim,
        headers,
        table_title=str(metadata.get("table_title") or ""),
        condition_headers=condition_headers,
    )
    attempt_nodes.append(header_match)
    header = header_match.get("header")
    if header and skip_addend_aliases and any(
        _alias_matches_property(str(header), alias) for alias in skip_addend_aliases
    ):
        attempt_nodes.append({
            "node": "table_bind",
            "state": "skipped_formula_addend",
            "header": header,
        })
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    if not header:
        attempt_nodes.append({
            "node": "table_bind",
            "state": existing.get("state") or "unbound_property",
        })
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    binding = bind_table_row(
        candidate,
        applicability,
        exclude_headers={str(header)},
    )
    if not (isinstance(binding, dict) and binding.get("state") == "matched"):
        if existing.get("state") == "matched" and str(header) in (existing.get("column_values") or {}):
            binding = existing
        else:
            attempt_nodes.append({
                "node": "table_bind",
                "state": (binding or {}).get("state") if isinstance(binding, dict) else "absent",
                "row_index": (binding or {}).get("row_index") if isinstance(binding, dict) else None,
                "candidate_row_count": (binding or {}).get("candidate_row_count") if isinstance(binding, dict) else 0,
            })
            return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    attempt_nodes.append({
        "node": "table_bind",
        "state": binding.get("state"),
        "row_index": binding.get("row_index"),
        "candidate_row_count": binding.get("candidate_row_count"),
    })
    roles = list(candidate.get("evidence_roles") or [])
    if roles and "nominal_rule" not in roles:
        attempt_nodes.append({"node": "evidence_role", "state": "rejected", "roles": roles})
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    raw_standard = str((binding.get("column_values") or {}).get(header) or "")
    evidence_claim = _inherit_header_unit(
        parse_claim(f"{header}: {raw_standard}", evidence_role="nominal_rule"),
        header,
    )
    bound_cell = {
        "candidate_key": key,
        "header": header,
        "raw_standard": raw_standard,
        "evidence_claim": evidence_claim,
        "table_row_binding": binding,
    }
    if not compare:
        attempt_nodes.append({"node": "verdict", "state": "cell_bound"})
        return {
            "attempt": {"candidate_key": key, "nodes": attempt_nodes},
            "bound_cell": bound_cell,
        }
    relation = compare_claims(report_claim, evidence_claim)
    attempt_nodes.append({
        "node": "compare",
        "relation": relation.get("relation"),
        "tightness": relation.get("tightness"),
        "reason": relation.get("reason"),
        "unit_normalize": relation.get("unit_normalize"),
    })
    status = _status_for_table_comparison(relation)
    if relation.get("relation") == "not_comparable" or status is None:
        attempt_nodes.append({
            "node": "verdict",
            "state": "fallback",
            "reason": relation.get("reason") or "not_comparable",
        })
        return {"attempt": {"candidate_key": key, "nodes": attempt_nodes}}
    comparison = {
        "source": "generic_bound_table_claim",
        "candidate_key": key,
        **relation,
        "conclusion": "supports" if status == "supported" else "conflicts",
        "status": status,
        "target_column": header,
        "table_row_binding": binding,
        "trace": {
            "report_claim": report_claim,
            "evidence_claim": evidence_claim,
            "property_similarity": relation.get("property_similarity"),
            "unit_normalize": relation.get("unit_normalize"),
            "nodes": list(attempt_nodes),
        },
    }
    attempt_nodes.append({"node": "verdict", "state": "authoritative", "status": status})
    return {
        "attempt": {"candidate_key": key, "nodes": attempt_nodes},
        "comparison": comparison,
    }


def resolve_table_claim_decision(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose one programmatic table verdict, or fall back when bindings disagree."""
    authoritative = [
        item
        for item in comparisons
        if isinstance(item, dict)
        and item.get("source") == "generic_bound_table_claim"
        and _status_for_table_comparison(item) is not None
        and isinstance(item.get("trace"), dict)
    ]
    if not authoritative:
        derived = [
            item
            for item in comparisons
            if isinstance(item, dict)
            and item.get("source") == "generic_derived_sum"
            and _status_for_table_comparison(item) is not None
        ]
        if len(derived) == 1:
            chosen = derived[0]
            status = str(chosen.get("status") or _status_for_table_comparison(chosen))
            return {
                "mode": "programmatic_formula",
                "reason_code": "derived_sum_comparable",
                "status": status,
                "candidate_key": chosen.get("candidate_key"),
                "authoritative_count": 1,
                "tightness": chosen.get("tightness"),
                "relation": chosen.get("relation"),
                "comparison": chosen,
            }
        return {
            "mode": "fallback_llm",
            "reason_code": "no_authoritative_table_claim",
            "status": None,
            "candidate_key": None,
            "authoritative_count": 0,
        }
    statuses = {str(item.get("status") or _status_for_table_comparison(item)) for item in authoritative}
    if len(statuses) > 1:
        return {
            "mode": "fallback_llm",
            "reason_code": "conflicting_table_bindings",
            "status": None,
            "candidate_key": None,
            "authoritative_count": len(authoritative),
            "statuses": sorted(statuses),
        }
    chosen = authoritative[0]
    status = str(chosen.get("status") or _status_for_table_comparison(chosen))
    return {
        "mode": "programmatic_table",
        "reason_code": "unique_bound_comparable",
        "status": status,
        "candidate_key": chosen.get("candidate_key"),
        "authoritative_count": len(authoritative),
        "tightness": chosen.get("tightness"),
        "relation": chosen.get("relation"),
        "comparison": chosen,
    }


def build_deterministic_comparisons(
    requirement_text: str,
    project_name: str,
    candidates: list[dict[str, Any]],
    *,
    unit: str | None = None,
    requirement_extraction: dict[str, Any] | None = None,
    applicability: dict[str, Any] | None = None,
    manual_knowledge_rules: dict[str, Any] | list[Any] | None = None,
    formulas: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return comparable uniquely bound table claims, including supports."""
    return evaluate_table_claims(
        requirement_text,
        project_name,
        candidates,
        unit=unit,
        requirement_extraction=requirement_extraction,
        applicability=applicability,
        manual_knowledge_rules=manual_knowledge_rules,
        formulas=formulas,
    )["comparisons"]
