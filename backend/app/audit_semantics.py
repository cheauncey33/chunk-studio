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
    r"(?i)(kv|v|mv|ka|a|ma|kva|mva|va|kw|mw|w|hz|khz|mhz|s|ms|μs|us|%|db(?:a)?)"
)
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
    unit_match = _UNIT_RE.search(raw_value)
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


def bind_table_row(candidate: dict[str, Any], applicability: dict[str, Any]) -> dict[str, Any] | None:
    """Return a binding only for one fully satisfied row.

    Partial and multiple matches remain explicit non-authoritative states.
    """
    metadata = candidate.get("business_metadata") or {}
    if str(metadata.get("content_type") or candidate.get("content_type") or "") != "table":
        return None
    headers, rows = _html_table(str(candidate.get("text") or ""))
    if not headers or not rows:
        return None
    selectors: list[tuple[int, str, float]] = []
    for index, header in enumerate(headers):
        ranked = sorted(
            (
                (max(semantic_similarity(header, alias) for alias in selector["aliases"]), selector)
                for selector in _profile_selectors(applicability)
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0.5:
            selector = ranked[0][1]
            selectors.append((index, str(selector["parameter"]), float(selector["value"])))
    if not selectors:
        return None
    matches_by_row: list[tuple[int, list[str], list[dict[str, Any]]]] = []
    for row_index, row in enumerate(rows, start=1):
        matches = [
            {"parameter": name, "target": target, "column": headers[column], "cell": row[column]}
            for column, name, target in selectors
            if column < len(row) and _cell_matches(row[column], target)
        ]
        if len(matches) == len(selectors):
            matches_by_row.append((row_index, row, matches))
    state = "matched" if len(matches_by_row) == 1 else "ambiguous" if matches_by_row else "unresolved"
    result: dict[str, Any] = {
        "state": state,
        "matched": state == "matched",
        "selector_count": len(selectors),
        "selector_matches": len(selectors) if matches_by_row else 0,
        "headers": headers,
        "candidate_row_count": len(matches_by_row),
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


def _select_unique_property_header(report_claim: dict[str, Any], binding: dict[str, Any]) -> str | None:
    condition_headers = {
        str(item.get("column") or "")
        for item in binding.get("matched_parameters") or []
        if isinstance(item, dict)
    }
    ranked = sorted(
        (
            (semantic_similarity(_claim_property_text(report_claim), header), header)
            for header in binding.get("headers") or []
            if header not in condition_headers
        ),
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.35:
        return None
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.1:
        return None
    return ranked[0][1]


def compare_claims(report_claim: dict[str, Any], evidence_claim: dict[str, Any]) -> dict[str, Any]:
    """Compare two already-bound claims using generic value semantics."""
    left = report_claim.get("value") or {}
    right = evidence_claim.get("value") or {}
    if semantic_similarity(_claim_property_text(report_claim), _claim_property_text(evidence_claim)) < 0.35:
        return {"relation": "not_comparable", "reason": "property_not_bound"}
    left_unit, right_unit = left.get("unit"), right.get("unit")
    if left_unit and right_unit and left_unit != right_unit:
        return {"relation": "not_comparable", "reason": "unit_conversion_unavailable"}
    left_numbers, right_numbers = left.get("numbers") or [], right.get("numbers") or []
    if left.get("kind") in {"enum", "text"} or right.get("kind") in {"enum", "text"}:
        relation = "equal" if _normal(left.get("normalized")) == _normal(right.get("normalized")) else "different"
        return {"relation": relation, "kind": "text"}
    if len(left_numbers) != 1 or len(right_numbers) != 1:
        return {"relation": "not_comparable", "reason": "non_scalar_or_ambiguous"}
    report_value, standard_value = float(left_numbers[0]), float(right_numbers[0])
    operator = str(right.get("operator") or "eq")
    if operator == "le":
        relation = "supports" if report_value <= standard_value else "conflicts"
    elif operator == "lt":
        relation = "supports" if report_value < standard_value else "conflicts"
    elif operator == "ge":
        relation = "supports" if report_value >= standard_value else "conflicts"
    elif operator == "gt":
        relation = "supports" if report_value > standard_value else "conflicts"
    else:
        relation = "supports" if math.isclose(abs(report_value), abs(standard_value), rel_tol=1e-9) else "conflicts"
    return {
        "relation": "equal" if relation == "supports" and operator == "eq" else "different" if relation == "conflicts" and operator == "eq" else relation,
        "kind": "exact" if operator == "eq" else "upper_bound" if operator in {"le", "lt"} else "lower_bound",
        "report_value": report_value,
        "standard_value": standard_value,
        "report_operator": str(left.get("operator") or "eq"),
        "standard_operator": operator,
    }


def build_deterministic_comparisons(
    requirement_text: str,
    project_name: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build conflicts only from uniquely bound table claims.

    ``project_name`` is ignored. Section-formula comparison is intentionally not
    authoritative until generic evidence-claim segmentation can bind one unique
    property and its applicability conditions.
    """
    del project_name
    report_claim = parse_claim(requirement_text)
    comparisons: list[dict[str, Any]] = []
    for candidate in candidates:
        binding = candidate.get("table_row_binding")
        if not isinstance(binding, dict) or binding.get("state") != "matched":
            continue
        if "nominal_rule" not in (candidate.get("evidence_roles") or []):
            continue
        header = _select_unique_property_header(report_claim, binding)
        if not header:
            continue
        raw_standard = str((binding.get("column_values") or {}).get(header) or "")
        evidence_claim = parse_claim(f"{header}: {raw_standard}", evidence_role="nominal_rule")
        relation = compare_claims(report_claim, evidence_claim)
        if relation.get("relation") != "different":
            continue
        comparisons.append({
            "source": "generic_bound_table_claim",
            "candidate_key": candidate.get("candidate_key"),
            **relation,
            "conclusion": "conflicts",
            "target_column": header,
            "table_row_binding": binding,
            "trace": {
                "report_claim": report_claim,
                "evidence_claim": evidence_claim,
                "property_similarity": semantic_similarity(
                    _claim_property_text(report_claim),
                    _claim_property_text(evidence_claim),
                ),
            },
        })
    return comparisons
